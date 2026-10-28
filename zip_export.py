"""Экспорт одного запуска. Не вызывает модель и не меняет результаты."""
import io
import json
import zipfile
from datetime import datetime, timezone
from pathlib import Path

import streamlit as st
from storage import get_run_dir, HISTORY_FILES

MAX_EXPORT_BYTES = 50 * 1024 * 1024
EXTRA_FILES = ("run_log.json", "00_pipeline.json", "feedback.json")


def build_run_archive(run_id):
    folder = get_run_dir(run_id)
    names = list(dict.fromkeys([*HISTORY_FILES, *EXTRA_FILES]))
    included, missing, skipped = [], [], []
    total = 0
    buffer = io.BytesIO()
    with zipfile.ZipFile(buffer, "w", compression=zipfile.ZIP_DEFLATED) as archive:
        for name in names:
            if Path(name).name != name:
                skipped.append({"file": name, "reason": "Недопустимое имя"})
                continue
            path = folder / name
            if path.is_symlink() or path.resolve().parent != folder.resolve():
                skipped.append({"file": name, "reason": "Ссылки на файлы не экспортируются"})
                continue
            if not path.exists():
                missing.append(name)
                continue
            if not path.is_file():
                skipped.append({"file": name, "reason": "Не является обычным файлом"})
                continue
            try:
                if total + path.stat().st_size > MAX_EXPORT_BYTES:
                    raise ValueError("Размер материалов превышает лимит экспорта 50 МиБ. Скачайте документы по отдельности.")
                with path.open("rb") as handle:
                    content = handle.read(MAX_EXPORT_BYTES - total + 1)
                if total + len(content) > MAX_EXPORT_BYTES:
                    raise ValueError("Размер материалов превышает лимит экспорта 50 МиБ.")
                total += len(content)
                archive.writestr(f"{run_id}/{name}", content)
                included.append(name)
            except OSError as exc:
                skipped.append({"file": name, "reason": f"Не удалось прочитать: {type(exc).__name__}"})
        if not included:
            raise ValueError("В этом запуске нет доступных файлов для экспорта.")
        manifest = {
            "run_id": run_id,
            "exported_at": datetime.now(timezone.utc).isoformat(),
            "included": included, "missing": missing, "skipped": skipped,
            "note": "Экспорт одного запуска, не резервная копия всей системы. Материалы могут содержать внутренние данные.",
        }
        archive.writestr(f"{run_id}/export_manifest.json", json.dumps(manifest, ensure_ascii=False, indent=2))
    return buffer.getvalue(), manifest


def show_run_export(run_id):
    st.caption("Архив может содержать внутренние документы, инструкции агентов и комментарии. Передавайте его только уполномоченным получателям.")
    st.caption("Во время подготовки архива не продолжайте этот же запуск в другом сеансе: экспорт не является транзакционным снимком.")
    if st.button("📦 Подготовить ZIP всего запуска", key="prepare_zip_" + run_id):
        try:
            with st.spinner("Подготовка архива..."):
                content, manifest = build_run_archive(run_id)
        except (OSError, ValueError) as exc:
            st.session_state.pop("run_zip_export", None)
            st.error(f"Не удалось подготовить ZIP: {exc}")
        else:
            # Keep only one archive per session to limit memory usage.
            st.session_state.run_zip_export = {
                "run_id": run_id, "content": content, "manifest": manifest,
            }
    prepared = st.session_state.get("run_zip_export")
    if prepared and prepared.get("run_id") == run_id:
        manifest = prepared['manifest']
        st.download_button(
            "⬇️ Скачать ZIP всего запуска", data=prepared['content'],
            file_name=run_id + ".zip", mime="application/zip",
            key="download_zip_" + run_id, on_click="ignore",
        )
        st.caption(f"Файлов: {len(manifest['included'])}. Подготовлен: {manifest['exported_at']} (UTC). Для обновления нажмите «Подготовить ZIP» повторно.")
        if manifest['skipped']:
            st.warning("Некоторые файлы пропущены. Причины перечислены в export_manifest.json внутри архива.")
