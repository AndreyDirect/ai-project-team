"""Запустите из папки проекта: python add_zip_export.py"""
import ast
import os
import tempfile
from datetime import datetime
from pathlib import Path


def patch_app(source):
    tree = ast.parse(source)
    function = next((node for node in tree.body
                     if isinstance(node, ast.FunctionDef) and node.name == "show_history_details"), None)
    if function is None:
        raise ValueError("Не найдена show_history_details. Нужен app.py с последним обновлением истории и пагинации. Файл не изменён.")
    if any(isinstance(node, ast.Call) and isinstance(node.func, ast.Name)
           and node.func.id == "show_run_export" for node in ast.walk(function)):
        return source, False
    caption = next((node for node in function.body
                    if isinstance(node, ast.Expr) and isinstance(node.value, ast.Call)
                    and isinstance(node.value.func, ast.Attribute)
                    and isinstance(node.value.func.value, ast.Name)
                    and node.value.func.value.id == "st" and node.value.func.attr == "caption"), None)
    if caption is None:
        raise ValueError("Структура show_history_details отличается от ожидаемой. Файл не изменён.")
    lines = source.splitlines(keepends=True)
    lines.insert(caption.end_lineno, "    show_run_export(selected_run)\n")
    import_exists = any(isinstance(node, ast.ImportFrom) and node.module == "zip_export"
                        and any(alias.name == "show_run_export" for alias in node.names) for node in tree.body)
    if not import_exists:
        future_lines = [node.end_lineno for node in tree.body
                        if isinstance(node, ast.ImportFrom) and node.module == "__future__"]
        insert_at = max(future_lines) if future_lines else 0
        if not future_lines and tree.body and isinstance(tree.body[0], ast.Expr) and isinstance(tree.body[0].value, ast.Constant) and isinstance(tree.body[0].value.value, str):
            insert_at = tree.body[0].end_lineno
        lines.insert(insert_at, "from zip_export import show_run_export\n")
    result = "".join(lines)
    ast.parse(result)
    return result, True


def main():
    root = Path(__file__).resolve().parent
    target = root / "app.py"
    helper = root / "zip_export.py"
    if not target.is_file() or not helper.is_file():
        raise FileNotFoundError("Рядом со скриптом должны находиться app.py и zip_export.py.")
    original = target.read_bytes()
    source = original.decode("utf-8-sig")
    updated, changed = patch_app(source)
    if not changed:
        print("Экспорт ZIP уже подключён. Повторное изменение не требуется.")
        return
    backup = root / ("app_before_zip_" + datetime.now().strftime("%Y%m%d_%H%M%S_%f") + ".bak")
    backup.write_bytes(original)
    temporary = None
    try:
        with tempfile.NamedTemporaryFile(mode="w", encoding="utf-8", dir=root, delete=False, suffix=".tmp") as handle:
            temporary = Path(handle.name)
            handle.write(updated)
        os.replace(temporary, target)
    finally:
        if temporary is not None:
            temporary.unlink(missing_ok=True)
    print(f"Экспорт ZIP подключён. Резервная копия: {backup.name}")
    print("Запустите: python -m streamlit run app.py")


if __name__ == "__main__":
    try:
        main()
    except Exception as exc:
        print(f"Обновление не выполнено: {exc}")
        raise SystemExit(1)
