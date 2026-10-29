"""Хранилище запусков ИИ-проектного офиса.

Совместимо с текущими app_v2.py, orchestrator.py, knowledge.py и config.py.
Добавлены атрибуты запуска: автор, проект, задача, модели, количество ошибок.
"""

from __future__ import annotations

import json
import os
import tempfile
from contextlib import contextmanager
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from config import BRIEF_FILE, OUTPUT_DIR

HISTORY_FILES = {
    "00_brief.md": "Бриф",
    "00_knowledge.md": "Источники",
    "01_plan.md": "План",
    "02_analysis.md": "Анализ",
    "03_architecture.md": "Архитектура",
    "04_document.md": "Итоговый документ",
    "05_review.md": "Проверка",
}

STATUS_LABELS = {
    "completed": "Завершён",
    "failed": "Завершён с ошибкой",
    "paused": "Приостановлен",
    "in_progress": "Выполняется",
    "unknown": "Статус неизвестен",
}


def now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


def format_datetime(value: str | None) -> str:
    """Преобразует ISO-дату в читаемый формат."""
    if not value:
        return "Не указано"
    try:
        dt = datetime.fromisoformat(str(value).replace("Z", "+00:00"))
        return dt.strftime("%d.%m.%Y, %H:%M")
    except ValueError:
        return str(value)


def read_brief() -> str:
    return Path(BRIEF_FILE).read_text(encoding="utf-8-sig")


def create_run_dir(
    project_id: str | None = None,
    project_name: str | None = None,
    task_id: str | None = None,
    task_title: str | None = None,
    author: str | None = None,
) -> Path:
    """Создаёт папку запуска и первичный журнал с атрибутами."""
    folder = Path(OUTPUT_DIR) / (
        "run_" + datetime.now().strftime("%Y-%m-%d_%H-%M-%S_%f")
    )
    folder.mkdir(parents=True, exist_ok=False)

    initial_log = {
        "project_id": project_id,
        "project_name": project_name,
        "task_id": task_id,
        "task_title": task_title,
        "author": author or "Не указан",
        "status": "in_progress",
        "models_used": [],
        "error_count": 0,
        "artifacts": [],
        "errors": [],
        "attempts": [],
    }
    save_run_log(folder, initial_log)
    return folder


def save_file(run_dir, filename: str, content: str) -> Path:
    folder = Path(run_dir).resolve()
    target = folder / filename

    if Path(filename).name != filename or target.resolve().parent != folder:
        raise ValueError("Недопустимое имя файла.")

    temporary = None
    try:
        with tempfile.NamedTemporaryFile(
            mode="w",
            encoding="utf-8",
            dir=folder,
            prefix=".save_",
            suffix=".tmp",
            delete=False,
        ) as handle:
            temporary = Path(handle.name)
            handle.write(content)
        os.replace(temporary, target)
    finally:
        if temporary is not None:
            temporary.unlink(missing_ok=True)

    return target


def get_run_dir(run_id: str) -> Path:
    root = Path(OUTPUT_DIR).resolve()
    if not isinstance(run_id, str) or not run_id or Path(run_id).name != run_id:
        raise ValueError("Недопустимый идентификатор запуска.")

    folder = (root / run_id).resolve()
    if folder.parent != root or not folder.is_dir():
        raise FileNotFoundError("Папка запуска не найдена.")
    return folder


def load_run_text(run_id: str, filename: str) -> str:
    allowed = set(HISTORY_FILES) | {"run_log.json", "00_pipeline.json"}
    if filename not in allowed:
        raise ValueError("Этот файл не входит в результаты запуска.")

    folder = get_run_dir(run_id)
    path = folder / filename
    if path.resolve().parent != folder:
        raise ValueError("Файл находится за пределами папки запуска.")

    return path.read_text(encoding="utf-8-sig")


def load_run_log(run_id: str) -> dict[str, Any]:
    data = json.loads(load_run_text(run_id, "run_log.json"))
    if not isinstance(data, dict):
        raise ValueError("Журнал должен содержать JSON-объект.")
    return data


def save_run_log(run_dir, data: dict[str, Any]) -> Path:
    path = Path(run_dir) / "run_log.json"

    old: dict[str, Any] = {}
    if path.exists():
        old = json.loads(path.read_text(encoding="utf-8-sig"))
        if not isinstance(old, dict):
            raise ValueError("Повреждённый журнал не будет перезаписан.")

    log = {**old, **data}
    log["run_id"] = Path(run_dir).name
    log["created_at"] = old.get("created_at") or data.get("created_at") or now_iso()
    log["updated_at"] = now_iso()
    log.setdefault("artifacts", [])
    log.setdefault("errors", [])
    log.setdefault("attempts", [])
    log.setdefault("models_used", [])

    errors = log.get("errors", [])
    log["error_count"] = len(errors) if isinstance(errors, list) else 1

    return save_file(
        run_dir,
        "run_log.json",
        json.dumps(log, ensure_ascii=False, indent=2) + "\n",
    )


