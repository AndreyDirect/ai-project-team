"""Подключение главной страницы: python add_overview.py"""
import ast
import os
import tempfile
from datetime import datetime
from pathlib import Path


def patch_app(source):
    tree = ast.parse(source)
    if any(isinstance(n, ast.ImportFrom) and n.module == 'overview_view' for n in tree.body):
        return source, False
    tabs = next((n for n in tree.body if isinstance(n, ast.Assign)
                 and isinstance(n.value, ast.Call) and isinstance(n.value.func, ast.Attribute)
                 and isinstance(n.value.func.value, ast.Name) and n.value.func.value.id == 'st'
                 and n.value.func.attr == 'tabs'
                 and any(isinstance(t, ast.Tuple) and any(isinstance(e, ast.Name) and e.id == 'tab_history' for e in t.elts) for t in n.targets)), None)
    if tabs is None:
        raise ValueError('Не найдена ожидаемая навигация. app.py не изменён.')
    labels = ['🏠 Обзор', '🚀 Новый запуск', '🕘 История запусков', '📚 База знаний']
    replacement = (
        "if st.session_state.get('main_tab') == '📋 Бриф проекта':\n"
        "    st.session_state.main_tab = '🚀 Новый запуск'\n"
        "tab_overview, tab_brief, tab_history, tab_knowledge = st.tabs(\n"
        f"    {labels!r},\n"
        "    key='main_tab', bind='query-params', on_change='rerun',\n"
        ")\n"
        "with tab_overview:\n"
        "    render_overview()\n"
    )
    lines = source.splitlines(keepends=True)
    lines[tabs.lineno-1:tabs.end_lineno] = [replacement]
    future = [n.end_lineno for n in tree.body if isinstance(n, ast.ImportFrom) and n.module == '__future__']
    position = max(future) if future else 0
    if not future and tree.body and isinstance(tree.body[0], ast.Expr) and isinstance(tree.body[0].value, ast.Constant) and isinstance(tree.body[0].value.value, str):
        position = tree.body[0].end_lineno
    lines.insert(position, 'from overview_view import render_overview\n')
    result = ''.join(lines)
    result = result.replace('st.title("🤖 AI Project Team")', 'st.title("ИИ-проектный офис")')
    result = result.replace('st.subheader("Бриф проекта")', 'st.subheader("Новый запуск")')
    result = result.replace('🚀 Отправить проект агентам', '🚀 Запустить подготовку')
    # Replace only the introductory caption, not model details elsewhere.
    parsed = ast.parse(result)
    intro = next((n for n in parsed.body if isinstance(n, ast.Expr)
                  and isinstance(n.value, ast.Call) and isinstance(n.value.func, ast.Attribute)
                  and isinstance(n.value.func.value, ast.Name) and n.value.func.value.id == 'st'
                  and n.value.func.attr == 'caption'), None)
    if intro:
        lines = result.splitlines(keepends=True)
        lines[intro.lineno-1:intro.end_lineno] = ['st.caption("Подготовка проектных документов по брифу и материалам компании.")\n']
        result = ''.join(lines)
    ast.parse(result)
    return result, True


def main():
    root = Path(__file__).resolve().parent
    for name in ['app.py', 'overview_view.py', 'history_view.py', 'pipeline.py']:
        if not (root / name).is_file():
            raise FileNotFoundError(f'Не найден {name}. Сохраните файлы в папке проекта.')
    app = root / 'app.py'
    original = app.read_bytes()
    updated, changed = patch_app(original.decode('utf-8-sig'))
    if not changed:
        print('Главная страница уже подключена.')
        return
    backup = root / ('app_before_overview_' + datetime.now().strftime('%Y%m%d_%H%M%S_%f') + '.bak')
    backup.write_bytes(original)
    temporary = None
    try:
        with tempfile.NamedTemporaryFile(mode='w', encoding='utf-8', dir=root, suffix='.tmp', delete=False) as handle:
            temporary = Path(handle.name)
            handle.write(updated)
        os.replace(temporary, app)
    finally:
        if temporary is not None:
            temporary.unlink(missing_ok=True)
    print(f'Обзор подключён. Резервная копия: {backup.name}')
    print('Запустите: python -m streamlit run app.py')


if __name__ == '__main__':
    try:
        main()
    except Exception as exc:
        print(f'Обновление не выполнено: {exc}')
        raise SystemExit(1)
