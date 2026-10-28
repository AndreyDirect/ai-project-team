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

from storage import read_brief, create_run_dir, save_file
from llm import ask


def main():
    brief = read_brief()
    run_dir = create_run_dir()

    # =========================
    # 1. PROJECT MANAGER
    # =========================

    print("Запускаю Project Manager...")

    plan = ask(
        PM_ROLE,
        f"{PM_TASK}\n\nПРОЕКТ:\n{brief}"
    )

    print("\n==============================")
    print("ПЛАН ПРОЕКТА")
    print("==============================\n")
    print(plan)

    approval = input("\nУтвердить план? Введите y или n: ")

    if approval.strip().lower() not in ("y", "yes"):
        print("\nОстановлено. Отредактируйте project_brief.txt и запустите снова.")
        return

    save_file(run_dir, "01_plan.md", plan)

    # =========================
    # 2. ANALYST
    # =========================

    print("\nЗапускаю Analyst...")

    analysis = ask(
        ANALYST_ROLE,
        f"{ANALYST_TASK}\n\nПРОЕКТ:\n{brief}\n\nПЛАН:\n{plan}"
    )

    save_file(run_dir, "02_analysis.md", analysis)
    print("Analyst закончил.")

    # =========================
    # 3. ARCHITECT
    # =========================

    print("\nЗапускаю Architect...")

    architecture = ask(
        ARCHITECT_ROLE,
        f"{ARCHITECT_TASK}\n\nПРОЕКТ:\n{brief}\n\nПЛАН:\n{plan}\n\nАНАЛИЗ:\n{analysis}"
    )

    save_file(run_dir, "03_architecture.md", architecture)
    print("Architect закончил.")

    # =========================
    # 4. WRITER
    # =========================

    print("\nЗапускаю Writer...")

    document = ask(
        WRITER_ROLE,
        f"{WRITER_TASK}\n\n"
        f"ПРОЕКТ:\n{brief}\n\n"
        f"ПЛАН:\n{plan}\n\n"
        f"АНАЛИЗ:\n{analysis}\n\n"
        f"АРХИТЕКТУРА:\n{architecture}"
    )

    save_file(run_dir, "04_document.md", document)
    print("Writer закончил.")

    # =========================
    # 5. REVIEWER
    # =========================

    print("\nЗапускаю Reviewer...")

    review = ask(
        REVIEWER_ROLE,
        f"{REVIEWER_TASK}\n\nПРОЕКТ:\n{brief}\n\nДОКУМЕНТ:\n{document}"
    )

    save_file(run_dir, "05_review.md", review)

    print("\n==============================")
    print("РЕЗУЛЬТАТ ПРОВЕРКИ")
    print("==============================\n")
    print(review)

    print("\n==============================")
    print("ГОТОВО")
    print("==============================")
    print(f"Все файлы сохранены в: {run_dir}")


if __name__ == "__main__":
    main()