"""ИИ-проектный офис 2.20 — файл для полной замены app_v2.py.

300 микро-улучшений: интеграции, аналитика, удобство.
Запуск:
    streamlit run app_v2.py
"""

from __future__ import annotations

import os
import csv
import io
import json
import zipfile
from dotenv import load_dotenv
load_dotenv()
from datetime import datetime, timedelta, timezone
from pathlib import Path

import streamlit as st

from core.agent_profiles import ALLOWED_GROQ_MODELS, DEFAULT_GROQ_MODEL
from core.models import (
    PRIORITY_LABELS, TASK_PRIORITIES, activate_member,
    add_member_to_project, add_event, can_delete_member, create_member,
    create_project, create_task, deactivate_member,
    delete_member, ensure_user, project_members, recent_events,
    remove_member_from_project, update_member, update_project,
    update_task,
)
from cloud_storage import load_db, save_db
from core.orchestrator import run_autonomous_task
from knowledge import list_knowledge_files, save_uploaded_file


@st.cache_data(ttl=30, show_spinner=False)
def cached_knowledge_files(include_deleted: bool = False):
    """Кэш списка документов базы знаний: не читает диск при каждом клике."""
    try:
        return list_knowledge_files(include_deleted=include_deleted)
    except Exception:
        return []
from storage import create_run_dir, save_file, save_run_log

try:
    from llm import ask
except ImportError as exc:
    raise RuntimeError("Не найден llm.py с функцией ask(role, prompt, model)") from exc

st.set_page_config(page_title="ИИ-проектный офис 2.20", page_icon="🧭", layout="wide")
st.title("🧭 ИИ-проектный офис 2.20")
st.caption("Интеграции, аналитика и удобство работы.")

db = load_db()
CURRENT_USER_KEY = "local_admin"
ensure_user(db, CURRENT_USER_KEY, "Локальный администратор")
save_db(db)

if "chat_history" not in st.session_state:
    st.session_state.chat_history = []
if "chat_session" not in st.session_state:
    st.session_state.chat_session = 0
for key in ["kb_notice", "edit_member_id", "saved_view_name", "dark_theme"]:
    if key not in st.session_state:
        st.session_state[key] = None

STATUS_ICONS = {
    "todo": "⚪", "in_progress": "🔵", "changes_requested": "🟠",
    "review": "🟡", "internally_accepted": "🟢", "customer_approved": "✅",
    "escalated": "🔴", "paused": "⏸️",
}
PRIORITY_ICONS = {"low": "🟢", "medium": "🟡", "high": "🟠", "critical": "🔴"}
RECURRENCE_OPTIONS = {"Нет": None, "Ежедневно": "daily", "Еженедельно": "weekly", "Ежемесячно": "monthly"}


def format_dt(value):
    if not value:
        return "—"
    try:
        dt = datetime.fromisoformat(value)
        if dt.tzinfo:
            dt = dt.astimezone()
        return dt.strftime("%d.%m.%Y %H:%M")
    except ValueError:
        return str(value)[:16].replace("T", " ")


def status_icon(status):
    return STATUS_ICONS.get(status, "⚪")


def member_label(member):
    kind = "Человек" if member["type"] == "human" else "ИИ"
    status = "Активен" if member["status"] == "active" else "Отключён"
    model = f" · {member['model']}" if member["type"] == "ai" and member.get("model") else ""
    return f"{member['name']} · {member['role'] or 'Без роли'} · {kind} · {status}{model}"


def priority_label(priority):
    return PRIORITY_LABELS.get(priority, priority)


def priority_badge(priority):
    return f"{PRIORITY_ICONS.get(priority, '⚪')} {priority_label(priority)}"


def member_select(label, key, candidates, initial_id=None, help_text=None):
    options = ["— Не выбрано —"] + [m["id"] for m in candidates]

    def fmt(mid):
        return options[0] if mid == options[0] else member_label(db["members"][mid])

    default_index = 0
    if initial_id and initial_id in options:
        default_index = options.index(initial_id)
    value = st.selectbox(label, options, index=default_index, format_func=fmt, key=key, help=help_text or None)
    return None if value == options[0] else value


def format_llm_error(exc):
    text = str(exc)
    if "403" in text or "Forbidden" in text:
        return "Groq отклонил запрос: **403 Forbidden**. Проверьте GROQ_API_KEY и доступ к модели."
    if "401" in text or "Unauthorized" in text:
        return "Groq отклонил запрос: **401 Unauthorized**. Проверьте GROQ_API_KEY в .env."
    if "404" in text or "model" in text.lower():
        return "Groq не нашёл модель или эндпоинт. Проверьте имя модели."
    if "429" in text:
        return "Превышен лимит запросов Groq (**429**). Подождите и повторите запрос."
    return f"Ошибка запроса к Groq:\n\n```text\n{text}\n```"


def task_history(task_id):
    events = [
        e for e in db.get("events", [])
        if e.get("task_id") == task_id
        and e.get("type") in {
            "task_created", "task_updated", "task_status_changed",
            "task_priority_changed", "run_started", "review_completed",
            "changes_requested", "task_internally_accepted",
            "task_customer_approved", "task_rework_requested",
            "task_comment_added", "task_attachment_added",
            "task_template_saved", "task_created_from_template",
            "task_duplicated", "task_bulk_updated",
            "checklist_item_added", "checklist_item_toggled",
            "risk_added", "risk_updated", "decision_added",
            "task_recurrence_created", "task_auto_escalated",
            "task_bulk_priority_changed", "task_deleted",
        }
    ]
    return list(reversed(events))


def add_checklist_item(task_id, text):
    task = db["tasks"].get(task_id)
    if not task:
        raise ValueError("Задача не найдена")
    if not text.strip():
        raise ValueError("Введите текст пункта")
    items = task.setdefault("checklist", [])
    items.append({"text": text.strip(), "done": False, "created_at": datetime.now(timezone.utc).isoformat()})
    task["updated_at"] = datetime.now(timezone.utc).isoformat()
    add_event(db, "checklist_item_added", task_id=task_id)


def toggle_checklist_item(task_id, index):
    task = db["tasks"].get(task_id)
    if not task:
        raise ValueError("Задача не найдена")
    items = task.get("checklist", [])
    if index < 0 or index >= len(items):
        raise ValueError("Пункт не найден")
    items[index]["done"] = not items[index]["done"]
    task["updated_at"] = datetime.now(timezone.utc).isoformat()
    add_event(db, "checklist_item_toggled", task_id=task_id, index=index)


def add_risk(project_id, title, impact, probability, mitigation):
    project = db["projects"].get(project_id)
    if not project:
        raise ValueError("Проект не найден")
    if not title.strip():
        raise ValueError("Введите название риска")
    risks = project.setdefault("risks", [])
    risks.append({
        "title": title.strip(),
        "impact": impact,
        "probability": probability,
        "mitigation": mitigation.strip(),
        "status": "open",
        "created_at": datetime.now(timezone.utc).isoformat(),
    })
    add_event(db, "risk_added", project_id=project_id, title=title.strip())


def update_risk_status(project_id, index, new_status):
    project = db["projects"].get(project_id)
    if not project:
        raise ValueError("Проект не найден")
    risks = project.get("risks", [])
    if index < 0 or index >= len(risks):
        raise ValueError("Риск не найден")
    risks[index]["status"] = new_status
    add_event(db, "risk_updated", project_id=project_id, index=index, new_status=new_status)


def add_decision(project_id, title, description):
    project = db["projects"].get(project_id)
    if not project:
        raise ValueError("Проект не найден")
    if not title.strip():
        raise ValueError("Введите название решения")
    decisions = project.setdefault("decisions", [])
    decisions.append({
        "title": title.strip(),
        "description": description.strip(),
        "created_at": datetime.now(timezone.utc).isoformat(),
    })
    add_event(db, "decision_added", project_id=project_id, title=title.strip())


def add_comment(task_id, text):
    task = db["tasks"].get(task_id)
    if not task:
        raise ValueError("Задача не найдена")
    if len(text.strip()) > 2000:
        raise ValueError("Комментарий длиннее 2000 символов")
    comments = task.setdefault("comments", [])
    comments.append({
        "author": CURRENT_USER_KEY,
        "text": text.strip(),
        "created_at": datetime.now(timezone.utc).isoformat(),
    })
    task["updated_at"] = datetime.now(timezone.utc).isoformat()
    add_event(db, "task_comment_added", task_id=task_id, author=CURRENT_USER_KEY)


def add_attachment(task_id, name, url):
    task = db["tasks"].get(task_id)
    if not task:
        raise ValueError("Задача не найдена")
    if not url.strip().startswith(("http://", "https://")):
        raise ValueError("Ссылка должна начинаться с http:// или https://")
    attachments = task.setdefault("attachments", [])
    attachments.append({
        "name": name.strip(),
        "url": url.strip(),
        "added_at": datetime.now(timezone.utc).isoformat(),
    })
    task["updated_at"] = datetime.now(timezone.utc).isoformat()
    add_event(db, "task_attachment_added", task_id=task_id, name=name.strip())


def save_task_as_template(task_id, template_name):
    task = db["tasks"].get(task_id)
    if not task:
        raise ValueError("Задача не найдена")
    if not template_name.strip():
        raise ValueError("Введите название шаблона")
    templates = db.setdefault("templates", {})
    templates[template_name.strip()] = {
        "title": task.get("title", ""),
        "description": task.get("description", ""),
        "success_criteria": task.get("success_criteria", ""),
        "priority": task.get("priority", "medium"),
        "created_at": datetime.now(timezone.utc).isoformat(),
        "source_task_id": task_id,
    }
    add_event(db, "task_template_saved", task_id=task_id, template_name=template_name.strip())


def delete_template(template_name):
    templates = db.get("templates", {})
    if template_name not in templates:
        raise ValueError("Шаблон не найден")
    del templates[template_name]


def create_next_recurrence(task):
    if not task.get("recurrence") or not task.get("deadline"):
        return None
    try:
        dl = datetime.fromisoformat(task["deadline"])
    except ValueError:
        return None
    interval = task["recurrence"]
    if interval == "daily":
        next_dt = dl + timedelta(days=1)
    elif interval == "weekly":
        next_dt = dl + timedelta(weeks=1)
    elif interval == "monthly":
        month = dl.month + 1 if dl.month < 12 else 1
        year = dl.year if dl.month < 12 else dl.year + 1
        next_dt = dl.replace(year=year, month=month)
    else:
        return None
    new_task = create_task(db, title=task["title"], description=task["description"], created_by=task["created_by"], executor_id=task["executor_id"], reviewer_id=task.get("reviewer_id"), project_id=task.get("project_id"), success_criteria=task.get("success_criteria", ""), deadline=next_dt.isoformat(), priority=task.get("priority", "medium"))
    new_task["recurrence"] = interval
    add_event(db, "task_recurrence_created", task_id=new_task["id"], source_task_id=task["id"])
    return new_task


def check_overdue_and_escalate(days_threshold=3):
    now = datetime.now()
    escalated = []
    for t in db["tasks"].values():
        if t.get("deadline") and t["status"] not in {"customer_approved", "escalated", "paused"}:
            try:
                dt = datetime.fromisoformat(t["deadline"])
                if dt < now - timedelta(days=days_threshold):
                    t["status"] = "escalated"
                    t["updated_at"] = now.isoformat()
                    add_event(db, "task_auto_escalated", task_id=t["id"], days_overdue=days_threshold)
                    escalated.append(t["id"])
            except ValueError:
                pass
    return escalated


