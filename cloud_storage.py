"""Облачное хранилище db в Supabase (одна строка JSON в таблице app_state).

Быстрая версия: чтение из Supabase кэшируется на 5 секунд,
при сетевом сбое — 2 попытки, затем бесшумный переход на локальный JSON.
"""

from __future__ import annotations

import json
import os
import time
from datetime import datetime, timezone
from pathlib import Path

import streamlit as st

DATA_PATH = Path("data/project_office.json")
STATE_ID = "main"

_client = None


def _get_client():
    """Лениво создаёт клиент Supabase. Если ключей нет — вернёт None."""
    global _client
    if _client is not None:
        return _client
    url = os.environ.get("SUPABASE_URL")
    key = os.environ.get("SUPABASE_SECRET_KEY") or os.environ.get("SUPABASE_KEY")
    if not url or not key:
        return None
    try:
        from supabase import create_client
        _client = create_client(url, key)
    except Exception as exc:
        st.warning(f"Supabase недоступен, работаем с локальным JSON: {exc}")
        _client = None
    return _client


@st.cache_data(ttl=5, show_spinner=False)
def _fetch_remote() -> dict | None:
    """Читает db из Supabase с кэшем на 5 секунд и 2 повторами при сбое сети."""
    client = _get_client()
    if client is None:
        return None
    for attempt in range(2):
        try:
            row = client.table("app_state").select("data").eq("id", STATE_ID).execute()
            rows = row.data or []
            return rows[0]["data"] if rows else None
        except Exception:
            if attempt == 0:
                time.sleep(1)  # пауза и повтор при разрыве соединения
    return None


def load_db() -> dict:
    """
    Читает db из Supabase (с кэшем). Если там пусто — берёт локальный JSON
    и сразу заливает его в облако (первичная миграция).
    Если облако недоступно — работает с локальным JSON без предупреждений.
    """
    remote = _fetch_remote()
    if remote is not None:
        return remote
    # В облаке пусто или ошибка сети — мигрируем/читаем локальный JSON
    local = _load_local()
    save_db(local)
    return local


def save_db(db: dict) -> None:
    """Сохраняет db в Supabase и (для подстраховки) в локальный JSON."""
    st.cache_data.clear()  # сброс кэша, чтобы свежие данные сразу были видны
    client = _get_client()
    payload = {
        "id": STATE_ID,
        "data": db,
        "updated_at": datetime.now(timezone.utc).isoformat(),
    }
    if client is not None:
        try:
            client.table("app_state").upsert(payload).execute()
        except Exception as exc:
            st.warning(f"Не удалось сохранить в Supabase: {exc}")
    _save_local(db)


def _load_local() -> dict:
    if DATA_PATH.exists():
        try:
            return json.loads(DATA_PATH.read_text(encoding="utf-8"))
        except Exception:
            pass
    return {"users": {}, "projects": {}, "tasks": {}, "events": []}


def _save_local(db: dict) -> None:
    DATA_PATH.parent.mkdir(parents=True, exist_ok=True)
    DATA_PATH.write_text(
        json.dumps(db, ensure_ascii=False, indent=2), encoding="utf-8"
    )
