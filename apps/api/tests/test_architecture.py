"""Architecture rules that keep tenant isolation hard to break by accident."""

import ast
from pathlib import Path

PACKAGE = Path(__file__).resolve().parent.parent / "ape"

# Only these may import the database driver or run SQL.
SQL_ALLOWED = ("core/db.py", "repos/")
SQL_METHODS = {"execute", "executemany", "cursor", "copy"}


def _python_files() -> list[Path]:
    return sorted(PACKAGE.rglob("*.py"))


def _is_allowed(path: Path) -> bool:
    relative = path.relative_to(PACKAGE).as_posix()
    return any(relative == a or relative.startswith(a) for a in SQL_ALLOWED)


def test_only_the_repository_layer_talks_to_the_database() -> None:
    offenders: list[str] = []
    for path in _python_files():
        if _is_allowed(path):
            continue
        tree = ast.parse(path.read_text())
        for node in ast.walk(tree):
            if isinstance(node, ast.Import):
                names = [alias.name for alias in node.names]
            elif isinstance(node, ast.ImportFrom):
                names = [node.module or ""]
            elif isinstance(node, ast.Attribute) and node.attr in SQL_METHODS:
                offenders.append(f"{path.relative_to(PACKAGE)}:{node.lineno} calls .{node.attr}()")
                continue
            else:
                continue
            for name in names:
                if name.split(".")[0] in {"psycopg", "psycopg_pool"}:
                    offenders.append(f"{path.relative_to(PACKAGE)}:{node.lineno} imports {name}")
    assert not offenders, "raw SQL outside ape/repos:\n" + "\n".join(offenders)


def test_repository_functions_require_a_connection_wrapper() -> None:
    """Every public repository function takes a TenantConnection or SystemConnection first."""
    offenders: list[str] = []
    for path in sorted((PACKAGE / "repos").glob("*.py")):
        tree = ast.parse(path.read_text())
        for node in tree.body:
            if not isinstance(node, ast.AsyncFunctionDef | ast.FunctionDef) or node.name.startswith(
                "_"
            ):
                continue
            first = node.args.args[0] if node.args.args else None
            annotation = ast.unparse(first.annotation) if first and first.annotation else ""
            if annotation not in {"TenantConnection", "SystemConnection"}:
                offenders.append(f"{path.name}:{node.name}")
    assert not offenders, f"repository functions without a connection wrapper: {offenders}"