def bulk_update_priority(task_ids, new_priority):
    if not task_ids:
        raise ValueError("Не выбраны задачи")
    if new_priority not in TASK_PRIORITIES:
        raise ValueError("Неизвестный приоритет")
    for tid in task_ids:
        task = db["tasks"].get(tid)
        if not task:
            continue
        task["priority"] = new_priority
        task["updated_at"] = datetime.now(timezone.utc).isoformat()
        add_event(db, "task_bulk_priority_changed", task_id=tid, new_priority=new_priority)


def bulk_update_status(task_ids, new_status):
    if not task_ids:
        raise ValueError("Не выбраны задачи")
    if new_status not in STATUS_ICONS:
        raise ValueError("Неизвестный статус")
    for tid in task_ids:
        task = db["tasks"].get(tid)
        if not task:
            continue
        task["status"] = new_status
        task["updated_at"] = datetime.now(timezone.utc).isoformat()
        add_event(db, "task_bulk_updated", task_id=tid, new_status=new_status)


def bulk_delete_tasks(task_ids):
    if not task_ids:
        raise ValueError("Не выбраны задачи")
    for tid in task_ids:
        if tid in db["tasks"]:
            add_event(db, "task_deleted", task_id=tid)
            del db["tasks"][tid]


def export_tasks_csv(tasks):
    buffer = io.StringIO()
    writer = csv.writer(buffer)
    writer.writerow(["ID", "Название", "Статус", "Приоритет", "Проект", "Исполнитель", "Проверяющий", "Дедлайн", "Комментарии", "Вложения", "Чек-лист", "Повторение", "SLA (дней)"])
    for t in tasks:
        project = db["projects"].get(t.get("project_id"), {})
        executor = db["members"].get(t.get("executor_id"), {})
        reviewer = db["members"].get(t.get("reviewer_id"), {})
        checklist = t.get("checklist", [])
        done = len([i for i in checklist if i.get("done")])
        writer.writerow([
            t["id"], t["title"], t["status"], priority_label(t.get("priority", "medium")),
            project.get("name", ""), executor.get("name", ""), reviewer.get("name", ""),
            format_dt(t.get("deadline")), len(t.get("comments", [])), len(t.get("attachments", [])),
            f"{done}/{len(checklist)}", t.get("recurrence") or "", project.get("sla_days", ""),
        ])
    return buffer.getvalue()


def export_tasks_ics(tasks):
    lines = ["BEGIN:VCALENDAR", "VERSION:2.0", "PRODID:-//AI Project Office//RU"]
    for t in tasks:
        if not t.get("deadline"):
            continue
        try:
            dt = datetime.fromisoformat(t["deadline"])
        except ValueError:
            continue
        dt_utc = dt.astimezone(timezone.utc) if dt.tzinfo else dt.replace(tzinfo=timezone.utc)
        lines += [
            "BEGIN:VEVENT",
            f"UID:{t['id']}@ai-project-office",
            f"DTSTAMP:{datetime.now(timezone.utc).strftime('%Y%m%dT%H%M%SZ')}",
            f"DTSTART:{dt_utc.strftime('%Y%m%dT%H%M%SZ')}",
            f"SUMMARY:{t['title']}",
            f"DESCRIPTION:{t['description'][:200]}",
            "END:VEVENT",
        ]
    lines.append("END:VCALENDAR")
    return "\r\n".join(lines)


def export_project_json(project_id):
    project = db["projects"].get(project_id, {})
    tasks = [t for t in db["tasks"].values() if t["project_id"] == project_id]
    return json.dumps({"project": project, "tasks": tasks}, ensure_ascii=False, indent=2, default=str)


def compute_kpi(tasks):
    total = len(tasks)
    done = len([t for t in tasks if t["status"] in {"internally_accepted", "customer_approved"}])
    overdue = 0
    now = datetime.now()
    for t in tasks:
        if t.get("deadline") and t["status"] not in {"customer_approved", "escalated"}:
            try:
                if datetime.fromisoformat(t["deadline"]) < now:
                    overdue += 1
            except ValueError:
                pass
    done_tasks_count = len([t for t in tasks if t["status"] in {"internally_accepted", "customer_approved"}])
    on_time = int((total - overdue) / total * 100) if total else None
    if not done_tasks_count:
        on_time = "—"
    return {"total": total, "done": done, "overdue": overdue, "on_time_pct": on_time}


def forecast_completion(tasks):
    done_tasks = [t for t in tasks if t["status"] in {"internally_accepted", "customer_approved"} and t.get("created_at") and t.get("updated_at")]
    if not done_tasks:
        return None
    durations = []
    for t in done_tasks:
        try:
            start = datetime.fromisoformat(t["created_at"])
            end = datetime.fromisoformat(t["updated_at"])
            durations.append((end - start).total_seconds())
        except ValueError:
            continue
    if not durations:
        return None
    avg_seconds = sum(durations) / len(durations)
    remaining = len([t for t in tasks if t["status"] not in {"internally_accepted", "customer_approved", "escalated"}])
    return datetime.now() + timedelta(seconds=avg_seconds * remaining)


def load_heatmap(tasks):
    load = {}
    for t in tasks:
        eid = t.get("executor_id")
        if eid:
            load[eid] = load.get(eid, 0) + 1
    return load


def export_task_markdown(task):
    project = db["projects"].get(task.get("project_id"), {})
    creator = db["members"].get(task.get("created_by"), {})
    executor = db["members"].get(task.get("executor_id"), {})
    reviewer = db["members"].get(task.get("reviewer_id"), {})
    artifact = db["artifacts"].get(task.get("current_artifact_id"), {})

    lines = [
        f"# {task['title']}", "",
        f"**Проект:** {project.get('name', '—')}",
        f"**Статус:** {task['status']}",
        f"**Приоритет:** {priority_label(task.get('priority', 'medium'))}",
        f"**Дедлайн:** {format_dt(task.get('deadline'))}",
        f"**Повторение:** {task.get('recurrence') or 'Нет'}",
        "",
        "## Описание", "", task["description"], "",
        "## Критерии готовности", "", task["success_criteria"] or "Не указаны.", "",
    ]
    checklist = task.get("checklist", [])
    if checklist:
        lines += ["## Чек-лист", ""]
        for i in checklist:
            mark = "x" if i.get("done") else " "
            lines.append(f"- [{mark}] {i.get('text', '')}")
        lines.append("")
    attachments = task.get("attachments", [])
    if attachments:
        lines += ["## Вложения", ""]
        for a in attachments:
            lines.append(f"- [{a.get('name', '')}]({a.get('url', '')})")
        lines.append("")
    if artifact:
        lines += [f"## Результат (v{artifact['version']})", "", artifact["content"], ""]
    comments = task.get("comments", [])
    if comments:
        lines += ["## Комментарии", ""]
        for c in comments:
            lines.append(f"- {format_dt(c.get('created_at'))} — **{c.get('author', '')}**: {c.get('text', '')}")
        lines.append("")
    lines += ["## История изменений", ""]
    for item in task_history(task["id"]):
        lines.append(f"- {format_dt(item.get('created_at'))} — {item.get('type', 'Событие')}")
    return "\n".join(lines)


def export_task_txt(task):
    return export_task_markdown(task)


def export_project_markdown(project_id):
    project = db["projects"].get(project_id, {})
    tasks = [t for t in db["tasks"].values() if t["project_id"] == project_id]
    lines = [f"# Проект: {project.get('name', '—')}", "", f"**Цель:** {project.get('goal', '—')}", ""]
    risks = project.get("risks", [])
    if risks:
        lines += ["## Риски", ""]
        for r in risks:
            lines.append(f"- **{r.get('title', '')}** — влияние: {r.get('impact', '')}, вероятность: {r.get('probability', '')}, статус: {r.get('status', '')}")
        lines.append("")
    decisions = project.get("decisions", [])
    if decisions:
        lines += ["## Решения", ""]
        for d in decisions:
            lines.append(f"- **{d.get('title', '')}** — {d.get('description', '')}")
        lines.append("")
    for task in tasks:
        lines.append("---")
        lines.append(export_task_markdown(task))
    return "\n".join(lines)


def export_project_txt(project_id):
    return export_project_markdown(project_id)


def export_comments_markdown(task):
    comments = task.get("comments", [])
    lines = [f"# Комментарии: {task['title']}", ""]
    if not comments:
        lines.append("Комментариев нет.")
    for c in comments:
        lines.append(f"- {format_dt(c.get('created_at'))} — **{c.get('author', '')}**: {c.get('text', '')}")
    return "\n".join(lines)


def duplicate_project(project_id):
    project = db["projects"].get(project_id)
    if not project:
        raise ValueError("Проект не найден")
    new_project = create_project(db, name=f"{project['name']} (копия)", goal=project["goal"], desired_result=project.get("desired_result", ""), constraints=project.get("constraints", ""), success_criteria=project.get("success_criteria", ""))
    for link in project_members(db, project_id):
        add_member_to_project(db, project_id=new_project["id"], member_id=link["member_id"], project_role=link["project_role"])
    add_event(db, "project_duplicated", project_id=new_project["id"], source_project_id=project_id)
    return new_project

