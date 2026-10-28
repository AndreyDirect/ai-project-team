import streamlit as st

from config import MODEL
from prompts import (
    PM_ROLE,
    PM_TASK,
    ANALYST_ROLE,
    ANALYST_TASK,
    ARCHITECT_ROLE,
    ARCHITECT_TASK,
    WRITER_ROLE,
    WRITER_TASK,
    REVIEWER_ROLE,
    REVIEWER_TASK,
)
from storage import create_run_dir, save_file
from llm import ask


# ==============================
# СТРАНИЦА
# ==============================

st.set_page_config(
    page_title="AI Project Team",
    page_icon="🤖",
    layout="wide",
    initial_sidebar_state="collapsed"
)

st.title("🤖 AI Project Team")
st.caption(
    "Заполните бриф проекта. Команда ИИ-агентов подготовит план, анализ, "
    "архитектуру, итоговый документ и проверку."
)


# ==============================
# СОСТОЯНИЕ
# ==============================

if "result" not in st.session_state:
    st.session_state.result = None

if "run_dir" not in st.session_state:
    st.session_state.run_dir = None


# ==============================
# ФОРМА ПРОЕКТА
# ==============================

st.subheader("Бриф проекта")

with st.form("project_form", clear_on_submit=False):

    st.markdown("#### Основное")

    project_name = st.text_input(
        "Название проекта",
        placeholder="Например: ИИ-ассистент для менеджеров по продажам",
        help="Короткое понятное название инициативы."
    )

    business_goal = st.text_area(
        "Бизнес-цель",
        height=120,
        placeholder=(
            "Какой результат нужен компании?\n\n"
            "Например: сократить время подготовки ответов менеджерам "
            "и повысить качество коммуникации с клиентами."
        ),
        help="Опишите измеримый результат: время, деньги, конверсия, качество."
    )

    target_user = st.text_area(
        "Целевой пользователь",
        height=110,
        placeholder=(
            "Кто будет пользоваться результатом?\n\n"
            "Например: менеджер по продажам, который ежедневно отвечает "
            "на типовые вопросы клиентов."
        ),
        help="Укажите конкретную роль: менеджер, руководитель, клиент, оператор."
    )

    st.markdown("#### Проблема и результат")

    problem = st.text_area(
        "Какая проблема есть сейчас",
        height=140,
        placeholder=(
            "Опишите текущий процесс и его слабые места.\n\n"
            "Например: менеджеры тратят 10–15 минут на поиск информации "
            "и подготовку ответа, часто копируют старые письма."
        ),
        help="Что происходит сейчас, сколько времени занимает и где возникают ошибки."
    )

    desired_result = st.text_area(
        "Какой результат должен получиться",
        height=140,
        placeholder=(
            "Что конкретно должна создать команда агентов?\n\n"
            "Например: документ с концепцией MVP: проблема, пользователи, "
            "сценарии, функции MVP, риски и метрики."
        ),
        help="Например: концепция, ТЗ, прототип, анализ, отчёт, архитектура."
    )

    st.markdown("#### Ограничения и критерии")

    left, right = st.columns(2)

    with left:
        constraints = st.text_area(
            "Ограничения и запреты",
            height=150,
            placeholder=(
                "Например:\n"
                "- Не предлагать интеграций с внешними системами.\n"
                "- Не выдумывать данные о компании.\n"
                "- Писать на русском языке."
            ),
            help="Стек, бюджет, сроки, запрещённые действия, требования к данным."
        )

    with right:
        success_criteria = st.text_area(
            "Критерии успеха",
            height=150,
            placeholder=(
                "Например:\n"
                "- Есть минимум 3 сценария использования.\n"
                "- Есть MVP из 3–5 функций.\n"
                "- Есть измеримая метрика эффекта."
            ),
            help="По каким признакам результат считается хорошим."
        )

    additional = st.text_area(
        "Дополнительный контекст",
        height=120,
        placeholder=(
            "Необязательно.\n\n"
            "Например: используем Bitrix24, есть база FAQ, клиенты чаще всего "
            "спрашивают про сроки и оплату."
        ),
        help="Продукты, системы, данные, примеры, важные особенности."
    )

    submitted = st.form_submit_button(
        "🚀 Отправить проект агентам",
        use_container_width=True,
        type="primary"
    )


# ==============================
# СБОРКА БРИФА
# ==============================

