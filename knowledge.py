from pathlib import Path

from config import ROOT

KNOWLEDGE_DIR = ROOT / "knowledge"
MAX_FILE_CHARS = 4000
MAX_RESULTS = 3


def save_uploaded_file(filename: str, content: str) -> Path:
    KNOWLEDGE_DIR.mkdir(exist_ok=True)
    path = KNOWLEDGE_DIR / filename
    path.write_text(content, encoding="utf-8")
    return path


def list_knowledge_files():
    if not KNOWLEDGE_DIR.exists():
        return []

    files = list(KNOWLEDGE_DIR.glob("*.txt")) + list(KNOWLEDGE_DIR.glob("*.md"))
    return sorted(files)


def search_knowledge(query: str) -> str:
    if not KNOWLEDGE_DIR.exists():
        return ""

    files = list_knowledge_files()

    if not files:
        return ""

    query_words = [
        word.lower()
        for word in query.split()
        if len(word) > 2
    ]

    scored = []

    for file in files:
        try:
            content = file.read_text(encoding="utf-8")
        except Exception:
            continue

        content_lower = content.lower()

        score = sum(
            1 for word in query_words
            if word in content_lower
        )

        if score > 0:
            scored.append((score, file.name, content))

    if not scored:
        return ""

    scored.sort(reverse=True, key=lambda item: item[0])
    selected = scored[:MAX_RESULTS]

    parts = []

    for score, filename, content in selected:
        short_content = content[:MAX_FILE_CHARS]
        parts.append(
            f"### Источник: {filename}\n\n{short_content}"
        )

    return "\n\n---\n\n".join(parts)