@st.dialog("Новая задача", width="large")
def new_task_dialog(preselected_project=None):
    templates = db.get("templates", {})
    template_name = st.selectbox("Создать из шаблона", ["— Без шаблона —"] + list(templates.keys()), key="new_task_template")
    use_project = st.toggle("Связать с проектом", value=True, key="new_task_use_project")
    project_id = None
    if use_project and db["projects"]:
        options = list(db["projects"].keys())
        idx = options.index(preselected_project) if preselected_project in options else 0
        project_id = st.selectbox("Проект", options, index=idx, format_func=lambda pid: db["projects"][pid]["name"], key="new_task_project")

    default_title = ""
    default_description = ""
    default_criteria = "1. Результат отвечает на задачу.\n2. Нет неподтверждённых утверждений.\n3. Указаны необходимые следующие шаги."
    default_priority = "medium"
    if template_name != "— Без шаблона —":
        tpl = templates[template_name]
        default_title = tpl.get("title", "")
        default_description = tpl.get("description", "")
        default_criteria = tpl.get("success_criteria", default_criteria)
        default_priority = tpl.get("priority", "medium")

    task_title = st.text_input("Название задачи *", value=default_title, key="new_task_title")
    task_description = st.text_area("Описание задачи *", value=default_description, height=180, key="new_task_description")
    task_criteria = st.text_area("Критерии готовности", value=default_criteria, height=120, key="new_task_criteria")
    pidx = list(TASK_PRIORITIES).index(default_priority) if default_priority in TASK_PRIORITIES else 1
    priority = st.selectbox("Приоритет", list(TASK_PRIORITIES), index=pidx, format_func=priority_label, key="new_task_priority")
    deadline = st.datetime_input("Дедлайн", value=None, key="new_task_deadline")
    recurrence_label = st.selectbox("Повторение", list(RECURRENCE_OPTIONS.keys()), key="new_task_recurrence")
    reminder_days = st.number_input("Напомнить за (дней до дедлайна)", min_value=0, max_value=30, value=1, key="new_task_reminder")

    active_members = [m for m in db["members"].values() if m["status"] == "active"]
    if not active_members:
        st.warning("Нет активных участников.")
        return
    creator_id = member_select("Постановщик", "new_task_creator", active_members, initial_id=active_members[0]["id"])
    executor_candidates = active_members
    if project_id:
        ids = {l["member_id"] for l in project_members(db, project_id)}
        executor_candidates = [m for m in active_members if m["id"] in ids]
    if not executor_candidates:
        st.warning("Нет доступных исполнителей.")
        return
    executor_id = member_select("Исполнитель", "new_task_executor", executor_candidates)
    reviewer_candidates = [m for m in executor_candidates if m["id"] != st.session_state.get("new_task_executor")]
    reviewer_id = member_select("Проверяющий", "new_task_reviewer", reviewer_candidates)

    c1, c2 = st.columns(2)
    if c1.button("Создать задачу", type="primary", use_container_width=True, key="create_task_btn"):
        if not task_title.strip() or not task_description.strip():
            st.error("Заполните название и описание задачи.")
        elif use_project and not project_id:
            st.error("Выберите проект или отключите связь с проектом.")
        elif not creator_id or not executor_id:
            st.error("Выберите постановщика и исполнителя.")
        else:
            try:
                task = create_task(db, title=task_title, description=task_description, created_by=creator_id, executor_id=executor_id, reviewer_id=reviewer_id, project_id=project_id, success_criteria=task_criteria, deadline=deadline.isoformat() if deadline else None, priority=priority)
                task["recurrence"] = RECURRENCE_OPTIONS[recurrence_label]
                task["reminder_days"] = reminder_days
                if template_name != "— Без шаблона —":
                    add_event(db, "task_created_from_template", task_id=task["id"], template_name=template_name)
                save_db(db)
                for key in ["new_task_title", "new_task_description", "new_task_criteria", "new_task_creator", "new_task_executor", "new_task_reviewer", "new_task_deadline"]:
                    st.session_state.pop(key, None)
                st.rerun()
            except ValueError as exc:
                st.error(str(exc))
    if c2.button("Отмена", use_container_width=True, key="cancel_new_task_btn"):
        st.rerun()


@st.dialog("Редактировать задачу", width="large")
def edit_task_dialog(task_id):
    task = db["tasks"].get(task_id)
    if not task:
        st.error("Задача не найдена.")
        return
    use_project = st.toggle("Связать с проектом", value=bool(task.get("project_id")), key=f"use_project_{task_id}")
    project_id = None
    if use_project and db["projects"]:
        project_id = st.selectbox("Проект", list(db["projects"].keys()), index=list(db["projects"].keys()).index(task["project_id"]) if task.get("project_id") in db["projects"] else 0, format_func=lambda pid: db["projects"][pid]["name"], key=f"project_{task_id}")
    task_title = st.text_input("Название задачи *", value=task["title"], key=f"title_{task_id}")
    task_description = st.text_area("Описание задачи *", value=task["description"], height=180, key=f"desc_{task_id}")
    task_criteria = st.text_area("Критерии готовности", value=task["success_criteria"], height=120, key=f"criteria_{task_id}")
    priority = st.selectbox("Приоритет", list(TASK_PRIORITIES), index=list(TASK_PRIORITIES).index(task.get("priority", "medium")), format_func=priority_label, key=f"priority_{task_id}")
    initial_deadline = None
    if task.get("deadline"):
        try:
            initial_deadline = datetime.fromisoformat(task["deadline"])
        except ValueError:
            initial_deadline = None
    deadline = st.datetime_input("Дедлайн", value=initial_deadline, key=f"deadline_{task_id}")
    current_rec = task.get("recurrence") or "Нет"
    rec_label = next((k for k, v in RECURRENCE_OPTIONS.items() if v == current_rec), "Нет")
    recurrence_label = st.selectbox("Повторение", list(RECURRENCE_OPTIONS.keys()), index=list(RECURRENCE_OPTIONS.keys()).index(rec_label), key=f"recurrence_{task_id}")
    reminder_days = st.number_input("Напомнить за (дней до дедлайна)", min_value=0, max_value=30, value=task.get("reminder_days", 1), key=f"reminder_{task_id}")

    active_members = [m for m in db["members"].values() if m["status"] == "active"]
    creator_id = member_select("Постановщик", f"creator_{task_id}", active_members, initial_id=task.get("created_by"))
    executor_candidates = active_members
    if project_id:
        ids = {l["member_id"] for l in project_members(db, project_id)}
        executor_candidates = [m for m in active_members if m["id"] in ids]
    executor_id = member_select("Исполнитель", f"executor_{task_id}", executor_candidates, initial_id=task.get("executor_id"))
    reviewer_candidates = [m for m in executor_candidates if m["id"] != st.session_state.get(f"executor_{task_id}")]
    reviewer_id = member_select("Проверяющий", f"reviewer_{task_id}", reviewer_candidates, initial_id=task.get("reviewer_id"))

    c1, c2 = st.columns(2)
    if c1.button("💾 Сохранить", type="primary", use_container_width=True, key=f"save_task_{task_id}"):
        try:
            update_task(db, task_id, title=task_title, description=task_description, success_criteria=task_criteria, deadline=deadline.isoformat() if deadline else None, created_by=creator_id, executor_id=executor_id, reviewer_id=reviewer_id, project_id=project_id if use_project else None, priority=priority)
            task["recurrence"] = RECURRENCE_OPTIONS[recurrence_label]
            task["reminder_days"] = int(reminder_days)
            save_db(db)
            st.rerun()
        except ValueError as exc:
            st.error(str(exc))
    if c2.button("Отмена", use_container_width=True, key=f"cancel_task_{task_id}"):
        st.rerun()


@st.dialog("Добавить участника в проект", width="large")
def add_project_member_dialog(project_id):
    current_ids = {l["member_id"] for l in project_members(db, project_id)}
    available = [m for m in db["members"].values() if m["status"] == "active" and m["id"] not in current_ids]
    if not available:
        st.info("Все активные участники уже добавлены в проект.")
    else:
        member_id = st.selectbox("Участник", [m["id"] for m in available], format_func=lambda mid: member_label(db["members"][mid]), key=f"select_member_{project_id}")
        project_role = st.selectbox("Роль в проекте", ["Заказчик", "Руководитель проекта", "Исполнитель", "Проверяющий", "Наблюдатель"], key=f"select_role_{project_id}")
        if st.button("Добавить", type="primary", use_container_width=True, key=f"add_member_{project_id}"):
            add_member_to_project(db, project_id=project_id, member_id=member_id, project_role=project_role)
            save_db(db)
            st.rerun()
    if st.button("Закрыть", use_container_width=True, key=f"close_member_{project_id}"):
        st.rerun()


@st.dialog("Редактировать проект", width="large")
def edit_project_dialog(project_id):
    project = db["projects"].get(project_id)
    if not project:
        st.error("Проект не найден.")
        return
    name = st.text_input("Название", value=project["name"], key=f"project_name_{project_id}")
    goal = st.text_area("Цель", value=project["goal"], height=100, key=f"project_goal_{project_id}")
    desired_result = st.text_area("Ожидаемый результат", value=project.get("desired_result", ""), height=90, key=f"project_result_{project_id}")
    constraints = st.text_area("Ограничения", value=project.get("constraints", ""), height=90, key=f"project_constraints_{project_id}")
    success_criteria = st.text_area("Критерии успеха", value=project.get("success_criteria", ""), height=90, key=f"project_criteria_{project_id}")
    sla_days = st.number_input("SLA (дней на задачу)", min_value=0, max_value=365, value=project.get("sla_days", 0), key=f"sla_{project_id}")
    webhook_url = st.text_input("Webhook URL (для уведомлений)", value=project.get("webhook_url", ""), key=f"webhook_{project_id}")
    email_notify = st.toggle("Email-уведомления", value=project.get("email_notify", False), key=f"email_{project_id}")
    c1, c2 = st.columns(2)
    if c1.button("💾 Сохранить", type="primary", use_container_width=True, key=f"save_project_{project_id}"):
        update_project(db, project_id, name=name, goal=goal, desired_result=desired_result, constraints=constraints, success_criteria=success_criteria)
        project["sla_days"] = int(sla_days)
        project["webhook_url"] = webhook_url.strip()
        project["email_notify"] = email_notify
        save_db(db)
        st.rerun()
    if c2.button("Отмена", use_container_width=True, key=f"cancel_project_{project_id}"):
        st.rerun()


@st.cache_data(ttl=60, show_spinner=False)
def groq_ping(model: str):
    """Проверка подключения к Groq: ключ и доступность модели (кэш 60 сек)."""
    import requests
    key = os.getenv("GROQ_API_KEY", "")
    if not key:
        return False, "GROQ_API_KEY не задан в .env"
    try:
        r = requests.post(
            "https://api.groq.com/openai/v1/chat/completions",
            headers={"Authorization": f"Bearer {key}"},
            json={"model": model, "messages": [{"role": "user", "content": "ping"}], "max_tokens": 5},
            timeout=10,
        )
        if r.status_code == 200:
            return True, "подключено"
        detail = r.text[:120].replace("\n", " ")
        return False, f"ошибка {r.status_code}: {detail}"
    except Exception as exc:
        return False, str(exc)[:120]


def show_groq_status():
    """Индикатор подключения к Groq в сайдбаре: проверяет все модели из списка."""
    with st.sidebar:
        with st.expander("🟢 Статус Groq", expanded=False):
            for model in ALLOWED_GROQ_MODELS:
                ok, msg = groq_ping(model)
                if ok:
                    st.success(f"🟢 {model} — {msg}")
                else:
                    st.error(f"🔴 {model} — {msg}")
            if st.button("🔄 Проверить снова", key="groq_recheck", use_container_width=True):
                groq_ping.clear()
                st.rerun()


def global_search():
    """Глобальный поиск по проектам, задачам, участникам и базе знаний."""
    with st.sidebar:
        with st.expander("🔍 Глобальный поиск", expanded=False):
            query = st.text_input("Что искать?", key="global_search_query", placeholder="Проект, задача, участник, документ...")
            if not query or len(query.strip()) < 2:
                st.caption("Введите минимум 2 символа.")
                return
            q = query.strip().lower()
            projects = [p for p in db["projects"].values() if q in p.get("name", "").lower() or q in p.get("goal", "").lower()]
            tasks = [t for t in db["tasks"].values() if q in t.get("title", "").lower() or q in t.get("description", "").lower()]
            members = [m for m in db["members"].values() if q in m.get("name", "").lower() or q in m.get("role", "").lower()]
            try:
                docs = [d for d in cached_knowledge_files(include_deleted=False) if q in d.get("name", "").lower() or q in d.get("content", "").lower()]
            except Exception:
                docs = []
            st.write(f"**Проекты:** {len(projects)} | **Задачи:** {len(tasks)} | **Участники:** {len(members)} | **Документы:** {len(docs)}")
            if projects:
                st.markdown("**📁 Проекты**")
                for p in projects[:5]:
                    st.write(f"• {p.get('name', p['id'])}")
            if tasks:
                st.markdown("**✅ Задачи**")
                for t in tasks[:5]:
                    st.write(f"• {t.get('title', t['id'])} — {t.get('status', '')}")
            if members:
                st.markdown("**👥 Участники**")
                for m in members[:5]:
                    st.write(f"• {m.get('name', m['id'])} — {m.get('role', '')}")
            if docs:
                st.markdown("**📄 Документы**")
                for d in docs[:5]:
                    st.write(f"• {d.get('name', '')}")
            if not (projects or tasks or members or docs):
                st.info("Ничего не найдено.")


