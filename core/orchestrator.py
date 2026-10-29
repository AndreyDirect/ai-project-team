"""Оркестратор автономной ИИ-команды 2.5.

Добавлены: единый журнал запуска, этапы, сохранение результата в историю, ошибки.
"""

from __future__ import annotations

import json
import re
from datetime import datetime, timezone
from typing import Any

from core.agent_profiles import build_agent_prompt
from core.models import (
    add_event, create_artifact, get_artifact, get_member,
    get_task, load_db, save_db,
)
from storage import (
    create_run_dir, register_error, register_model,
    run_lock, save_file, save_run_log,
)

try:
    from llm import ask
except ImportError as exc:
    raise RuntimeError("Не найден llm.py с функцией ask(role, prompt, model)") from exc

try:
    from knowledge import search_knowledge
except ImportError:
    def search_knowledge(text: str) -> str:
        return ""


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


def _extract_json(text: str) -> dict[str, Any]:
    text = text.strip()
    fenced = re.search(r"```(?:json)?\s*(\{.*?\})\s*```", text, re.DOTALL)
    if fenced:
        text = fenced.group(1)
    else:
        match = re.search(r"\{.*\}", text, re.DOTALL)
        if match:
            text = match.group(0)
    data = json.loads(text)
    if not isinstance(data, dict):
        raise ValueError("Агент вернул не JSON-объект")
    return data


def _ask_json(role: str, prompt: str, model: str) -> dict[str, Any]:
    prompt = f"{prompt}\n\nОтветь ТОЛЬКО корректным JSON-объектом без пояснений и без Markdown."
    try:
        response = ask(role, prompt, model=model)
    except TypeError:
        response = ask(role, prompt)
    if not isinstance(response, str) or not response.strip():
        raise ValueError(f"{role} вернул пустой ответ")
    return _extract_json(response)


def _set_stage(run_dir, stages: list[dict[str, Any]], stage: str, status: str, details: str = "") -> None:
    for item in stages:
        if item["stage"] == stage:
            item["status"] = status
            if details:
                item["details"] = details
            break
    else:
        stages.append({"stage": stage, "status": status, "details": details})
    save_run_log(run_dir, {"stages": stages})


