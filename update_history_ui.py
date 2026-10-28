"""Установка интерфейса истории: python update_history_ui.py"""
import ast
import os
import tempfile
from datetime import datetime
from pathlib import Path


def patch_app(source):
    tree = ast.parse(source)
    history = next((node for node in tree.body if isinstance(node, ast.With)
                    and any(isinstance(item.context_expr, ast.Name) and item.context_expr.id == "tab_history"
                            for item in node.items)), None)
    if history is None:
        raise ValueError("В app.py не найдена вкладка tab_history. Нужна версия с историей запусков.")
    already = (len(history.body) == 1 and isinstance(history.body[0], ast.Expr)
               and isinstance(history.body[0].value, ast.Call)
               and isinstance(history.body[0].value.func, ast.Name)
               and history.body[0].value.func.id == "render_history")
    imported = any(isinstance(n, ast.ImportFrom) and n.module == "history_view"
                   and any(a.name == "render_history" for a in n.names) for n in tree.body)
    if already and imported:
        return source, False
    lines = source.splitlines(keepends=True)
    lines[history.lineno-1:history.end_lineno] = ["with tab_history:\n", "    render_history()\n"]
    if not imported:
        future = [n.end_lineno for n in tree.body if isinstance(n, ast.ImportFrom) and n.module == "__future__"]
        position = max(future) if future else 0
        if not future and tree.body and isinstance(tree.body[0], ast.Expr) and isinstance(tree.body[0].value, ast.Constant) and isinstance(tree.body[0].value.value, str):
            position = tree.body[0].end_lineno
        lines.insert(position, "from history_view import render_history\n")
    updated = "".join(lines)
    ast.parse(updated)
    return updated, True


def main():
    root = Path(__file__).resolve().parent
    path = root / "app.py"
    if not (root / "history_view.py").is_file():
        raise FileNotFoundError("Сохраните history_view.py рядом с app.py.")
    original = path.read_bytes()
    updated, changed = patch_app(original.decode("utf-8-sig"))
    if not changed:
        print("Новый интерфейс истории уже подключён.")
        return
    backup = root / ("app_before_history_ui_" + datetime.now().strftime("%Y%m%d_%H%M%S_%f") + ".bak")
    backup.write_bytes(original)
    temporary = None
    try:
        with tempfile.NamedTemporaryFile(mode="w", encoding="utf-8", dir=root, suffix=".tmp", delete=False) as handle:
            temporary = Path(handle.name)
            handle.write(updated)
        os.replace(temporary, path)
    finally:
        if temporary is not None:
            temporary.unlink(missing_ok=True)
    print(f"История обновлена. Резервная копия: {backup.name}")
    print("Запустите: python -m streamlit run app.py")


if __name__ == "__main__":
    try:
        main()
    except Exception as exc:
        print(f"Обновление не выполнено: {exc}")
        raise SystemExit(1)
