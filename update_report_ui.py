"""Обновление окна отчёта: python update_report_ui.py"""
import ast
import os
import tempfile
from datetime import datetime
from pathlib import Path


def patch_history(source):
    tree = ast.parse(source)
    function = next((n for n in tree.body if isinstance(n, ast.FunctionDef) and n.name == 'render_report'), None)
    if function is None:
        raise ValueError('Не найдена render_report. Нужен history_view.py с отдельными кнопками ZIP, «Продолжить» и «Заново».')
    replacement = ('def render_report(row):\n'
                   '    from report_view import render_report_dialog\n'
                   '    render_report_dialog(row)\n')
    lines = source.splitlines(keepends=True)
    lines[function.lineno-1:function.end_lineno] = [replacement]
    updated = ''.join(lines)
    ast.parse(updated)
    return updated


def main():
    root = Path(__file__).resolve().parent
    path = root / 'history_view.py'
    if not (root / 'report_view.py').is_file():
        raise FileNotFoundError('Сохраните report_view.py рядом с history_view.py.')
    original = path.read_bytes()
    updated = patch_history(original.decode('utf-8-sig'))
    if updated == original.decode('utf-8-sig'):
        print('Окно отчёта уже обновлено.')
        return
    backup = root / ('history_view_before_report_' + datetime.now().strftime('%Y%m%d_%H%M%S_%f') + '.bak')
    backup.write_bytes(original)
    temporary = None
    try:
        with tempfile.NamedTemporaryFile(mode='w', encoding='utf-8', dir=root, suffix='.tmp', delete=False) as handle:
            temporary = Path(handle.name)
            handle.write(updated)
        os.replace(temporary, path)
    finally:
        if temporary is not None:
            temporary.unlink(missing_ok=True)
    print(f'Окно отчёта обновлено. Резервная копия: {backup.name}')
    print('Запустите: python -m streamlit run app.py')


if __name__ == '__main__':
    try:
        main()
    except Exception as exc:
        print(f'Обновление не выполнено: {exc}')
        raise SystemExit(1)
