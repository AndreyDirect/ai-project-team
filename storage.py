from pathlib import Path
from datetime import datetime

from config import BRIEF_FILE, OUTPUT_DIR


def read_brief() -> str:
    if not BRIEF_FILE.exists():
        raise FileNotFoundError(
            "Не найден файл project_brief.txt. "
            "Создайте его в папке проекта и опишите там задачу."
        )

    return BRIEF_FILE.read_text(encoding="utf-8")


def create_run_dir() -> Path:
    run_id = datetime.now().strftime("%Y-%m-%d_%H-%M-%S")
    run_dir = OUTPUT_DIR / f"run_{run_id}"
    run_dir.mkdir(parents=True, exist_ok=True)
    return run_dir


def save_file(run_dir: Path, filename: str, content: str) -> Path:
    path = run_dir / filename
    path.write_text(content, encoding="utf-8")
    return path