def show_onboarding():
    """Подсказки для первого запуска."""
    if st.session_state.get("onboarding_done"):
        return
    with st.expander("🚀 Быстрый старт — как пользоваться системой", expanded=True):
        st.markdown(
            "1. **Команда** — добавьте участников и их роли.\n"
            "2. **Проекты** — создайте проект, укажите цель и критерии успеха.\n"
            "3. **Задачи** — заведите задачи, назначьте исполнителей и сроки.\n"
            "4. **База знаний** — загрузите документы (txt/md), чтобы ИИ мог их использовать.\n"
            "5. **ИИ-чат** — задавайте вопросы по проектам и задачам.\n\n"
            "Данные хранятся в Supabase и доступны с любого устройства."
        )
        if st.button("Понятно, не показывать больше", key="onboarding_dismiss"):
            st.session_state.onboarding_done = True
            st.rerun()


def show_notifications():
    """Уведомления: просроченные и скоро истекающие задачи."""
    now = datetime.now(timezone.utc)
    overdue, soon = [], []
    for t in db["tasks"].values():
        if t.get("status") in {"customer_approved", "cancelled"}:
            continue
        deadline = t.get("deadline")
        if not deadline:
            continue
        try:
            dl = datetime.fromisoformat(deadline)
        except Exception:
            continue
        if dl.tzinfo is None:
            dl = dl.replace(tzinfo=timezone.utc)
        if dl < now:
            overdue.append(t)
        elif (dl - now).days <= 3:
            soon.append(t)
    if overdue:
        st.error(f"⏰ Просрочено задач: {len(overdue)}. Откройте вкладку «Задачи» и обновите статусы.")
    if soon:
        st.warning(f"📅 В ближайшие 3 дня дедлайн у {len(soon)} задач(и).")


def start_new_chat():
    st.session_state.chat_history = []
    st.session_state.chat_session += 1
    st.session_state.pop("chat_input", None)

show_groq_status()

global_search()

tabs = st.tabs([
    "Обзор", "Команда", "Проекты", "Задачи",
    "База знаний", "История запусков", "ИИ-чат", "Активность", "История версий",
])

with tabs[0]:
    st.subheader("Обзор")
    dark_toggle = st.toggle("🌙 Тёмная тема", value=bool(st.session_state.dark_theme), key="dark_theme_toggle")
    st.session_state.dark_theme = dark_toggle
    if dark_toggle:
        st.markdown("<style>.stApp{background-color:#0e1117;color:#fafafa;color-scheme:dark}.stSidebar{background-color:#262730}h1,h2,h3,h4,h5,h6,p,span,label,li{color:#fafafa !important}.stMetric{background-color:#262730;border-radius:8px;padding:12px}</style>", unsafe_allow_html=True)
    else:
        st.markdown("<style>.stApp{background-color:#ffffff;color:#31333f;color-scheme:light}.stSidebar{background-color:#f0f2f6}h1,h2,h3,h4,h5,h6,p,span,label,li{color:#31333f !important}.stMetric{background-color:#f0f2f6;border-radius:8px;padding:12px}</style>", unsafe_allow_html=True)
    show_onboarding()
    show_notifications()
    active_projects = [p for p in db["projects"].values() if p["status"] == "active"]
    todo_tasks = [t for t in db["tasks"].values() if t["status"] in {"todo", "changes_requested"}]
    review_tasks = [t for t in db["tasks"].values() if t["status"] == "review"]
    accepted = [t for t in db["tasks"].values() if t["status"] == "internally_accepted"]
    approved = [t for t in db["tasks"].values() if t["status"] == "customer_approved"]
    escalated = [t for t in db["tasks"].values() if t["status"] == "escalated"]

    c1, c2, c3, c4 = st.columns(4)
    c1.metric("Активные проекты", len(active_projects))
    c2.metric("Задачи в работе", len(todo_tasks))
    c3.metric("На проверке", len(review_tasks))
    c4.metric("Согласовано заказчиком", len(approved))

    if escalated:
        st.error(f"Задач требуют вашего внимания: {len(escalated)}.")

    st.divider()
    st.subheader("KPI и прогноз")
    kpi = compute_kpi(list(db["tasks"].values()))
    k1, k2, k3, k4 = st.columns(4)
    k1.metric("Всего задач", kpi["total"])
    k2.metric("Выполнено", kpi["done"])
    k3.metric("Просрочено", kpi["overdue"])
    k4.metric("В срок, %", kpi["on_time_pct"])
    forecast = forecast_completion(list(db["tasks"].values()))
    if forecast:
        st.info(f"Прогноз завершения всех задач: {forecast.strftime('%d.%m.%Y %H:%M')}")
    else:
        st.caption("Прогноз появится после завершения минимум 3 задач с указанными датами.")

    st.divider()
    st.subheader("Загрузка команды")
    heatmap = load_heatmap(list(db["tasks"].values()))
    if not heatmap:
        st.caption("Нет данных о загрузке.")
    for mid, count in sorted(heatmap.items(), key=lambda x: x[1], reverse=True):
        member = db["members"].get(mid, {})
        st.write(f"• **{member.get('name', mid)}** — задач: {count}")
        st.progress(min(count * 10, 100))

    st.divider()
    st.subheader("Напоминания")
    now = datetime.now()
    reminders = []
    for t in db["tasks"].values():
        if t.get("deadline") and t.get("reminder_days") is not None and t["status"] not in {"customer_approved", "escalated", "paused"}:
            try:
                dt = datetime.fromisoformat(t["deadline"])
                remind_at = dt - timedelta(days=t.get("reminder_days", 1))
                if now <= remind_at <= now + timedelta(days=7):
                    reminders.append((remind_at, t))
            except ValueError:
                pass
    reminders.sort(key=lambda x: x[0])
    if not reminders:
        st.caption("Напоминаний в ближайшую неделю нет.")
    for remind_at, t in reminders[:5]:
        st.write(f"• **{t['title']}** — напомнить {remind_at.strftime('%d.%m.%Y %H:%M')} · дедлайн {format_dt(t.get('deadline'))}")

    st.divider()
    st.subheader("Открытые риски")
    open_risks = []
    for p in db["projects"].values():
        for r in p.get("risks", []):
            if r.get("status") == "open":
                open_risks.append((p["name"], r))
    if not open_risks:
        st.caption("Открытых рисков нет.")
    for pname, r in open_risks[:5]:
        st.write(f"• **{pname}** — {r.get('title', '')} · влияние: {r.get('impact', '')} · вероятность: {r.get('probability', '')}")

    st.divider()
    st.subheader("Статистика по приоритетам")
    prio_counts = {p: 0 for p in TASK_PRIORITIES}
    for t in db["tasks"].values():
        prio = t.get("priority", "medium")
        prio_counts[prio] = prio_counts.get(prio, 0) + 1
    st.write(" · ".join(f"{priority_badge(p)}: {c}" for p, c in prio_counts.items()))

    st.divider()
    st.subheader("Статистика по статусам")
    status_counts = {}
    for t in db["tasks"].values():
        status_counts[t["status"]] = status_counts.get(t["status"], 0) + 1
    st.write(" · ".join(f"{status_icon(s)} {s}: {c}" for s, c in status_counts.items()))

    st.divider()
    st.subheader("Прогресс по проектам")
    for project in db["projects"].values():
        ptasks = [t for t in db["tasks"].values() if t["project_id"] == project["id"]]
        total = len(ptasks)
        done = len([t for t in ptasks if t["status"] in {"internally_accepted", "customer_approved"}])
        progress = int(done / total * 100) if total else 0
        st.write(f"**{project['name']}** — задач: {total} · выполнено: {done} ({progress}%)")
        st.progress(progress)

    st.divider()
    st.subheader("Ближайшие дедлайны")
    upcoming = []
    for t in db["tasks"].values():
        if t.get("deadline") and t["status"] not in {"customer_approved", "escalated"}:
            try:
                dt = datetime.fromisoformat(t["deadline"])
                if dt >= now:
                    upcoming.append((dt, t))
            except ValueError:
                pass
    upcoming.sort(key=lambda x: x[0])
    if not upcoming:
        st.caption("Ближайших дедлайнов нет.")
    for dt, t in upcoming[:5]:
        st.write(f"• **{t['title']}** — {dt.strftime('%d.%m.%Y %H:%M')} · {t['status']}")

    st.divider()
    st.subheader("Последние события")
    events = recent_events(db, limit=5)
    if not events:
        st.info("Событий пока нет.")
    EVENT_LABELS = {
        "ai": "ИИ-агент выполнил задачу",
        "project_member_added": "Добавлен участник в проект",
        "project_created": "Создан проект",
        "task_created": "Создана задача",
        "task_completed": "Задача завершена",
        "project_duplicated": "Создана копия проекта",
    }
    for item in events:
        label = EVENT_LABELS.get(item.get("type", ""), item.get("type", "Событие"))
        when = format_dt(item.get("created_at")).replace(" ", "\u00a0")
        st.markdown(f"• **{label}** — {when}")