def run_autonomous_task(task_id: str) -> dict[str, Any]:
    db = load_db()
    task = get_task(db, task_id)

    if not task.get("project_id"):
        return {
            "status": "skipped",
            "message": "Задача без проекта не поддерживает автономный запуск. Свяжите задачу с проектом.",
        }

    executor = get_member(db, task["executor_id"])
    reviewer = get_member(db, task["reviewer_id"])
    project = db["projects"][task["project_id"]]

    run_dir = create_run_dir(
        project_id=project["id"],
        project_name=project["name"],
        task_id=task["id"],
        task_title=task["title"],
        author=executor.get("name", "Не указан"),
    )

    stages: list[dict[str, Any]] = []

    try:
        with run_lock(run_dir):
            knowledge = search_knowledge(
                "\n".join([
                    project.get("goal", ""), task.get("title", ""),
                    task.get("description", ""), task.get("success_criteria", ""),
                ])
            )

            context = "\n\n".join([
                f"ПРОЕКТ: {project['name']}",
                f"ЦЕЛЬ ПРОЕКТА:\n{project['goal']}",
                f"ОЖИДАЕМЫЙ РЕЗУЛЬТАТ:\n{project.get('desired_result') or 'Не указан'}",
                f"ОГРАНИЧЕНИЯ:\n{project.get('constraints') or 'Не указаны'}",
                f"КРИТЕРИИ УСПЕХА ПРОЕКТА:\n{project.get('success_criteria') or 'Не указаны'}",
                f"ЗАДАЧА: {task['title']}",
                f"ОПИСАНИЕ:\n{task['description']}",
                f"КРИТЕРИИ ГОТОВНОСТИ:\n{task['success_criteria']}",
                f"ПРИОРИТЕТ: {task.get('priority', 'medium')}",
                f"МАТЕРИАЛЫ:\n{knowledge or 'Не найдены.'}",
            ])

            executor_model = executor.get("model") or "llama-3.3-70b-versatile"
            reviewer_model = reviewer.get("model") or executor_model

            register_model(run_dir, executor_model)
            register_model(run_dir, reviewer_model)

            if task["status"] not in {"todo", "changes_requested"}:
                save_run_log(run_dir, {"status": "skipped", "stages": stages})
                return {
                    "status": "skipped",
                    "message": f"Задача в статусе {task['status']}.",
                    "run_id": run_dir.name,
                }

            task["status"] = "in_progress"
            task["updated_at"] = _now()
            add_event(
                db, "run_started",
                task_id=task_id, run_id=run_dir.name,
                executor_id=executor["id"], executor_model=executor_model,
                reviewer_id=reviewer["id"], reviewer_model=reviewer_model,
            )
            save_db(db)
            _set_stage(run_dir, stages, "context", "completed", "Проект, задача и база знаний подготовлены.")

            _set_stage(run_dir, stages, "execution", "in_progress", "Исполнитель готовит результат.")

            if task["current_artifact_id"]:
                previous = get_artifact(db, task["current_artifact_id"])
                executor_prompt = (
                    f"{context}\n\nПРЕДЫДУЩАЯ ВЕРСИЯ:\n{previous['content']}\n\n"
                    "Исправь предыдущую версию с учётом замечаний."
                )
                version = previous["version"] + 1
            else:
                executor_prompt = f"{context}\n\nПодготовь результат задачи."
                version = 1

            response = _ask_json(
                "analyst",
                build_agent_prompt("analyst", executor_prompt),
                executor_model,
            )
            if response.get("decision") != "submit_result":
                raise ValueError(f"Исполнитель вернул: {response.get('decision')}")

            content = str(response.get("content", "")).strip()
            if not content:
                raise ValueError("Исполнитель не вернул content")

            artifact = create_artifact(db, task_id, executor["id"], content, version)
            artifact_filename = f"{version:02d}_result.md"
            save_file(run_dir, artifact_filename, content)

            save_run_log(run_dir, {
                "artifacts": [artifact_filename],
                "artifact_id": artifact["id"],
                "artifact_version": version,
            })

            task["status"] = "review"
            task["updated_at"] = _now()
            save_db(db)
            _set_stage(run_dir, stages, "execution", "completed", f"Создана версия v{version}.")
            _set_stage(run_dir, stages, "review", "in_progress", "Ревьюер проверяет результат.")

            reviewer_prompt = (
                f"{context}\n\nРЕЗУЛЬТАТ (v{artifact['version']}):\n{content}\n\n"
                "Верни JSON: {\"decision\": \"accept_internal\" или \"request_changes\", "
                "\"criterion_results\": {}, \"findings\": [], \"comment\": \"\"}"
            )
            review = _ask_json(
                "reviewer",
                build_agent_prompt("reviewer", reviewer_prompt),
                reviewer_model,
            )

            decision = review.get("decision")
            if decision not in {"accept_internal", "request_changes"}:
                raise ValueError(f"Ревьюер вернул: {decision}")

            add_event(
                db, "review_completed",
                task_id=task_id, run_id=run_dir.name,
                artifact_id=artifact["id"], decision=decision,
                reviewer_model=reviewer_model, review=review,
            )

            blocking = [
                i for i in review.get("findings", [])
                if isinstance(i, dict) and i.get("severity") == "blocking"
            ]

            review_comment = str(review.get("comment") or "").strip()
            _set_stage(
                run_dir, stages, "review", "completed",
                review_comment or ("Замечаний нет." if decision == "accept_internal" else "Есть замечания."),
            )

            if decision == "accept_internal" or not blocking:
                task["status"] = "internally_accepted"
                task["updated_at"] = _now()
                add_event(
                    db, "task_internally_accepted",
                    task_id=task_id, run_id=run_dir.name, artifact_id=artifact["id"],
                )
                save_db(db)
                save_run_log(run_dir, {"status": "completed"})
                return {
                    "status": "internally_accepted",
                    "run_id": run_dir.name,
                    "artifact_id": artifact["id"],
                    "version": version,
                    "review": review,
                }

            task["revision_cycle"] += 1
            if task["revision_cycle"] > task["max_revision_cycles"]:
                task["status"] = "escalated"
                task["updated_at"] = _now()
                add_event(
                    db, "task_escalated",
                    task_id=task_id, run_id=run_dir.name,
                    reason="revision_limit_reached",
                )
                save_db(db)
                save_run_log(run_dir, {"status": "escalated"})
                return {
                    "status": "escalated",
                    "run_id": run_dir.name,
                    "reason": "Достигнут лимит доработок.",
                    "review": review,
                }

            task["status"] = "changes_requested"
            task["updated_at"] = _now()
            add_event(
                db, "changes_requested",
                task_id=task_id, run_id=run_dir.name,
                artifact_id=artifact["id"],
                revision_cycle=task["revision_cycle"],
                findings=blocking,
            )
            save_db(db)
            save_run_log(run_dir, {"status": "changes_requested"})
            return {
                "status": "changes_requested",
                "run_id": run_dir.name,
                "artifact_id": artifact["id"],
                "version": version,
                "revision_cycle": task["revision_cycle"],
                "review": review,
            }

    except Exception as exc:
        task["status"] = "paused"
        task["updated_at"] = _now()
        add_event(db, "task_paused", task_id=task_id, run_id=run_dir.name, reason=str(exc))
        save_db(db)
        register_error(run_dir, str(exc))
        save_run_log(run_dir, {"status": "paused"})
        return {"status": "paused", "run_id": run_dir.name, "error": str(exc)}
