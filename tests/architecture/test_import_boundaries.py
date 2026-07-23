import ast
import importlib
from pathlib import Path


SRC = Path(__file__).resolve().parents[2] / "src"


def test_all_three_packages_import():
    assert importlib.import_module("wavegen_tool_core")
    assert importlib.import_module("wavegen_tool_cli")
    assert importlib.import_module("wavegen_tool_webui")


def test_package_import_boundaries():
    forbidden = {
        "wavegen_tool_core": ("wavegen_tool_cli", "wavegen_tool_webui"),
        "wavegen_tool_cli": ("wavegen_tool_webui",),
        "wavegen_tool_webui": ("wavegen_tool_cli",),
    }

    for package, forbidden_prefixes in forbidden.items():
        for path in (SRC / package).rglob("*.py"):
            tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
            imports = _imported_modules(tree)
            assert not any(
                imported == prefix or imported.startswith(f"{prefix}.")
                for imported in imports
                for prefix in forbidden_prefixes
            ), f"{path} violates package import boundaries"


def test_cli_delegates_instrument_rules_to_core():
    cli_path = SRC / "wavegen_tool_cli" / "cli.py"
    source = cli_path.read_text(encoding="utf-8")

    assert "identify_instrument" in source
    assert "*IDN?" not in source
    assert "33521B" not in source
    assert "KEYSIGHT TECHNOLOGIES" not in source


def _imported_modules(tree):
    modules = []
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            modules.extend(alias.name for alias in node.names)
        elif isinstance(node, ast.ImportFrom) and node.module is not None:
            modules.append(node.module)
    return modules
