"""Центральная конфигурация ИИ-проектного офиса."""

from __future__ import annotations

import os
from pathlib import Path

# Корневая папка проекта.
ROOT = Path(__file__).parent

# Модель по умолчанию для старых модулей и запросов без явного выбора модели.
# В новом интерфейсе модель выбирается в карточке ИИ-агента и передаётся в llm.ask().
DEFAULT_MODEL = "llama-3.3-70b-versatile"
MODEL = os.getenv("GROQ_MODEL", DEFAULT_MODEL)

# Файл брифа из первой версии.
BRIEF_FILE = ROOT / "project_brief.txt"

# Папка результатов и истории запусков.
OUTPUT_DIR = ROOT / "output"

# Папки базы знаний.
KNOWLEDGE_DIR = ROOT / "knowledge"
KNOWLEDGE_TRASH_DIR = ROOT / "knowledge_trash"
KNOWLEDGE_META_FILE = ROOT / "knowledge_meta.json"

# Хранилище проектного офиса версии 2.
PROJECT_OFFICE_DATA_DIR = ROOT / "data"
PROJECT_OFFICE_DB_FILE = PROJECT_OFFICE_DATA_DIR / "project_office.json"

# Поведение запросов к Groq.
RETRY_DELAYS = [5, 15, 45]
TEMPERATURE = 0.3
MAX_TOKENS = None

# Ограничения автономной команды по умолчанию.
DEFAULT_MAX_REVISION_CYCLES = 2
DEFAULT_MAX_AGENT_TURNS = 12
