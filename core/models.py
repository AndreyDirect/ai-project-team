"""Хранилище ИИ-проектного офиса 2.5.

Добавлены: резервные копии базы, приоритеты задач, расширенное редактирование задач.
"""

from __future__ import annotations

import json
import os
import shutil
import tempfile
import uuid
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

DATA_DIR = Path(os.getenv("PROJECT_OFFICE_DATA_DIR", "data"))
DB_PATH = DATA_DIR / "project_office.json"
BACKUP_DIR = DATA_DIR / "backups"
MAX_BACKUPS = 20

TASK_PRIORITIES = {"low", "medium", "high", "critical"}
PRIORITY_LABELS = {
    "low": "Низкий",
    "medium": "Средний",
    "high": "Высокий",
    "critical": "Критический",
}


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


def _id(prefix: str) -> str:
    return f"{prefix}_{uuid.uuid4().hex[:10]}"


def _empty() -> dict[str, Any]:
    return {
        "schema_version": 4,
        "users": {}, "members": {}, "projects": {},
        "project_members": {}, "tasks": {}, "artifacts": {}, "events": [],
    }


def load_db() -> dict[str, Any]:
    DATA_DIR.mkdir(parents=True, exist_ok=True)
    if not DB_PATH.exists():
        return _empty()
    try:
        data = json.loads(DB_PATH.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        backup = DB_PATH.with_suffix(".json.corrupt")
        if DB_PATH.exists():
            shutil.copy2(DB_PATH, backup)
        return _empty()
    if not isinstance(data, dict):
        return _empty()
    for key, value in _empty().items():
        data.setdefault(key, value)
    return data


def _make_backup() -> None:
    if not DB_PATH.exists():
        return
    BACKUP_DIR.mkdir(parents=True, exist_ok=True)
    stamp = datetime.now().strftime("%Y-%m-%d_%H-%M-%S")
    target = BACKUP_DIR / f"project_office_{stamp}.json"
    shutil.copy2(DB_PATH, target)

    backups = sorted(BACKUP_DIR.glob("project_office_*.json"))
    for old in backups[:-MAX_BACKUPS]:
        old.unlink(missing_ok=True)


def save_db(data: dict[str, Any]) -> None:
    DATA_DIR.mkdir(parents=True, exist_ok=True)
    _make_backup()

    tmp = None
    try:
        with tempfile.NamedTemporaryFile(
            "w", encoding="utf-8", dir=DATA_DIR,
            prefix=".db_", suffix=".tmp", delete=False,
        ) as f:
            tmp = Path(f.name)
            json.dump(data, f, ensure_ascii=False, indent=2)
            f.write("\n")
        os.replace(tmp, DB_PATH)
    finally:
        if tmp:
            tmp.unlink(missing_ok=True)


def add_event(db: dict[str, Any], type_: str, **details: Any) -> dict[str, Any]:
    item = {"id": _id("evt"), "type": type_, "created_at": _now(), **details}
    db["events"].append(item)
    return item


event = add_event


def ensure_user(db: dict[str, Any], user_key: str, display_name: str) -> dict[str, Any]:
    user = db["users"].get(user_key)
    if user:
        return user
    user = {
        "id": user_key, "display_name": display_name, "role": "owner",
        "created_at": _now(), "status": "active",
    }
    db["users"][user_key] = user
    add_event(db, "user_created", user_id=user_key, name=display_name)
    return user


def create_member(
    db: dict[str, Any], name: str, member_type: str, role: str,
    user_key: str | None = None, model: str = "", instructions: str = "",
) -> dict[str, Any]:
    if member_type not in {"human", "ai"}:
        raise ValueError("Тип участника: human или ai")
    if member_type == "human" and not user_key:
        raise ValueError("Человеку нужен user_key")
    if member_type == "ai" and not model:
        raise ValueError("ИИ-агенту нужно выбрать модель")

    member_id = _id("mem")
    member = {
        "id": member_id, "name": name.strip(), "type": member_type,
        "role": role.strip(), "user_key": user_key, "model": model.strip(),
        "instructions": instructions.strip(), "profile_version": 1,
        "status": "active", "created_at": _now(),
    }
    db["members"][member_id] = member
    add_event(db, "member_created", member_id=member_id, name=member["name"], type=member_type)
    return member


def update_member(
    db: dict[str, Any], member_id: str, name: str | None = None,
    role: str | None = None, model: str | None = None, instructions: str | None = None,
) -> dict[str, Any]:
    member = db["members"].get(member_id)
    if not member:
        raise ValueError("Участник не найден")
    changed = False
    if name is not None and name.strip() and name != member["name"]:
        member["name"] = name.strip(); changed = True
    if role is not None and role != member["role"]:
        member["role"] = role.strip(); changed = True
    if instructions is not None and instructions != member["instructions"]:
        member["instructions"] = instructions; changed = True
    if model is not None and model != member["model"]:
        if member["type"] != "ai":
            raise ValueError("Модель назначается только ИИ-агенту")
        member["model"] = model.strip(); changed = True
    if changed:
        member["profile_version"] += 1
        add_event(db, "member_updated", member_id=member_id)
    return member


def deactivate_member(db: dict[str, Any], member_id: str) -> None:
    member = db["members"].get(member_id)
    if not member:
        raise ValueError("Участник не найден")
    member["status"] = "inactive"
    add_event(db, "member_deactivated", member_id=member_id)


def activate_member(db: dict[str, Any], member_id: str) -> None:
    member = db["members"].get(member_id)
    if not member:
        raise ValueError("Участник не найден")
    member["status"] = "active"
    add_event(db, "member_activated", member_id=member_id)


def can_delete_member(db: dict[str, Any], member_id: str) -> tuple[bool, str]:
    if member_id not in db["members"]:
        return False, "Участник не найден"
    if any(t.get("executor_id") == member_id or t.get("reviewer_id") == member_id for t in db["tasks"].values()):
        return False, "Участник указан в задачах"
    if any(a.get("author_id") == member_id for a in db["artifacts"].values()):
        return False, "Участник автор результатов"
    if any(l.get("member_id") == member_id for l in db["project_members"].values()):
        return False, "Участник добавлен в проекты"
    return True, "Можно удалить"


def delete_member(db: dict[str, Any], member_id: str) -> None:
    allowed, reason = can_delete_member(db, member_id)
    if not allowed:
        raise ValueError(reason)
    member = db["members"].pop(member_id)
    add_event(db, "member_deleted", member_id=member_id, name=member.get("name", ""))


def create_project(
    db: dict[str, Any], name: str, goal: str,
    desired_result: str = "", constraints: str = "", success_criteria: str = "",
) -> dict[str, Any]:
    project_id = _id("prj")
    project = {
        "id": project_id, "name": name.strip(), "goal": goal.strip(),
        "desired_result": desired_result.strip(), "constraints": constraints.strip(),
        "success_criteria": success_criteria.strip(),
        "status": "active", "created_at": _now(),
    }
    db["projects"][project_id] = project
    add_event(db, "project_created", project_id=project_id, name=project["name"])
    return project


def update_project(
    db: dict[str, Any], project_id: str, name: str | None = None,
    goal: str | None = None, desired_result: str | None = None,
    constraints: str | None = None, success_criteria: str | None = None,
) -> dict[str, Any]:
    project = db["projects"].get(project_id)
    if not project:
        raise ValueError("Проект не найден")
    if name is not None and name.strip():
        project["name"] = name.strip()
    if goal is not None:
        project["goal"] = goal.strip()
    if desired_result is not None:
        project["desired_result"] = desired_result.strip()
    if constraints is not None:
        project["constraints"] = constraints.strip()
    if success_criteria is not None:
        project["success_criteria"] = success_criteria.strip()
    add_event(db, "project_updated", project_id=project_id)
    return project


def add_member_to_project(
    db: dict[str, Any], project_id: str, member_id: str, project_role: str
) -> dict[str, Any]:
    if project_id not in db["projects"]:
        raise ValueError("Проект не найден")
    member = db["members"].get(member_id)
    if not member:
        raise ValueError("Участник не найден")
    if member["status"] != "active":
        raise ValueError("Участник отключён")

    link_id = f"{project_id}:{member_id}"
    link = {
        "id": link_id, "project_id": project_id, "member_id": member_id,
        "project_role": project_role.strip(), "added_at": _now(),
    }
    db["project_members"][link_id] = link
    add_event(db, "project_member_added", project_id=project_id, member_id=member_id, project_role=project_role)
    return link


def remove_member_from_project(db: dict[str, Any], project_id: str, member_id: str) -> None:
    link_id = f"{project_id}:{member_id}"
    if link_id in db["project_members"]:
        del db["project_members"][link_id]
        add_event(db, "project_member_removed", project_id=project_id, member_id=member_id)


def project_members(db: dict[str, Any], project_id: str) -> list[dict[str, Any]]:
    result = []
    for link in db["project_members"].values():
        if link["project_id"] != project_id:
            continue
        member = db["members"].get(link["member_id"])
        if member:
            result.append({**link, "member": member})
    return result


def create_task(
    db: dict[str, Any],
    title: str,
    description: str,
    created_by: str,
    executor_id: str,
    reviewer_id: str | None = None,
    project_id: str | None = None,
    success_criteria: str = "",
    deadline: str | None = None,
    priority: str = "medium",
    max_revision_cycles: int = 2,
) -> dict[str, Any]:
    if not title.strip() or not description.strip():
        raise ValueError("Заполните название и описание задачи")
    if created_by not in db["members"]:
        raise ValueError("Постановщик не найден")
    if executor_id not in db["members"]:
        raise ValueError("Исполнитель не найден")
    if priority not in TASK_PRIORITIES:
        raise ValueError("Недопустимый приоритет задачи")

    if project_id:
        if project_id not in db["projects"]:
            raise ValueError("Проект не найден")
        ids = {
            link["member_id"] for link in db["project_members"].values()
            if link["project_id"] == project_id
        }
        if executor_id not in ids:
            raise ValueError("Исполнитель не добавлен в проект")
        if reviewer_id and reviewer_id not in ids:
            raise ValueError("Проверяющий не добавлен в проект")

    if reviewer_id and reviewer_id == executor_id:
        raise ValueError("Исполнитель и проверяющий должны быть разными")

    task_id = _id("task")
    task = {
        "id": task_id,
        "project_id": project_id,
        "title": title.strip(),
        "description": description.strip(),
        "success_criteria": success_criteria.strip(),
        "created_by": created_by,
        "executor_id": executor_id,
        "reviewer_id": reviewer_id,
        "deadline": deadline,
        "priority": priority,
        "status": "todo",
        "revision_cycle": 0,
        "max_revision_cycles": max_revision_cycles,
        "current_artifact_id": None,
        "created_at": _now(),
        "updated_at": _now(),
    }
    db["tasks"][task_id] = task
    add_event(
        db, "task_created",
        task_id=task_id, title=task["title"],
        project_id=project_id, created_by=created_by,
        executor_id=executor_id, deadline=deadline, priority=priority,
    )
    return task


def update_task(
    db: dict[str, Any], task_id: str,
    title: str | None = None, description: str | None = None,
    success_criteria: str | None = None, deadline: str | None = None,
    executor_id: str | None = None, reviewer_id: str | None = None,
    created_by: str | None = None, project_id: str | None = None,
    priority: str | None = None, status: str | None = None,
    max_revision_cycles: int | None = None,
) -> dict[str, Any]:
    task = db["tasks"].get(task_id)
    if not task:
        raise ValueError("Задача не найдена")

    old_priority = task.get("priority", "medium")
    old_status = task.get("status")

    if title is not None and title.strip():
        task["title"] = title.strip()
    if description is not None:
        task["description"] = description.strip()
    if success_criteria is not None:
        task["success_criteria"] = success_criteria.strip()
    if deadline is not None:
        task["deadline"] = deadline
    if created_by is not None:
        if created_by not in db["members"]:
            raise ValueError("Постановщик не найден")
        task["created_by"] = created_by
    if executor_id is not None:
        if executor_id not in db["members"]:
            raise ValueError("Исполнитель не найден")
        task["executor_id"] = executor_id
    if reviewer_id is not None:
        if reviewer_id and reviewer_id == task.get("executor_id"):
            raise ValueError("Исполнитель и проверяющий должны быть разными")
        task["reviewer_id"] = reviewer_id
    if project_id is not None:
        if project_id and project_id not in db["projects"]:
            raise ValueError("Проект не найден")
        task["project_id"] = project_id or None
    if priority is not None:
        if priority not in TASK_PRIORITIES:
            raise ValueError("Недопустимый приоритет задачи")
        task["priority"] = priority
    if status is not None:
        task["status"] = status
    if max_revision_cycles is not None:
        task["max_revision_cycles"] = max_revision_cycles

    task["updated_at"] = _now()
    add_event(db, "task_updated", task_id=task_id)

    if priority is not None and priority != old_priority:
        add_event(db, "task_priority_changed", task_id=task_id, priority=priority)
    if status is not None and status != old_status:
        add_event(db, "task_status_changed", task_id=task_id, status=status)

    return task


def get_member(db: dict[str, Any], member_id: str) -> dict[str, Any]:
    member = db["members"].get(member_id)
    if not member:
        raise ValueError(f"Участник не найден: {member_id}")
    return member


def get_task(db: dict[str, Any], task_id: str) -> dict[str, Any]:
    task = db["tasks"].get(task_id)
    if not task:
        raise ValueError(f"Задача не найдена: {task_id}")
    return task


def get_artifact(db: dict[str, Any], artifact_id: str) -> dict[str, Any]:
    artifact = db["artifacts"].get(artifact_id)
    if not artifact:
        raise ValueError(f"Результат не найден: {artifact_id}")
    return artifact


def create_artifact(
    db: dict[str, Any], task_id: str, author_id: str, content: str, version: int
) -> dict[str, Any]:
    artifact_id = _id("art")
    artifact = {
        "id": artifact_id, "task_id": task_id, "author_id": author_id,
        "version": version, "content": content, "created_at": _now(),
    }
    db["artifacts"][artifact_id] = artifact
    task = db["tasks"].get(task_id)
    if task:
        task["current_artifact_id"] = artifact_id
        task["updated_at"] = _now()
    add_event(db, "artifact_created", task_id=task_id, artifact_id=artifact_id, version=version)
    return artifact


def recent_events(db: dict[str, Any], limit: int = 100) -> list[dict[str, Any]]:
    return list(reversed(db["events"][-limit:]))
