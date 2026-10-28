import json
import os
import tempfile
from contextlib import contextmanager
from datetime import datetime, timezone
from pathlib import Path
from config import BRIEF_FILE, OUTPUT_DIR

HISTORY_FILES = {
    "00_brief.md": "Бриф", "00_knowledge.md": "Источники",
    "01_plan.md": "План", "02_analysis.md": "Анализ",
    "03_architecture.md": "Архитектура", "04_document.md": "Итоговый документ",
    "05_review.md": "Проверка",
}


def now_iso():
    return datetime.now(timezone.utc).isoformat()


def read_brief():
    return Path(BRIEF_FILE).read_text(encoding="utf-8-sig")


def create_run_dir():
    folder = Path(OUTPUT_DIR) / ("run_" + datetime.now().strftime("%Y-%m-%d_%H-%M-%S_%f"))
    folder.mkdir(parents=True, exist_ok=False)
    return folder


def save_file(run_dir, filename, content):
    folder = Path(run_dir).resolve()
    target = folder / filename
    if Path(filename).name != filename or target.resolve().parent != folder:
        raise ValueError("Недопустимое имя файла.")
    temporary = None
    try:
        with tempfile.NamedTemporaryFile(mode="w", encoding="utf-8", dir=folder,
                                         prefix=".save_", suffix=".tmp", delete=False) as handle:
            temporary = Path(handle.name)
            handle.write(content)
        os.replace(temporary, target)
    finally:
        if temporary is not None:
            temporary.unlink(missing_ok=True)
    return target


def get_run_dir(run_id):
    root = Path(OUTPUT_DIR).resolve()
    if not isinstance(run_id, str) or not run_id or Path(run_id).name != run_id:
        raise ValueError("Недопустимый идентификатор запуска.")
    folder = (root / run_id).resolve()
    if folder.parent != root or not folder.is_dir():
        raise FileNotFoundError("Папка запуска не найдена.")
    return folder


def load_run_text(run_id, filename):
    if filename not in HISTORY_FILES and filename not in {"run_log.json", "00_pipeline.json"}:
        raise ValueError("Этот файл не входит в результаты запуска.")
    folder = get_run_dir(run_id)
    path = folder / filename
    if path.resolve().parent != folder:
        raise ValueError("Файл находится за пределами папки запуска.")
    return path.read_text(encoding="utf-8-sig")


def load_run_log(run_id):
    data = json.loads(load_run_text(run_id, "run_log.json"))
    if not isinstance(data, dict):
        raise ValueError("Журнал должен содержать JSON-объект.")
    return data


def save_run_log(run_dir, data):
    path = Path(run_dir) / "run_log.json"
    old = {}
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
    return save_file(run_dir, "run_log.json", json.dumps(log, ensure_ascii=False, indent=2) + "\n")


@contextmanager
def run_lock(run_dir):
    # Advisory locks are released by the OS if the process exits.
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
            raise RuntimeError("Этот запуск уже выполняется в другом процессе или сеансе.") from exc
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


def list_runs():
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
        except (OSError, ValueError) as exc:
            warnings.append(f"Журнал недоступен: {exc}")
            log = {}
        project = str(log.get("project_name") or "").strip()
        if not project:
            try:
                lines = load_run_text(folder.name, "00_brief.md").splitlines()
                for i, line in enumerate(lines):
                    if line.strip().startswith("ПРОЕКТ:"):
                        project = line.partition(":")[2].strip() or next((s.strip() for s in lines[i+1:] if s.strip()), "")
                        break
            except (OSError, ValueError):
                pass
        errors = log.get("errors", [])
        runs.append({
            "run_id": folder.name, "project_name": project or "Без названия",
            "created_at": str(log.get("created_at") or ""),
            "status": str(log.get("status") or "unknown"),
            "model": str(log.get("model") or "Не указана"),
            "errors": errors if isinstance(errors, list) else [str(errors)],
            "warnings": warnings,
        })
    return sorted(runs, key=lambda row: row["run_id"], reverse=True)
