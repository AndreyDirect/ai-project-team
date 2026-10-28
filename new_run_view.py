"""Новый запуск: компактный бриф, прогресс и переход к отчёту."""
from pathlib import Path
import streamlit as st
from config import MODEL
from knowledge import list_knowledge_files
from pipeline import start_project
from storage import load_run_log
from history_view import open_run, archive_bytes
from functools import partial

STEPS = [("plan", "План"), ("analysis", "Анализ"),
         ("architecture", "Архитектура"), ("document", "Итоговый документ"),
         ("review", "Проверка")]


def build_brief(values):
    return "\n\n".join(f"{label}:\n{str(value).strip()}" for label, value in values if str(value).strip())


def draw_progress(placeholder, completed, active=None, failed=None):
    with placeholder.container():
        st.progress(len(completed) / len(STEPS), text=f"Сохранено {len(completed)} из {len(STEPS)} этапов")
        lines = []
        for key, label in STEPS:
            if key in completed:
                lines.append(f"✅ {label} — сохранён")
            elif key == failed:
                lines.append(f"❌ {label} — ошибка")
            elif key == active:
                lines.append(f"⏳ {label} — выполняется")
            else:
                lines.append(f"○ {label} — ожидает")
        st.markdown("  \n".join(lines))


def go_history():
    st.session_state.main_tab = "🕘 История запусков"


def remember_folder(folder):
    st.session_state.run_dir = folder


def render_new_run(feedback_renderer=None):
    st.subheader("Новый запуск")
    st.caption("Опишите задачу и ожидаемый документ. Дополнительные условия можно уточнить в раскрывающемся блоке.")
    with st.form("new_run_brief_form", clear_on_submit=False):
        project = st.text_input("Название проекта *", placeholder="ИИ-ассистент для менеджеров по продажам", key="new_project")
        kind = st.selectbox("Тип результата", ["Концепция решения", "Техническое задание", "Анализ", "Другое"],
                            help="Подсказка для брифа. Используется та же команда из пяти агентов.", key="new_kind")
        goal = st.text_area("Бизнес-цель *", placeholder="Какой результат нужен компании?", height=100, key="new_goal")
        problem = st.text_area("Текущая проблема", placeholder="Как устроен процесс сейчас и что нужно изменить?", height=100, key="new_problem")
        desired = st.text_area("Что должно быть в результате *", placeholder="Например: концепция MVP со сценариями, функциями, рисками и метриками.", height=120, key="new_desired")
        with st.expander("Пользователи, ограничения и критерии успеха"):
            user = st.text_area("Целевой пользователь", key="new_user")
            constraints = st.text_area("Ограничения и запреты", placeholder="Бюджет, сроки, требования к данным, запрещённые действия.", key="new_constraints")
            criteria = st.text_area("Критерии успеха", key="new_criteria")
            additional = st.text_area("Дополнительный контекст", key="new_additional")
        st.markdown("#### Параметры запуска")
        st.write("Модель: " + str(MODEL))
        st.caption("План → анализ → архитектура → итоговый документ → проверка")
        try:
            files = list_knowledge_files()
            st.caption(f"Источники: автоматический поиск по базе знаний. Документов в базе: {len(files)}. Используются только найденные подходящие материалы.")
        except Exception as exc:
            st.warning(f"Не удалось проверить базу знаний: {exc}")
        st.caption("Результаты сохраняются в истории. Запуск отправляет запросы в Groq и может расходовать API-квоту. При ошибке готовые этапы останутся для продолжения.")
        st.caption("* Обязательные поля. Тип результата не заменяет описание ожидаемого документа.")
        submitted = st.form_submit_button("🚀 Запустить подготовку", type="primary", use_container_width=True)

    progress_area = st.empty()
    if submitted:
        errors = []
        if not project.strip():
            errors.append("Укажите название — оно будет отображаться в истории.")
        if not goal.strip():
            errors.append("Опишите бизнес-цель.")
        if not desired.strip():
            errors.append("Уточните, какой документ или результат нужно подготовить.")
        if errors:
            for error in errors:
                st.error(error)
        else:
            brief = build_brief([
                ("ПРОЕКТ", project), ("ТИП РЕЗУЛЬТАТА", kind), ("БИЗНЕС-ЦЕЛЬ", goal),
                ("ЦЕЛЕВОЙ ПОЛЬЗОВАТЕЛЬ", user), ("ТЕКУЩАЯ ПРОБЛЕМА", problem),
                ("ЖЕЛАЕМЫЙ РЕЗУЛЬТАТ", desired), ("ОГРАНИЧЕНИЯ", constraints),
                ("КРИТЕРИИ УСПЕХА", criteria), ("ДОПОЛНИТЕЛЬНЫЙ КОНТЕКСТ", additional),
            ])
            st.session_state.result = {}
            st.session_state.run_dir = None
            draw_progress(progress_area, set())
            def progress(number, label):
                done = {key for key, _ in STEPS[:number-1]}
                draw_progress(progress_area, done, active=STEPS[number-1][0])
            try:
                outcome = start_project(brief, project.strip(), progress=progress, on_created=remember_folder)
            except Exception as exc:
                st.error(f"Ошибка подготовки или сохранения запуска: {exc}")
                if st.session_state.run_dir:
                    st.caption("Папка уже создана. Проверьте запуск через историю.")
                progress_area.empty()
            else:
                st.session_state.result = outcome['result']
                st.session_state.run_dir = outcome['run_dir']
                completed = set(outcome['result'])
                failed = next((key for key, _ in STEPS if key not in completed), None)
                draw_progress(progress_area, completed, failed=failed if outcome['status'] != 'completed' else None)
                if outcome['status'] != 'completed':
                    try:
                        from report_view import explain_error
                        reason, action = explain_error(outcome.get('error') or '')
                    except ImportError:
                        reason, action = 'Запуск прерван.', 'Откройте отчёт или историю для подробностей.'
                    st.error(reason)
                    st.write(action)
                    with st.expander("Технические детали"):
                        st.text(outcome.get('error') or 'Ошибка не указана.')
    folder = st.session_state.get('run_dir')
    if folder:
        run_id = Path(folder).name
        try:
            log = load_run_log(run_id)
        except (OSError, ValueError) as exc:
            st.warning(f"Журнал последнего запуска недоступен: {exc}")
            st.button("Открыть историю", key="new_run_history_missing", on_click=go_history)
            return
        st.divider()
        st.subheader("Последний открытый запуск")
        st.write(str(log.get('project_name') or run_id))
        st.caption("ID: " + run_id)
        if log.get('status') == 'completed':
            st.success("Подготовка завершена. Результаты сохранены в истории.")
        elif log.get('status') == 'failed':
            st.warning("Запуск завершился ошибкой. Сохранённые результаты доступны; продолжить можно из истории.")
        else:
            st.info("Последний сохранённый статус: " + str(log.get('status') or 'не указан'))
        report, archive, history = st.columns(3)
        if report.button("Открыть отчёт", key="new_run_report", type="primary", use_container_width=True):
            open_run(run_id, 'report')
        archive.download_button("Скачать запуск · ZIP", data=partial(archive_bytes, run_id),
                                file_name=run_id+'.zip', mime='application/zip', on_click='ignore',
                                key="new_run_zip", use_container_width=True)
        history.button("Открыть историю", key="new_run_history", on_click=go_history, use_container_width=True)
        if feedback_renderer and st.session_state.get('result', {}).get('document'):
            with st.expander("Обратная связь — необязательно"):
                feedback_renderer(folder)