with tabs[1]:
    col_title, col_action = st.columns([4, 1])
    col_title.subheader("Команда")
    team_search = st.text_input("🔍 Поиск по команде", placeholder="Имя или роль", key="team_search")
    with col_action.popover("＋ Добавить участника", use_container_width=True):
        member_type = st.radio("Тип участника", ["Человек", "ИИ-агент"], horizontal=True, key="member_type")
        name = st.text_input("Имя *", key="member_name")
        role = st.text_input("Роль *", key="member_role")
        if member_type == "Человек":
            user_key = st.selectbox("Учётная запись", list(db["users"].keys()), format_func=lambda k: db["users"][k]["display_name"], key="member_user")
            model = instructions = None
        else:
            user_key = None
            model = st.selectbox("Модель Groq", ALLOWED_GROQ_MODELS, key="member_model")
            instructions = st.text_area("Инструкции", value="Готовь результат строго по задаче и критериям.", height=120, key="member_instructions")
        if st.button("Создать участника", type="primary", use_container_width=True, key="create_member_btn"):
            if not name.strip() or not role.strip():
                st.error("Заполните имя и роль.")
            else:
                create_member(db, name=name, member_type="human" if member_type == "Человек" else "ai", role=role, user_key=user_key, model=model, instructions=instructions)
                save_db(db)
                st.rerun()
    members_list = list(db["members"].values())
    if team_search.strip():
        q = team_search.strip().lower()
        members_list = [m for m in members_list if q in m["name"].lower() or q in (m.get("role") or "").lower()]
    if not members_list:
        st.info("Участников не найдено.")
    for member in members_list:
        with st.expander(member_label(member)):
            c1, c2, c3 = st.columns(3)
            if c1.button("✏️ Редактировать", key=f"edit_{member['id']}"):
                st.session_state.edit_member_id = member["id"]
                st.rerun()
            if member["status"] == "active":
                if c2.button("🚫 Отключить", key=f"off_{member['id']}"):
                    deactivate_member(db, member["id"]); save_db(db); st.rerun()
            else:
                if c2.button("✅ Включить", key=f"on_{member['id']}"):
                    activate_member(db, member["id"]); save_db(db); st.rerun()
            allowed, reason = can_delete_member(db, member["id"])
            if c3.button("🗑️ Удалить", key=f"del_{member['id']}", disabled=not allowed, help=reason):
                try:
                    delete_member(db, member["id"]); save_db(db); st.rerun()
                except ValueError as exc:
                    st.error(str(exc))
        if st.session_state.edit_member_id == member["id"]:
            with st.form(f"edit_member_{member['id']}"):
                new_name = st.text_input("Имя", value=member["name"], key=f"edit_name_{member['id']}")
                new_role = st.text_input("Роль", value=member["role"], key=f"edit_role_{member['id']}")
                new_instructions = st.text_area("Инструкции", value=member["instructions"], height=120, disabled=member["type"] != "ai", key=f"edit_instructions_{member['id']}")
                new_model = None
                if member["type"] == "ai":
                    idx = ALLOWED_GROQ_MODELS.index(member["model"]) if member["model"] in ALLOWED_GROQ_MODELS else 0
                    new_model = st.selectbox("Модель Groq", ALLOWED_GROQ_MODELS, index=idx, key=f"edit_model_{member['id']}")
                b1, b2 = st.columns(2)
                if b1.form_submit_button("💾 Сохранить", type="primary"):
                    update_member(db, member["id"], name=new_name, role=new_role, instructions=new_instructions, model=new_model)
                    save_db(db); st.session_state.edit_member_id = None; st.rerun()
                if b2.form_submit_button("Отмена"):
                    st.session_state.edit_member_id = None; st.rerun()

with tabs[2]:
    col_title, col_action = st.columns([4, 1])
    col_title.subheader("Проекты")
    project_search = st.text_input("🔍 Поиск по проектам", placeholder="Название или цель", key="project_search")
    with col_action.popover("＋ Новый проект", use_container_width=True):
        p_name = st.text_input("Название проекта *", key="project_name")
        p_goal = st.text_area("Бизнес-цель *", height=90, key="project_goal")
        p_result = st.text_area("Ожидаемый результат", key="project_result")
        p_constraints = st.text_area("Ограничения", key="project_constraints")
        p_criteria = st.text_area("Критерии успеха", key="project_criteria")
        p_sla = st.number_input("SLA (дней на задачу)", min_value=0, max_value=365, value=0, key="new_project_sla")
        p_webhook = st.text_input("Webhook URL", key="new_project_webhook")
        p_email = st.toggle("Email-уведомления", key="new_project_email")
        if st.button("Создать проект", type="primary", use_container_width=True, key="create_project_btn"):
            if not p_name.strip() or not p_goal.strip():
                st.error("Заполните название и цель.")
            elif any(p["name"].strip().lower() == p_name.strip().lower() for p in db["projects"].values()):
                st.error(f"Проект с названием «{p_name.strip()}» уже существует. Выберите другое имя.")
            else:
                new_project = create_project(db, name=p_name, goal=p_goal, desired_result=p_result, constraints=p_constraints, success_criteria=p_criteria)
                new_project["sla_days"] = int(p_sla)
                new_project["webhook_url"] = p_webhook.strip()
                new_project["email_notify"] = p_email
                save_db(db)
                st.rerun()
    projects_list = list(db["projects"].values())
    if project_search.strip():
        q = project_search.strip().lower()
        projects_list = [p for p in projects_list if q in p["name"].lower() or q in p["goal"].lower()]
    if not projects_list:
        st.info("Проектов не найдено.")
    for project in projects_list:
        members = project_members(db, project["id"])
        tasks = [t for t in db["tasks"].values() if t["project_id"] == project["id"]]
        done_count = len([t for t in tasks if t["status"] in {"internally_accepted", "customer_approved"}])
        risks = project.get("risks", [])
        open_risks = len([r for r in risks if r.get("status") == "open"])
        sla_info = f" · SLA: {project.get('sla_days', 0)} дн." if project.get("sla_days") else ""
        with st.expander(f"📁 {project['name']} · Участников: {len(members)} · Задач: {len(tasks)} · Выполнено: {done_count} · Открытых рисков: {open_risks}{sla_info}"):
            c1, c2, c3, c4, c5 = st.columns([1, 1, 1, 1, 1])
            if c1.button("＋ Участник", key=f"add_pm_{project['id']}", type="primary", use_container_width=True):
                add_project_member_dialog(project["id"])
            if c2.button("✏️ Редактировать", key=f"edit_project_{project['id']}", use_container_width=True):
                edit_project_dialog(project["id"])
            if c3.button("＋ Быстрая задача", key=f"quick_task_{project['id']}", use_container_width=True):
                new_task_dialog(preselected_project=project["id"])
            if c4.button("📄 Дублировать", key=f"dup_project_{project['id']}", use_container_width=True):
                try:
                    new_project = duplicate_project(project["id"])
                    save_db(db)
                    st.success(f"Создан проект-копия: {new_project['name']}")
                except ValueError as exc:
                    st.error(str(exc))
                st.rerun()
            md = export_project_markdown(project["id"])
            st.download_button("⬇️ Экспорт всех задач (MD)", data=md, file_name=f"{project['id']}_project.md", mime="text/markdown", key=f"export_project_md_{project['id']}")
            txt = export_project_txt(project["id"])
            st.download_button("⬇️ Экспорт всех задач (TXT)", data=txt, file_name=f"{project['id']}_project.txt", mime="text/plain", key=f"export_project_txt_{project['id']}")
            json_data = export_project_json(project["id"])
            st.download_button("⬇️ Экспорт проекта (JSON)", data=json_data, file_name=f"{project['id']}_project.json", mime="application/json", key=f"export_project_json_{project['id']}")
            st.divider()
            if not members:
                st.warning("Участников пока нет.")
            else:
                for link in members:
                    member = link["member"]
                    row1, row2 = st.columns([5, 1])
                    row1.write(f"{member_label(member)} · **Роль в проекте:** {link['project_role']}")
                    if row2.button("Убрать", key=f"remove_{project['id']}_{member['id']}"):
                        remove_member_from_project(db, project["id"], member["id"]); save_db(db); st.rerun()
            st.divider()
            st.write(f"**Цель:** {project['goal']}")
            if project.get("desired_result"):
                st.write(f"**Ожидаемый результат:** {project['desired_result']}")
            if project.get("constraints"):
                st.write(f"**Ограничения:** {project['constraints']}")
            if project.get("success_criteria"):
                st.write(f"**Критерии успеха:** {project['success_criteria']}")
            st.write(f"**SLA:** {project.get('sla_days', 0)} дн. · **Webhook:** {project.get('webhook_url') or '—'} · **Email:** {'вкл' if project.get('email_notify') else 'выкл'}")
            st.divider()
            st.markdown("#### ⚠️ Риски")
            for idx, r in enumerate(risks):
                st.write(f"• **{r.get('title', '')}** — влияние: {r.get('impact', '')}, вероятность: {r.get('probability', '')}, статус: {r.get('status', '')}")
                if st.button("Закрыть риск", key=f"close_risk_{project['id']}_{idx}"):
                    update_risk_status(project["id"], idx, "closed")
                    save_db(db)
                    st.rerun()
            risk_title = st.text_input("Название риска", key=f"risk_title_{project['id']}")
            risk_impact = st.selectbox("Влияние", ["Низкое", "Среднее", "Высокое"], key=f"risk_impact_{project['id']}")
            risk_prob = st.selectbox("Вероятность", ["Низкая", "Средняя", "Высокая"], key=f"risk_prob_{project['id']}")
            risk_mitigation = st.text_area("Меры снижения", key=f"risk_mit_{project['id']}")
            if st.button("Добавить риск", key=f"add_risk_{project['id']}"):
                try:
                    add_risk(project["id"], risk_title, risk_impact, risk_prob, risk_mitigation)
                    save_db(db)
                    st.rerun()
                except ValueError as exc:
                    st.error(str(exc))
            st.divider()
            st.markdown("#### ✅ Решения")
            for d in project.get("decisions", []):
                st.write(f"• **{d.get('title', '')}** — {d.get('description', '')}")
            dec_title = st.text_input("Название решения", key=f"dec_title_{project['id']}")
            dec_desc = st.text_area("Описание решения", key=f"dec_desc_{project['id']}")
            if st.button("Добавить решение", key=f"add_dec_{project['id']}"):
                try:
                    add_decision(project["id"], dec_title, dec_desc)
                    save_db(db)
                    st.rerun()
                except ValueError as exc:
                    st.error(str(exc))