def register_model(run_dir, model: str | None) -> None:
    """Добавляет использованную модель в журнал, если её там ещё нет."""
    if not model:
        return
    try:
        log = load_run_log(Path(run_dir).name)
    except (OSError, ValueError, json.JSONDecodeError):
        log = {}

    models = log.get("models_used", [])
    if not isinstance(models, list):
        models = []
    if model not in models:
        models.append(model)

    save_run_log(run_dir, {"models_used": models})


def register_error(run_dir, error: str) -> None:
    """Добавляет ошибку в журнал и пересчитывает error_count."""
    try:
        log = load_run_log(Path(run_dir).name)
    except (OSError, ValueError, json.JSONDecodeError):
        log = {}

    errors = log.get("errors", [])
    if not isinstance(errors, list):
        errors = []
    errors.append(str(error))

    save_run_log(run_dir, {"errors": errors, "error_count": len(errors)})


@contextmanager
def run_lock(run_dir):
    handle = open(Path(run_dir) / ".run.lock", "a+b")
    locked = False
    try:
        handle.seek(0, 2)
        if handle.tell() == 0:
            handle.write(b"0")
            handle.flush()
        handle.seek(0)

        try:
            if os.name == "nt":
                import msvcrt
                msvcrt.locking(handle.fileno(), msvcrt.LK_NBLCK, 1)
            else:
                import fcntl
                fcntl.flock(handle.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
        except OSError as exc:
            raise RuntimeError(
                "Этот запуск уже выполняется в другом процессе или сеансе."
            ) from exc

        locked = True
        yield
    finally:
        if locked:
            handle.seek(0)
            if os.name == "nt":
                import msvcrt
                msvcrt.locking(handle.fileno(), msvcrt.LK_UNLCK, 1)
            else:
                import fcntl
                fcntl.flock(handle.fileno(), fcntl.LOCK_UN)
        handle.close()


def list_runs() -> list[dict[str, Any]]:
    """Возвращает запуски с читаемыми атрибутами для интерфейса."""
    root = Path(OUTPUT_DIR)
    if not root.exists():
        return []

    runs = []
    for folder in root.iterdir():
        if not folder.name.startswith("run_") or not folder.is_dir() or folder.is_symlink():
            continue

        warnings = []
        try:
            log = load_run_log(folder.name)
        except (OSError, ValueError, json.JSONDecodeError) as exc:
            warnings.append(f"Журнал недоступен: {exc}")
            log = {}

        project = str(log.get("project_name") or "").strip()
        if not project:
            try:
                lines = load_run_text(folder.name, "00_brief.md").splitlines()
                for index, line in enumerate(lines):
                    if line.strip().startswith("ПРОЕКТ:"):
                        project = (
                            line.partition(":")[2].strip()
                            or next(
                                (s.strip() for s in lines[index + 1:] if s.strip()),
                                "",
                            )
                        )
                        break
            except (OSError, ValueError):
                pass

        errors = log.get("errors", [])
        if not isinstance(errors, list):
            errors = [str(errors)]

        models = log.get("models_used", [])
        if not isinstance(models, list):
            models = []

        status = str(log.get("status") or "unknown")

        runs.append({
            "run_id": folder.name,
            "display_id": folder.name.replace("run_", "").split("_")[0:3],
            "project_name": project or "Без проекта",
            "task_title": str(log.get("task_title") or "Без задачи"),
            "author": str(log.get("author") or "Не указан"),
            "created_at": log.get("created_at"),
            "created_at_human": format_datetime(log.get("created_at")),
            "updated_at": log.get("updated_at"),
            "updated_at_human": format_datetime(log.get("updated_at")),
            "status": status,
            "status_human": STATUS_LABELS.get(status, status),
            "model": str(log.get("model") or "Не указана"),
            "models_used": models,
            "error_count": len(errors),
            "errors": errors,
            "artifacts": log.get("artifacts", []),
            "artifact_count": len(log.get("artifacts", [])),
            "warnings": warnings,
        })

    return sorted(runs, key=lambda row: row["run_id"], reverse=True)