def build_brief():
    parts = []

    if project_name.strip():
        parts.append(f"ПРОЕКТ: {project_name.strip()}")

    if business_goal.strip():
        parts.append(f"БИЗНЕС-ЦЕЛЬ:\n{business_goal.strip()}")

    if target_user.strip():
        parts.append(f"ЦЕЛЕВОЙ ПОЛЬЗОВАТЕЛЬ:\n{target_user.strip()}")

    if problem.strip():
        parts.append(f"ТЕКУЩАЯ ПРОБЛЕМА:\n{problem.strip()}")

    if desired_result.strip():
        parts.append(f"ЖЕЛАЕМЫЙ РЕЗУЛЬТАТ:\n{desired_result.strip()}")

    if constraints.strip():
        parts.append(f"ОГРАНИЧЕНИЯ:\n{constraints.strip()}")

    if success_criteria.strip():
        parts.append(f"КРИТЕРИИ УСПЕХА:\n{success_criteria.strip()}")

    if additional.strip():
        parts.append(f"ДОПОЛНИТЕЛЬНЫЙ КОНТЕКСТ:\n{additional.strip()}")

    return "\n\n".join(parts)


# ==============================
# ЗАПУСК АГЕНТОВ
# ==============================

if submitted:
    brief = build_brief()

    if not brief.strip():
        st.error("Заполните хотя бы название проекта и бизнес-цель.")
        st.stop()

    run_dir = create_run_dir()
    st.session_state.run_dir = run_dir

    result = {}

    try:
        with st.spinner("1/5 — Project Manager составляет план..."):
            plan = ask(
                PM_ROLE,
                f"{PM_TASK}\n\nПРОЕКТ:\n{brief}"
            )

        save_file(run_dir, "01_plan.md", plan)
        result["plan"] = plan

        with st.spinner("2/5 — Analyst анализирует проект..."):
            analysis = ask(
                ANALYST_ROLE,
                f"{ANALYST_TASK}\n\n"
                f"ПРОЕКТ:\n{brief}\n\n"
                f"ПЛАН:\n{plan}"
            )

        save_file(run_dir, "02_analysis.md", analysis)
        result["analysis"] = analysis

        with st.spinner("3/5 — Architect проектирует решение..."):
            architecture = ask(
                ARCHITECT_ROLE,
                f"{ARCHITECT_TASK}\n\n"
                f"ПРОЕКТ:\n{brief}\n\n"
                f"ПЛАН:\n{plan}\n\n"
                f"АНАЛИЗ:\n{analysis}"
            )

        save_file(run_dir, "03_architecture.md", architecture)
        result["architecture"] = architecture

        with st.spinner("4/5 — Writer собирает итоговый документ..."):
            document = ask(
                WRITER_ROLE,
                f"{WRITER_TASK}\n\n"
                f"ПРОЕКТ:\n{brief}\n\n"
                f"ПЛАН:\n{plan}\n\n"
                f"АНАЛИЗ:\n{analysis}\n\n"
                f"АРХИТЕКТУРА:\n{architecture}"
            )

        save_file(run_dir, "04_document.md", document)
        result["document"] = document

        with st.spinner("5/5 — Reviewer проверяет результат..."):
            review = ask(
                REVIEWER_ROLE,
                f"{REVIEWER_TASK}\n\n"
                f"ПРОЕКТ:\n{brief}\n\n"
                f"ДОКУМЕНТ:\n{document}"
            )

        save_file(run_dir, "05_review.md", review)
        result["review"] = review

        st.session_state.result = result
        st.success("Готово! Результаты сохранены в папке output.")

    except Exception as e:
        st.error(f"Ошибка при работе агентов: {e}")


# ==============================
# РЕЗУЛЬТАТЫ
# ==============================

if st.session_state.result:
    result = st.session_state.result

    st.divider()
    st.header("Результаты команды")

    tab_plan, tab_analysis, tab_arch, tab_doc, tab_review = st.tabs(
        [
            "📋 План",
            "📊 Анализ",
            "🏗️ Архитектура",
            "📄 Итоговый документ",
            "🔍 Проверка"
        ]
    )

    with tab_plan:
        st.markdown(result["plan"])
        st.download_button(
            "⬇️ Скачать план",
            data=result["plan"],
            file_name="01_plan.md",
            mime="text/markdown",
            use_container_width=True
        )

    with tab_analysis:
        st.markdown(result["analysis"])
        st.download_button(
            "⬇️ Скачать анализ",
            data=result["analysis"],
            file_name="02_analysis.md",
            mime="text/markdown",
            use_container_width=True
        )

    with tab_arch:
        st.markdown(result["architecture"])
        st.download_button(
            "⬇️ Скачать архитектуру",
            data=result["architecture"],
            file_name="03_architecture.md",
            mime="text/markdown",
            use_container_width=True
        )

    with tab_doc:
        st.markdown(result["document"])
        st.download_button(
            "⬇️ Скачать итоговый документ",
            data=result["document"],
            file_name="04_document.md",
            mime="text/markdown",
            use_container_width=True
        )

    with tab_review:
        st.markdown(result["review"])
        st.download_button(
            "⬇️ Скачать проверку",
            data=result["review"],
            file_name="05_review.md",
            mime="text/markdown",
            use_container_width=True
        )

    if st.session_state.run_dir:
        st.info(
            f"Файлы сохранены в:\n\n`{st.session_state.run_dir}`"
        )