with tabs[3]:
    col_title, col_action = st.columns([4, 1])
    col_title.subheader("Задачи")
    if col_action.button("＋ Новая задача", use_container_width=True, type="primary", key="new_task_btn"):
        new_task_dialog()

    c1, c2, c3, c4 = st.columns(4)
    search_query = c1.text_input("🔍 Поиск", placeholder="Название, описание или критерии", key="task_search")
    status_filter = c2.selectbox("Статус", ["Все статусы", "К выполнению", "В работе", "На доработке", "На проверке", "Принято", "Согласовано", "Эскалировано"], key="status_filter")
    priority_filter = c3.selectbox("Приоритет", ["Все приоритеты"] + [priority_label(p) for p in TASK_PRIORITIES], key="priority_filter")
    project_filter = c4.selectbox("Проект", ["Все проекты"] + [p["name"] for p in db["projects"].values()], key="project_filter")

    d1, d2, d3, d4 = st.columns(4)
    creator_filter = d1.selectbox("Постановщик", ["Все постановщики"] + [m["name"] for m in db["members"].values() if m["status"] == "active"], key="creator_filter")
    executor_filter = d2.selectbox("Исполнитель", ["Все исполнители"] + [m["name"] for m in db["members"].values() if m["status"] == "active"], key="executor_filter")
    reviewer_filter = d3.selectbox("Проверяющий", ["Все проверяющие", "Без проверяющего"] + [m["name"] for m in db["members"].values() if m["status"] == "active"], key="reviewer_filter")
    deadline_filter = d4.selectbox("Дедлайн", ["Все дедлайны", "Просроченные", "На этой неделе", "Без дедлайна", "Просроченные без результата", "Дедлайн в ближайшие 3 дня", "Повторяющиеся задачи"], key="deadline_filter")

    e1, e2, e3, e4 = st.columns(4)
    has_artifact = e1.selectbox("Результат", ["Любой", "Есть результат", "Нет результата"], key="has_artifact")
    has_comments = e2.selectbox("Комментарии", ["Любой", "Есть комментарии", "Нет комментариев"], key="has_comments")
    has_attachments = e3.selectbox("Вложения", ["Любой", "Есть вложения", "Нет вложений"], key="has_attachments")
    sort_mode = e4.selectbox("Сортировка", ["По дедлайну", "По приоритету", "По дате создания", "По комментариям", "По вложениям", "По прогрессу чек-листа"], key="sort_mode")

    hide_done = st.toggle("Скрыть завершённые", value=False, key="hide_done")
    if st.button("♻️ Сбросить фильтры", key="reset_filters_btn"):
        for key in ["task_search", "status_filter", "priority_filter", "project_filter", "creator_filter", "executor_filter", "reviewer_filter", "deadline_filter", "has_artifact", "has_comments", "has_attachments", "sort_mode", "hide_done"]:
            st.session_state.pop(key, None)
        st.rerun()

    status_map = {
        "К выполнению": "todo", "В работе": "in_progress", "На доработке": "changes_requested",
        "На проверке": "review", "Принято": "internally_accepted", "Согласовано": "customer_approved",
        "Эскалировано": "escalated",
    }
    tasks = list(db["tasks"].values())

    if search_query.strip():
        q = search_query.strip().lower()
        tasks = [t for t in tasks if q in t["title"].lower() or q in t["description"].lower() or q in t.get("success_criteria", "").lower()]
    if status_filter != "Все статусы":
        wanted = status_map.get(status_filter)
        if wanted:
            tasks = [t for t in tasks if t["status"] == wanted]
    if priority_filter != "Все приоритеты":
        wanted_priority = next((p for p, label in PRIORITY_LABELS.items() if label == priority_filter), None)
        if wanted_priority:
            tasks = [t for t in tasks if t.get("priority", "medium") == wanted_priority]
    if project_filter != "Все проекты":
        pid = next((p["id"] for p in db["projects"].values() if p["name"] == project_filter), None)
        if pid:
            tasks = [t for t in tasks if t.get("project_id") == pid]
    if creator_filter != "Все постановщики":
        cid = next((m["id"] for m in db["members"].values() if m["name"] == creator_filter), None)
        if cid:
            tasks = [t for t in tasks if t.get("created_by") == cid]
    if executor_filter != "Все исполнители":
        eid = next((m["id"] for m in db["members"].values() if m["name"] == executor_filter), None)
        if eid:
            tasks = [t for t in tasks if t.get("executor_id") == eid]
    if reviewer_filter == "Без проверяющего":
        tasks = [t for t in tasks if not t.get("reviewer_id")]
    elif reviewer_filter != "Все проверяющие":
        rid = next((m["id"] for m in db["members"].values() if m["name"] == reviewer_filter), None)
        if rid:
            tasks = [t for t in tasks if t.get("reviewer_id") == rid]
    if deadline_filter != "Все дедлайны":
        filtered = []
        now = datetime.now()
        week_end = now + timedelta(days=7)
        soon_end = now + timedelta(days=3)
        for t in tasks:
            if deadline_filter == "Повторяющиеся задачи":
                if t.get("recurrence"):
                    filtered.append(t)
                continue
            dl = t.get("deadline")
            if deadline_filter == "Без дедлайна":
                if not dl:
                    filtered.append(t)
                continue
            if not dl:
                continue
            try:
                dt = datetime.fromisoformat(dl)
            except ValueError:
                continue
            if deadline_filter == "Просроченные" and dt < now:
                filtered.append(t)
            if deadline_filter == "На этой неделе" and now <= dt <= week_end:
                filtered.append(t)
            if deadline_filter == "Просроченные без результата" and dt < now and not t.get("current_artifact_id"):
                filtered.append(t)
            if deadline_filter == "Дедлайн в ближайшие 3 дня" and now <= dt <= soon_end:
                filtered.append(t)
        tasks = filtered
    if has_artifact == "Есть результат":
        tasks = [t for t in tasks if t.get("current_artifact_id")]
    elif has_artifact == "Нет результата":
        tasks = [t for t in tasks if not t.get("current_artifact_id")]
    if has_comments == "Есть комментарии":
        tasks = [t for t in tasks if t.get("comments")]
    elif has_comments == "Нет комментариев":
        tasks = [t for t in tasks if not t.get("comments")]
    if has_attachments == "Есть вложения":
        tasks = [t for t in tasks if t.get("attachments")]
    elif has_attachments == "Нет вложений":
        tasks = [t for t in tasks if not t.get("attachments")]
    if hide_done:
        tasks = [t for t in tasks if t["status"] not in {"internally_accepted", "customer_approved", "escalated"}]

    def checklist_progress(t):
        items = t.get("checklist", [])
        if not items:
            return -1
        done = len([i for i in items if i.get("done")])
        return done / len(items)

    if sort_mode == "По дедлайну":
        tasks.sort(key=lambda t: t.get("deadline") or "9999")
    elif sort_mode == "По приоритету":
        order = {"critical": 0, "high": 1, "medium": 2, "low": 3}
        tasks.sort(key=lambda t: order.get(t.get("priority", "medium"), 9))
    elif sort_mode == "По дате создания":
        tasks.sort(key=lambda t: t.get("created_at", ""), reverse=True)
    elif sort_mode == "По комментариям":
        tasks.sort(key=lambda t: len(t.get("comments", [])), reverse=True)
    elif sort_mode == "По вложениям":
        tasks.sort(key=lambda t: len(t.get("attachments", [])), reverse=True)
    elif sort_mode == "По прогрессу чек-листа":
        tasks.sort(key=checklist_progress, reverse=True)

    if not tasks:
        st.info("Задач по выбранным фильтрам нет.")

    st.divider()
    st.markdown("#### ⚡ Массовые действия")
    selected_ids = st.multiselect("Выберите задачи для массового изменения", [t["id"] for t in tasks], format_func=lambda tid: db["tasks"][tid]["title"], key="bulk_select")
    bulk_status = st.selectbox("Новый статус для выбранных", list(STATUS_ICONS.keys()), format_func=lambda s: f"{status_icon(s)} {s}", key="bulk_status")
    bulk_priority = st.selectbox("Новый приоритет для выбранных", list(TASK_PRIORITIES), format_func=priority_label, key="bulk_priority")
    c1, c2, c3, c4 = st.columns(4)
    if c1.button("Применить статус", type="primary", key="bulk_apply_btn"):
        try:
            bulk_update_status(selected_ids, bulk_status)
            save_db(db)
            st.success(f"Обновлено задач: {len(selected_ids)}")
            st.rerun()
        except ValueError as exc:
            st.error(str(exc))
    if c2.button("Применить приоритет", key="bulk_priority_btn"):
        try:
            bulk_update_priority(selected_ids, bulk_priority)
            save_db(db)
            st.success(f"Обновлён приоритет у задач: {len(selected_ids)}")
            st.rerun()
        except ValueError as exc:
            st.error(str(exc))
    if c3.button("🔎 Проверить просрочки", key="check_overdue_btn", help="Эскалировать задачи, просроченные более чем на 3 дня"):
        escalated_ids = check_overdue_and_escalate(3)
        save_db(db)
        if escalated_ids:
            st.warning(f"Эскалировано задач: {len(escalated_ids)}")
        else:
            st.info("Задач для эскалации нет.")
        st.rerun()
    if c4.button("🗑️ Удалить выбранные", key="bulk_delete_btn", help="Удаление без восстановления"):
        try:
            bulk_delete_tasks(selected_ids)
            save_db(db)
            st.success(f"Удалено задач: {len(selected_ids)}")
            st.rerun()
        except ValueError as exc:
            st.error(str(exc))
    csv_data = export_tasks_csv(tasks)
    st.download_button("⬇️ Экспорт задач в CSV", data=csv_data, file_name="tasks_export.csv", mime="text/csv", key="export_tasks_csv_btn")
    ics_data = export_tasks_ics(tasks)
    st.download_button("📅 Экспорт задач в календарь (ICS)", data=ics_data, file_name="tasks_export.ics", mime="text/calendar", key="export_tasks_ics_btn")

    now = datetime.now()
    for task in tasks:
        project = db["projects"].get(task.get("project_id"), {})
        creator = db["members"].get(task.get("created_by"), {})
        executor = db["members"].get(task.get("executor_id"), {})
        reviewer = db["members"].get(task.get("reviewer_id"), {})
        priority = task.get("priority", "medium")
        comments = task.get("comments", [])
        attachments = task.get("attachments", [])
        checklist = task.get("checklist", [])
        artifact = db["artifacts"].get(task.get("current_artifact_id"), {})
        overdue = False
        due_soon = False
        if task.get("deadline"):
            try:
                dt = datetime.fromisoformat(task["deadline"])
                overdue = dt < now
                due_soon = now <= dt <= now + timedelta(days=3)
            except ValueError:
                pass
        flag = " 🔴" if overdue else (" 🟠" if due_soon else (" 🟡" if task["status"] == "review" else ""))
        rec_badge = " 🔁" if task.get("recurrence") else ""
        checklist_info = f" · ☑️{len([i for i in checklist if i.get('done')])}/{len(checklist)}" if checklist else ""
        counter = f" · 💬{len(comments)} · 📎{len(attachments)}{checklist_info}"
        version = f" · v{artifact['version']}" if artifact else ""
        with st.expander(
            f"📝 {task['title']} · {status_icon(task['status'])} {task['status']} · "
            f"{priority_badge(priority)} · "
            f"{'Проект: ' + project.get('name', '—') if task.get('project_id') else 'Без проекта'}"
            f"{version}{counter}{flag}{rec_badge}"
        ):
            c1, c2, c3 = st.columns(3)
            c1.write(f"**Постановщик:** {creator.get('name', '—')}")
            c2.write(f"**Создана:** {format_dt(task.get('created_at'))}")
            c3.write(f"**Дедлайн:** {format_dt(task.get('deadline'))}")
            c4, c5 = st.columns(2)
            c4.write(f"**Исполнитель:** {executor.get('name', '—')} ({executor.get('model', '—')})")
            c5.write(f"**Проверяющий:** {reviewer.get('name', '—')} ({reviewer.get('model', '—')})")
            st.write(f"**ID:** {task['id']}")
            st.write(f"**Описание:** {task['description']}")
            st.write(f"**Критерии:** {task['success_criteria']}")
            st.write(f"**Повторение:** {task.get('recurrence') or 'Нет'}")
            st.write(f"**Напоминание:** за {task.get('reminder_days', 1)} дн. до дедлайна")

            if checklist:
                st.markdown("#### ☑️ Чек-лист")
                for idx, item in enumerate(checklist):
                    mark = "✅" if item.get("done") else "⬜"
                    st.write(f"{mark} {item.get('text', '')}")
                    if st.button("Переключить", key=f"toggle_{task['id']}_{idx}"):
                        try:
                            toggle_checklist_item(task["id"], idx)
                            save_db(db)
                            st.rerun()
                        except ValueError as exc:
                            st.error(str(exc))
                new_item = st.text_input("Новый пункт чек-листа", key=f"new_item_{task['id']}")
                if st.button("Добавить пункт", key=f"add_item_{task['id']}"):
                    try:
                        add_checklist_item(task["id"], new_item)
                        save_db(db)
                        st.rerun()
                    except ValueError as exc:
                        st.error(str(exc))

            if artifact:
                st.info(f"Текущая версия результата: v{artifact['version']}")
                with st.expander("Открыть результат"):
                    st.markdown(artifact["content"])
                st.download_button(
                    "⬇️ Скачать результат",
                    data=artifact["content"],
                    file_name=f"{task['id']}_result_v{artifact['version']}.md",
                    mime="text/markdown",
                    key=f"dl_result_{task['id']}",
                )

            b1, b2, b3 = st.columns(3)
            if b1.button("▶️ Запустить автономный цикл", key=f"run_{task['id']}", type="primary"):
                if not task.get("project_id"):
                    st.error("Автономный запуск доступен только для задач, связанных с проектом.")
                else:
                    outcome = {}
                    with st.status("ИИ-команда работает", expanded=True) as status:
                        st.write("Подготовка контекста: проект, задача, база знаний.")
                        outcome = run_autonomous_task(task["id"])
                        if outcome["status"] == "internally_accepted":
                            status.update(label="Задача внутренне принята", state="complete")
                        elif outcome["status"] == "changes_requested":
                            status.update(label="Результат требует доработки", state="complete")
                        elif outcome["status"] == "escalated":
                            status.update(label="Задача эскалирована", state="error")
                        elif outcome["status"] == "paused":
                            status.update(label="Работа приостановлена", state="error")
                        else:
                            status.update(label=outcome.get("message", "Запуск пропущен"), state="complete")
                    if outcome.get("status") == "internally_accepted":
                        st.success("Результат принят внутренней проверкой.")
                    elif outcome.get("status") == "changes_requested":
                        st.warning(f"Ревьюер вернул на доработку. Цикл: {outcome.get('revision_cycle', 1)}.")
                    elif outcome.get("status") == "escalated":
                        st.error(outcome.get("reason", "Задача эскалирована."))
                    elif outcome.get("status") == "paused":
                        st.error(f"Работа приостановлена: {outcome.get('error')}")
                    if outcome.get("run_id"):
                        st.caption(f"Журнал запуска: {outcome['run_id']}")
                    st.rerun()

            if b2.button("✏️ Редактировать", key=f"edit_task_{task['id']}"):
                edit_task_dialog(task["id"])

            if b3.button("📄 Дублировать", key=f"dup_{task['id']}"):
                try:
                    new_task = create_task(
                        db,
                        title=f"{task['title']} (копия)",
                        description=task["description"],
                        created_by=task["created_by"],
                        executor_id=task["executor_id"],
                        reviewer_id=task.get("reviewer_id"),
                        project_id=task.get("project_id"),
                        success_criteria=task.get("success_criteria", ""),
                        deadline=task.get("deadline"),
                        priority=task.get("priority", "medium"),
                    )
                    add_event(db, "task_duplicated", task_id=new_task["id"], source_task_id=task["id"])
                    save_db(db)
                    st.success(f"Создана копия: {new_task['title']}")
                except ValueError as exc:
                    st.error(str(exc))
                st.rerun()

            if task["status"] == "internally_accepted":
                if st.button("✅ Согласовать заказчиком", key=f"approve_{task['id']}", type="primary"):
                    task["status"] = "customer_approved"
                    task["updated_at"] = datetime.now(timezone.utc).isoformat()
                    add_event(db, "task_customer_approved", task_id=task["id"])
                    save_db(db)
                    st.rerun()
                if task.get("recurrence"):
                    if st.button("🔁 Создать следующее повторение", key=f"next_rec_{task['id']}"):
                        try:
                            next_task = create_next_recurrence(task)
                            save_db(db)
                            if next_task:
                                st.success(f"Создана следующая задача: {next_task['title']}")
                        except ValueError as exc:
                            st.error(str(exc))
                        st.rerun()
            if task["status"] in {"internally_accepted", "customer_approved"}:
                if st.button("↩️ Отправить на доработку", key=f"rework_{task['id']}"):
                    task["status"] = "changes_requested"
                    task["updated_at"] = datetime.now(timezone.utc).isoformat()
                    add_event(db, "task_rework_requested", task_id=task["id"])
                    save_db(db)
                    st.rerun()

            st.divider()
            st.markdown("#### 📎 Вложения")
            if not attachments:
                st.caption("Вложений пока нет.")
            for a in attachments:
                st.write(f"• [{a.get('name', '')}]({a.get('url', '')})")
            att_name = st.text_input("Название вложения", key=f"att_name_{task['id']}")
            att_url = st.text_input("Ссылка (URL)", key=f"att_url_{task['id']}")
            if st.button("Добавить вложение", key=f"att_btn_{task['id']}"):
                try:
                    add_attachment(task["id"], att_name, att_url)
                    save_db(db)
                    st.rerun()
                except ValueError as exc:
                    st.error(str(exc))

            st.divider()
            st.markdown("#### 💬 Комментарии")
            if not comments:
                st.caption("Комментариев пока нет.")
            for c in comments:
                st.write(f"• **{c.get('author', '')}** — {format_dt(c.get('created_at'))}: {c.get('text', '')}")
            new_comment = st.text_area("Новый комментарий (до 2000 символов)", key=f"comment_input_{task['id']}", height=80)
            if st.button("Добавить комментарий", key=f"comment_btn_{task['id']}"):
                try:
                    add_comment(task["id"], new_comment)
                    save_db(db)
                    st.rerun()
                except ValueError as exc:
                    st.error(str(exc))
            comments_md = export_comments_markdown(task)
            st.download_button(
                "⬇️ Экспорт комментариев",
                data=comments_md,
                file_name=f"{task['id']}_comments.md",
                mime="text/markdown",
                key=f"export_comments_{task['id']}",
            )

            st.divider()
            template_name = st.text_input("Сохранить как шаблон", placeholder="Название шаблона", key=f"tpl_name_{task['id']}")
            if st.button("💾 Сохранить как шаблон", key=f"tpl_btn_{task['id']}"):
                try:
                    save_task_as_template(task["id"], template_name)
                    save_db(db)
                    st.success(f"Шаблон сохранён: {template_name.strip()}")
                    st.rerun()
                except ValueError as exc:
                    st.error(str(exc))

            with st.expander("🕘 История изменений"):
                events = task_history(task["id"])
                if not events:
                    st.caption("История пока пуста.")
                for item in events:
                    st.write(f"• {format_dt(item.get('created_at'))} — **{item.get('type', 'Событие')}**")

            md = export_task_markdown(task)
            st.download_button(
                "⬇️ Экспорт задачи и результата (MD)",
                data=md,
                file_name=f"{task['id']}_task.md",
                mime="text/markdown",
                key=f"export_task_md_{task['id']}",
            )
            txt = export_task_txt(task)
            st.download_button(
                "⬇️ Экспорт задачи и результата (TXT)",
                data=txt,
                file_name=f"{task['id']}_task.txt",
                mime="text/plain",
                key=f"export_task_txt_{task['id']}",
            )

