"""Главная страница ИИ-проектного офиса."""
import streamlit as st
from storage import list_runs
from knowledge import list_knowledge_files
from history_view import open_run, archive_bytes, stage_summary
from functools import partial

NAV = ["🏠 Обзор", "🚀 Новый запуск", "🕘 История запусков", "📚 База знаний"]


def go_to(label):
    st.session_state.main_tab = label


def render_overview():
    heading, action = st.columns([3, 1])
    heading.subheader("Рабочий обзор")
    action.button("＋ Новый запуск", type="primary", use_container_width=True,
                  key="home_new", on_click=go_to, args=(NAV[1],))
    try:
        rows = list_runs()
    except (OSError, ValueError) as exc:
        st.error(f"Не удалось прочитать историю запусков: {exc}")
        rows = None
    if rows is not None:
        completed = [r for r in rows if r['status'] == 'completed']
        attention = [r for r in rows if r['status'] == 'failed' or r['warnings'] or r['status'] == 'unknown']
        counters = st.columns(3)
        counters[0].metric("Всего запусков", len(rows), border=True)
        counters[1].metric("Завершены", len(completed), border=True)
        counters[2].metric("Требуют проверки", len(attention), border=True)
        running = sum(r['status'] == 'running' for r in rows)
        if running:
            st.caption(f"В журнале помечены как выполняющиеся: {running}. Это последний сохранённый статус, не проверка активности процесса.")
        if not rows:
            with st.container(border=True):
                st.subheader("Подготовьте первый проектный документ")
                st.write("Опишите задачу: агенты подготовят план, анализ, архитектуру, документ и проверку.")
                st.button("Создать первый запуск", key="home_first", on_click=go_to, args=(NAV[1],))
                st.caption("Базу знаний можно добавить позже: она не обязательна для первого запуска.")
        if attention:
            st.subheader("Требуют внимания")
            for row in attention[:3]:
                run_id = row['run_id']
                count, stage = stage_summary(run_id)
                with st.container(border=True):
                    st.write(row['project_name'])
                    label = "Ошибка" if row['status'] == 'failed' else "Нужно проверить журнал"
                    st.caption(f"{label}{(': ' + stage) if stage else ''} · Сохранено {count}/5 этапов · {run_id}")
                    if row['errors']:
                        try:
                            from report_view import explain_error
                            cause, _ = explain_error(str(row['errors'][-1]))
                        except ImportError:
                            cause = "Есть записанная ошибка. Причина доступна в отчёте."
                        st.write("Последняя причина: " + cause)
                    elif row['warnings']:
                        st.write("Журнал отсутствует или не удалось прочитать его содержимое.")
                    left, right = st.columns(2)
                    if left.button("Продолжить", key="home_resume_" + run_id, use_container_width=True):
                        open_run(run_id, 'resume')
                    if right.button("Открыть отчёт", key="home_attention_report_" + run_id, use_container_width=True):
                        open_run(run_id, 'report')
            if len(attention) > 3:
                st.caption(f"Показаны 3 из {len(attention)}. Остальные доступны в истории.")
        if rows:
            title, link = st.columns([3, 1])
            title.subheader("Последние запуски")
            link.button("Вся история →", key="home_all", on_click=go_to, args=(NAV[2],), use_container_width=True)
            header = st.columns([1.5, 3, 2, 0.7, 1])
            for col, label in zip(header, ["Дата", "Проект / отчёт", "Статус", "Готово", "Архив"]):
                col.write(label)
            labels = {'completed':'Завершён', 'failed':'Ошибка', 'running':'По журналу: выполняется', 'unknown':'Нет статуса'}
            for row in rows[:5]:
                run_id = row['run_id']
                count, stage = stage_summary(run_id)
                cols = st.columns([1.5, 3, 2, 0.7, 1])
                cols[0].write(str(row['created_at'] or run_id[4:]) .replace('T', ' ')[:16])
                if cols[1].button(row['project_name'], key="home_report_" + run_id, use_container_width=True):
                    open_run(run_id, 'report')
                status = labels.get(row['status'], 'Нет статуса')
                if row['status'] == 'failed' and stage:
                    status += ': ' + stage
                cols[2].write(status)
                cols[3].write(f'{count}/5')
                cols[4].download_button('ZIP', data=partial(archive_bytes, run_id), file_name=run_id+'.zip',
                                        mime='application/zip', key='home_zip_'+run_id, on_click='ignore', use_container_width=True)
            st.caption("Архивы могут содержать внутренние документы. Скачивание не запускает агентов.")
    st.divider()
    st.subheader("База знаний")
    try:
        files = list_knowledge_files()
    except (OSError, ValueError) as exc:
        st.warning(f"Не удалось прочитать базу знаний: {exc}")
    else:
        st.write(f"Документов: {len(files)}")
        if not files:
            st.caption("Добавьте FAQ, регламенты и описание продукта, если хотите использовать их как источники.")
        else:
            st.caption("При запуске поиск автоматически подбирает подходящие материалы. Наличие документов не гарантирует совпадение с брифом.")
    st.button("Открыть базу знаний / добавить документы", key="home_knowledge", on_click=go_to, args=(NAV[3],))
