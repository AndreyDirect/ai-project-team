"""База знаний с поиском, атрибутами, корзиной и восстановлением.

Активные документы: knowledge/
Корзина: knowledge_trash/
Метаданные: knowledge_meta.json
"""

from __future__ import annotations

import json
import os
import tempfile
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from config import ROOT

KNOWLEDGE_DIR = ROOT / "knowledge"
TRASH_DIR = ROOT / "knowledge_trash"
META_PATH = ROOT / "knowledge_meta.json"

MAX_FILE_CHARS = 4000
MAX_RESULTS = 5
ALLOWED_EXTENSIONS = {".txt", ".md"}


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


def _safe_name(filename: str) -> str:
    name = Path(filename.replace("\\", "/")).name.strip()
    if not name:
        raise ValueError("Пустое имя файла")
    if Path(name).suffix.lower() not in ALLOWED_EXTENSIONS:
        raise ValueError("Допускаются только файлы TXT и MD")
    if name in {".", ".."} or "/" in name or "\\" in name:
        raise ValueError("Недопустимое имя файла")
    return name


def _load_meta() -> dict[str, Any]:
    if not META_PATH.exists():
        return {"documents": {}}
    try:
        data = json.loads(META_PATH.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return {"documents": {}}
    if not isinstance(data, dict) or not isinstance(data.get("documents"), dict):
        return {"documents": {}}
    return data


def _save_meta(meta: dict[str, Any]) -> None:
    ROOT.mkdir(parents=True, exist_ok=True)
    tmp = None
    try:
        with tempfile.NamedTemporaryFile(
            "w", encoding="utf-8", dir=ROOT,
            prefix=".knowledge_meta_", suffix=".tmp", delete=False,
        ) as f:
            tmp = Path(f.name)
            json.dump(meta, f, ensure_ascii=False, indent=2)
            f.write("\n")
        os.replace(tmp, META_PATH)
    finally:
        if tmp:
            tmp.unlink(missing_ok=True)


def _document_meta(meta: dict[str, Any], filename: str) -> dict[str, Any]:
    return meta["documents"].setdefault(filename, {
        "author": "Не указан",
        "created_at": _now(),
        "updated_at": _now(),
        "deleted": False,
        "deleted_at": None,
    })


def save_uploaded_file(
    filename: str,
    content: str,
    author: str = "Не указан",
) -> Path:
    """Создаёт или обновляет активный документ и его метаданные."""
    name = _safe_name(filename)
    if not content.strip():
        raise ValueError("Пустой документ не сохранён")

    KNOWLEDGE_DIR.mkdir(parents=True, exist_ok=True)
    path = KNOWLEDGE_DIR / name
    path.write_text(content, encoding="utf-8")

    meta = _load_meta()
    doc = _document_meta(meta, name)

    if doc.get("deleted"):
        doc["deleted"] = False
        doc["deleted_at"] = None

    if doc.get("author") in {None, "", "Не указан"}:
        doc["author"] = author or "Не указан"

    doc["updated_at"] = _now()
    _save_meta(meta)
    return path


def get_document_meta(filename: str) -> dict[str, Any]:
    meta = _load_meta()
    return meta["documents"].get(filename, {
        "author": "Не указан",
        "created_at": None,
        "updated_at": None,
        "deleted": False,
        "deleted_at": None,
    })


def list_knowledge_files(include_deleted: bool = False) -> list[dict[str, Any]]:
    """Возвращает документы с атрибутами.

    По умолчанию — только активные документы.
    """
    meta = _load_meta()
    result = []

    for folder, deleted_flag in ((KNOWLEDGE_DIR, False), (TRASH_DIR, True)):
        if not folder.exists():
            continue
        for path in sorted(list(folder.glob("*.txt")) + list(folder.glob("*.md"))):
            doc = meta["documents"].get(path.name, {})
            is_deleted = doc.get("deleted", False) or deleted_flag
            if is_deleted != deleted_flag:
                continue
            if include_deleted is False and is_deleted:
                continue

            try:
                content = path.read_text(encoding="utf-8-sig")
            except (OSError, UnicodeError):
                content = ""

            result.append({
                "name": path.name,
                "path": path,
                "content": content,
                "author": doc.get("author", "Не указан"),
                "created_at": doc.get("created_at"),
                "updated_at": doc.get("updated_at"),
                "deleted": is_deleted,
                "deleted_at": doc.get("deleted_at"),
            })

    return result


def search_knowledge(query: str) -> str:
    """Ищет по активным документам и возвращает наиболее релевантные фрагменты."""
    if not query or not query.strip():
        return ""

    documents = list_knowledge_files(include_deleted=False)
    if not documents:
        return ""

    words = [w.lower() for w in query.split() if len(w) > 2]
    if not words:
        return ""

    scored = []
    for doc in documents:
        content = doc["content"]
        content_lower = content.lower()
        score = sum(1 for word in words if word in content_lower)

        title_score = sum(1 for word in words if word in doc["name"].lower())
        total_score = score + title_score * 2

        if total_score > 0:
            scored.append((total_score, doc))

    if not scored:
        return ""

    scored.sort(key=lambda item: item[0], reverse=True)

    parts = []
    for score, doc in scored[:MAX_RESULTS]:
        short_content = doc["content"][:MAX_FILE_CHARS]
        parts.append(
            f"### Источник: {doc['name']}\n"
            f"Автор: {doc['author']}\n\n"
            f"{short_content}"
        )

    return "\n\n---\n\n".join(parts)


def move_to_trash(filename: str) -> Path:
    """Перемещает документ в корзину. Документ можно восстановить."""
    name = _safe_name(filename)
    source = KNOWLEDGE_DIR / name
    if not source.exists():
        raise ValueError(f"Документ не найден: {name}")

    TRASH_DIR.mkdir(parents=True, exist_ok=True)
    target = TRASH_DIR / name

    counter = 1
    while target.exists():
        target = TRASH_DIR / f"{Path(name).stem}_{counter}{Path(name).suffix}"
        counter += 1

    source.rename(target)

    meta = _load_meta()
    doc = _document_meta(meta, name)
    doc["deleted"] = True
    doc["deleted_at"] = _now()
    _save_meta(meta)

    return target


def restore_from_trash(filename: str) -> Path:
    """Восстанавливает документ из корзины."""
    name = _safe_name(filename)
    source = TRASH_DIR / name
    if not source.exists():
        raise ValueError(f"Документ в корзине не найден: {name}")

    KNOWLEDGE_DIR.mkdir(parents=True, exist_ok=True)
    target = KNOWLEDGE_DIR / name

    counter = 1
    while target.exists():
        target = KNOWLEDGE_DIR / f"{Path(name).stem}_restored_{counter}{Path(name).suffix}"
        counter += 1

    source.rename(target)

    meta = _load_meta()
    doc = _document_meta(meta, target.name)
    doc["deleted"] = False
    doc["deleted_at"] = None
    doc["updated_at"] = _now()
    _save_meta(meta)

    return target


def delete_forever(filename: str) -> None:
    """Окончательно удаляет документ из корзины. Восстановление невозможно."""
    name = _safe_name(filename)
    path = TRASH_DIR / name

    if not path.exists():
        # Разрешаем окончательное удаление активного документа только по явному запросу.
        active = KNOWLEDGE_DIR / name
        if active.exists():
            active.unlink(missing_ok=True)
        else:
            raise ValueError(f"Документ не найден: {name}")
    else:
        path.unlink(missing_ok=True)

    meta = _load_meta()
    meta["documents"].pop(name, None)
    _save_meta(meta)