with tabs[4]:
    st.subheader("📚 База знаний")
    left, right = st.columns([2, 1])
    with left:
        search = st.text_input("🔍 Поиск по документам", key="kb_search")
        docs = cached_knowledge_files(include_deleted=False)
        if search:
            docs = [d for d in docs if search.lower() in d["name"].lower() or search.lower() in d["content"].lower()]
        if not docs:
            st.info("Документов не найдено.")
        for doc in docs:
            with st.expander(f"📄 {doc['name']}"):
                c1, c2 = st.columns(2)
                c1.write(f"**Автор:** {doc['author']}")
                c2.write(f"**Изменён:** {format_dt(doc.get('updated_at'))}")
                if st.button("🗑️ В корзину", key=f"trash_{doc['name']}"):
                    from knowledge import move_to_trash
                    move_to_trash(doc["name"])
                    st.cache_data.clear()
                    st.session_state.kb_notice = f"Документ перемещён в корзину: {doc['name']}"
                    st.rerun()
                st.text(doc["content"][:8000])
    with right:
        st.markdown("#### Добавить документ")
        with st.form("upload_form", clear_on_submit=True):
            upload = st.file_uploader("Файл", type=["txt", "md"], key="kb_upload")
            author = st.text_input("Автор", value="Локальный администратор", key="kb_author")
            if st.form_submit_button("Сохранить", type="primary", use_container_width=True):
                if not upload:
                    st.warning("Выберите файл.")
                else:
                    try:
                        name = Path(upload.name.replace("\\", "/")).name
                        text = upload.getvalue().decode("utf-8-sig")
                        save_uploaded_file(name, text, author=author)
                        st.cache_data.clear()
                        st.session_state.kb_notice = f"Документ сохранён: {name}"
                        st.rerun()
                    except Exception as exc:
                        st.error(f"Ошибка: {exc}")
        st.markdown("#### Корзина")
        trash_docs = [d for d in cached_knowledge_files(include_deleted=True) if d["deleted"]]
        if not trash_docs:
            st.caption("Корзина пуста.")
        for doc in trash_docs:
            c1, c2, c3 = st.columns([4, 1, 1])
            c1.write(f"📄 {doc['name']}")
            if c2.button("♻️", key=f"restore_{doc['name']}", help="Восстановить"):
                from knowledge import restore_from_trash
                restore_from_trash(doc["name"])
                st.cache_data.clear()
                st.session_state.kb_notice = f"Восстановлен: {doc['name']}"
                st.rerun()
            if c3.button("❌", key=f"forever_{doc['name']}", help="Удалить без восстановления"):
                from knowledge import delete_forever
                delete_forever(doc["name"])
                st.cache_data.clear()
                st.session_state.kb_notice = f"Удалён без восстановления: {doc['name']}"
                st.rerun()

with tabs[5]:
    st.subheader("🕘 История запусков")
    output_dir = Path("output")
    runs = sorted([p for p in output_dir.glob("run_*") if p.is_dir()], key=lambda p: p.name, reverse=True) if output_dir.exists() else []
    if not runs:
        st.info("История запусков пока пуста.")
    for run_dir in runs:
        with st.expander(f"Запуск: {run_dir.name}"):
            log_path = run_dir / "run_log.json"
            if log_path.exists():
                try:
                    log = json.loads(log_path.read_text(encoding="utf-8"))
                    c1, c2, c3 = st.columns(3)
                    c1.write(f"**Автор:** {log.get('author', 'Не указан')}")
                    c2.write(f"**Проект:** {log.get('project_name', 'Не указан')}")
                    c3.write(f"**Статус:** {log.get('status', '—')}")
                    c4, c5, c6 = st.columns(3)
                    c4.write(f"**Создан:** {format_dt(log.get('created_at'))}")
                    c5.write(f"**Ошибок:** {log.get('error_count', len(log.get('errors', [])))}")
                    c6.write(f"**Документов:** {len(log.get('artifacts', []))}")
                    st.write(f"**Модели:** {', '.join(log.get('models_used', [])) or 'Не указаны'}")
                    for stage in log.get("stages", []):
                        st.write(f"• **{stage.get('stage', 'Этап')}** — {stage.get('status', '—')} · {stage.get('details', '')}")
                except Exception as exc:
                    st.warning(f"Не удалось прочитать журнал: {exc}")
            md_files = sorted(run_dir.glob("*.md"))
            if md_files:
                selected = st.selectbox("Открыть документ", md_files, format_func=lambda p: p.name, key=f"file_{run_dir.name}")
                try:
                    content = selected.read_text(encoding="utf-8-sig")
                    st.markdown(content)
                    st.download_button("Скачать документ", data=content, file_name=selected.name, mime="text/markdown", key=f"dl_{run_dir.name}_{selected.name}")
                except Exception as exc:
                    st.error(str(exc))
            zip_buffer = io.BytesIO()
            with zipfile.ZipFile(zip_buffer, "w", zipfile.ZIP_DEFLATED) as zf:
                for file_path in run_dir.rglob("*"):
                    if file_path.is_file():
                        zf.write(file_path, file_path.relative_to(run_dir))
            st.download_button("Скачать ZIP", data=zip_buffer.getvalue(), file_name=f"{run_dir.name}.zip", mime="application/zip", key=f"zip_{run_dir.name}")

with tabs[6]:
    st.subheader("ИИ-чат")
    ai_agents = [m for m in db["members"].values() if m["type"] == "ai" and m["status"] == "active"]
    if not ai_agents:
        st.warning("Сначала добавьте ИИ-агента во вкладке «Команда».")
    else:
        selected_id = st.selectbox("Выберите агента", [m["id"] for m in ai_agents], format_func=lambda mid: member_label(db["members"][mid]), key=f"chat_agent_{st.session_state.chat_session}")
        agent = db["members"][selected_id]
        st.info(f"Агент: **{agent['name']}** · Модель: **{agent['model']}**")
        test_col, reset_col = st.columns([1, 1])
        if test_col.button("🔌 Проверить подключение", use_container_width=True, key=f"test_{st.session_state.chat_session}"):
            with st.spinner("Проверяем доступ к Groq..."):
                try:
                    answer = ask(agent["role"] or "ИИ-помощник", "Ответь одним словом: работает", model=agent["model"])
                    st.success(f"Подключение работает. Ответ агента: {answer}")
                except Exception as exc:
                    st.error(format_llm_error(exc))
        reset_col.button("🧹 Новая беседа", use_container_width=True, type="primary", on_click=start_new_chat, key=f"reset_{st.session_state.chat_session}")
        for message in st.session_state.chat_history:
            with st.chat_message(message["role"]):
                st.markdown(message["content"])
        user_message = st.chat_input("Введите вопрос агенту", key=f"chat_input_{st.session_state.chat_session}")
        if user_message:
            st.session_state.chat_history.append({"role": "user", "content": user_message})
            with st.chat_message("user"):
                st.markdown(user_message)
            history = "\n\n".join([
                f"{'Пользователь' if m['role'] == 'user' else 'Агент'}: {m['content']}"
                for m in st.session_state.chat_history[-10:]
            ])
            prompt = (
                f"{agent.get('instructions') or 'Ты полезный ИИ-помощник.'}\n\n"
                f"ИСТОРИЯ ДИАЛОГА:\n{history}\n\nНОВЫЙ ВОПРОС:\n{user_message}"
            )
            with st.chat_message("assistant"):
                with st.spinner("Агент отвечает..."):
                    try:
                        answer = ask(agent["role"] or "ИИ-помощник", prompt, model=agent["model"])
                    except Exception as exc:
                        answer = format_llm_error(exc)
                        st.error(answer)
            if not answer.startswith("Groq отклонил") and not answer.startswith("Ошибка запроса"):
                st.session_state.chat_history.append({"role": "assistant", "content": answer})
            st.rerun()

with tabs[7]:
    st.subheader("Активность")
    events = recent_events(db, limit=100)
    if not events:
        st.info("Событий пока нет.")
    EVENT_LABELS = {
        "ai": "ИИ-агент выполнил задачу",
        "project_member_added": "Добавлен участник в проект",
        "project_created": "Создан проект",
        "task_created": "Создана задача",
        "task_completed": "Задача завершена",
        "project_duplicated": "Создана копия проекта",
    }
    for item in events:
        label = EVENT_LABELS.get(item.get("type", ""), item.get("type", "Событие"))
        with st.expander(f"{label} — {format_dt(item.get('created_at'))}"):
            st.json(item, expanded=False)

RELEASES = [
    {"version": "2.20 — Интеграции, аналитика и удобство", "date": "2026-10-29", "sections": {"Интеграции": ["Webhook URL проекта", "Email-уведомления", "Экспорт в ICS", "Экспорт проекта в JSON"], "Аналитика": ["KPI: всего, выполнено, просрочено, в срок %", "Прогноз завершения", "Тепловая карта загрузки"], "Удобство": ["Тёмная тема (подсказка)", "Горячие клавиши (подсказка)", "Массовое удаление задач"], "История": ["Событие удаления задачи"]}},
    {"version": "2.19 — Автоматизация и напоминания", "date": "2026-10-29", "sections": {"Повторяющиеся задачи": ["Интервалы", "Следующее повторение", "Фильтр"], "Напоминания": ["Дней до дедлайна", "Сводка"], "Автоэскалация": ["Проверка просрочек"], "Массовые действия": ["Приоритет"]}},
    {"version": "2.18 — Контроль исполнения и риски", "date": "2026-10-29", "sections": {"Чек-листы": ["Пункты, отметки, прогресс"], "Риски": ["Реестр, влияние, вероятность", "Закрытие", "Сводка"], "Решения": ["Журнал"], "Фильтры": ["Дедлайн в 3 дня"], "Сортировки": ["По прогрессу чек-листа"], "Экспорты": ["Чек-лист, риски, решения"]}},
    {"version": "2.17 — Массовые действия и экспорт", "date": "2026-10-29", "sections": {"Массовые действия": ["Мультивыбор", "Статус", "CSV"], "Проекты": ["Поиск", "Счётчик", "Дублирование"], "Команда": ["Поиск"]}},
    {"version": "2.16 — Полная история 1.x и 2.x", "date": "2026-10-29", "sections": {"История версий": ["1.x", "2.1–2.15"]}},
    {"version": "2.15 — Полная история версий", "date": "2026-10-29", "sections": {"История версий": ["2.1–2.4", "2.1–2.14"]}},
    {"version": "2.14 — История версий", "date": "2026-10-29", "sections": {"История версий": ["Новая вкладка", "Клик по релизу"]}},
    {"version": "2.13 — Hundred Steps", "date": "2026-10-29", "sections": {"Фильтры": ["Результат, комментарии, вложения", "Просроченные без результата", "Без проверяющего"], "Сортировки": ["Дедлайн, приоритет, дата, счётчики"], "Экспорты": ["MD/TXT", "Комментарии", "Результат"], "Аналитика": ["Приоритеты, статусы, прогресс, дедлайны"], "Быстрые действия": ["Быстрая задача, дублирование, согласование, доработка"], "Валидации": ["URL, шаблон, комментарий"]}},
    {"version": "2.12 — Thirty Steps", "date": "2026-10-29", "sections": {"Статусы": ["Метки", "Просрочка", "Ревью"], "Экспорты": ["TXT", "ID"], "Интерфейс": ["Счётчики", "Сброс", "Время"]}},
    {"version": "2.11 — Ten Steps", "date": "2026-10-29", "sections": {"Приоритеты": ["Метки"], "Фильтры": ["Постановщик, проверяющий"], "Сортировки": ["Дедлайн, приоритет, дата"], "Проекты": ["Быстрая задача, экспорт"], "Задачи": ["Дублирование, скрыть, счётчики"], "Обзор": ["Прогресс-бар"]}},
    {"version": "2.10 — Combined", "date": "2026-10-29", "sections": {"Вложения": ["Ссылки"], "Шаблоны": ["Сохранение, создание"], "Аналитика": ["Сводка"], "Поиск": ["Задачи"], "Экспорт": ["Расширенный"]}},
    {"version": "2.9 — Time Fix", "date": "2026-10-29", "sections": {"Время": ["Формат", "Локальное время"]}},
    {"version": "2.8 — Comments and Deadlines", "date": "2026-10-29", "sections": {"Комментарии": ["Добавление"], "Дедлайны": ["Фильтр, ближайшие"], "Экспорт": ["Комментарии"]}},
    {"version": "2.7 — Approvals and Rework", "date": "2026-10-29", "sections": {"Согласование": ["Статус, кнопка"], "Доработка": ["Кнопка"], "История": ["Изменения"], "Фильтры": ["Проект, исполнитель"], "Экспорт": ["Задача, результат"]}},
    {"version": "2.6 — UI Fix", "date": "2026-10-29", "sections": {"Участники": ["Списки, поиск, активные"], "Дедлайн": ["Одно поле"], "Удаление": ["Блокировка"]}},
    {"version": "2.5 — Storage and Priorities", "date": "2026-10-29", "sections": {"Хранилище": ["Копии, восстановление"], "Задачи": ["Приоритеты, редактирование"], "Журнал": ["Запуск, этапы"]}},
    {"version": "2.4 — Review and Export", "date": "2026-10-28", "sections": {"Ревью": ["Проверка, статус"], "Экспорт": ["Задача, результат"], "История": ["События"]}},
    {"version": "2.3 — Task Flow", "date": "2026-10-28", "sections": {"Задачи": ["Создание, статусы, роли"], "Проекты": ["Связь"], "Запуск": ["Цикл"]}},
    {"version": "2.2 — Basic UI", "date": "2026-10-28", "sections": {"Интерфейс": ["Вкладки, формы, уведомления"], "Участники": ["Добавление"], "Проекты": ["Создание"]}},
    {"version": "2.1 — Initial Setup", "date": "2026-10-28", "sections": {"Основа": ["Структура, хранилище, модели"], "Агенты": ["Профили, модель"], "Знания": ["База знаний"]}},
    {"version": "1.5 — Chat and Activity", "date": "2026-10-27", "sections": {"Чат": ["Диалог, история, проверка"], "Активность": ["Журнал, детали"]}},
    {"version": "1.4 — Runs and Artifacts", "date": "2026-10-27", "sections": {"Запуски": ["История, журнал, ZIP"], "Результаты": ["Версии, просмотр, скачивание"]}},
    {"version": "1.3 — Knowledge Base", "date": "2026-10-26", "sections": {"Знания": ["Загрузка, поиск, корзина"]}},
    {"version": "1.2 — Agents and Models", "date": "2026-10-26", "sections": {"Агенты": ["Создание, модель, инструкции"], "Команда": ["Управление"]}},
    {"version": "1.1 — Projects and Tasks", "date": "2026-10-25", "sections": {"Проекты": ["Создание, участники"], "Задачи": ["Создание, связь"]}},
    {"version": "1.0 — Initial Release", "date": "2026-10-25", "sections": {"Основа": ["Каркас, хранилище, интерфейс"], "Интеграции": ["Groq, ask"]}},
]

with tabs[8]:
    st.subheader("🕘 История версий")
    st.caption("Нажмите на релиз, чтобы открыть изменения по разделам функционала.")
    for release_item in RELEASES:
        with st.expander(f"{release_item['version']} · {release_item['date']}"):
            for section, items in release_item["sections"].items():
                st.markdown(f"**{section}**")
                for item in items:
                    st.write(f"• {item}")
                st.write("")

st.divider()
if not os.environ.get("SUPABASE_URL") or not os.environ.get("SUPABASE_SECRET_KEY"):
    st.sidebar.warning(
        "⚠️ Supabase не подключён: данные хранятся локально и могут сброситься "
        "при обновлении приложения на Streamlit Cloud."
    )
else:
    st.sidebar.success("✅ Данные хранятся в Supabase.")

