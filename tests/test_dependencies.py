"""pyproject.toml declares every third-party module the application imports.

The tsOS image installs tsconfig with `pip --no-deps` and lists the Debian
packages itself (tsOS-base.Pifile), so a module that is imported but not
declared here is easy to miss until a device or a container lacks it."""

import ast
import re
import sys
import tomllib
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent

# import name -> distribution name, for those that differ
DISTRIBUTION = {
    "yaml": "pyyaml",
    "dateutil": "python-dateutil",
    "gi": "pygobject",
    "dbus": "dbus-python",
}


def normalize(name: str) -> str:
    return re.sub(r"[-_.]+", "-", re.split(r"[\[<>=!~ ;]", name, maxsplit=1)[0]).lower()


def declared() -> tuple[set[str], set[str]]:
    """(always installed, only with an extra)"""
    project = tomllib.loads((ROOT / "pyproject.toml").read_text())["project"]
    core = {normalize(dep) for dep in project["dependencies"]}
    extras = {normalize(dep) for deps in project["optional-dependencies"].values() for dep in deps}
    return core, extras


def imported_modules() -> dict[str, set[str]]:
    """top-level third-party module -> files importing it"""
    found: dict[str, set[str]] = {}
    for path in (ROOT / "app").rglob("*.py"):
        for node in ast.walk(ast.parse(path.read_text())):
            if isinstance(node, ast.Import):
                names = [alias.name for alias in node.names]
            elif isinstance(node, ast.ImportFrom) and node.level == 0 and node.module:
                names = [node.module]
            else:
                continue
            for name in names:
                top = name.split(".")[0]
                if top != "app" and top not in sys.stdlib_module_names:
                    found.setdefault(top, set()).add(str(path.relative_to(ROOT)))
    return found


def test_every_imported_module_is_declared():
    core, extras = declared()
    declared_all = core | extras
    missing = {
        module: sorted(files)
        for module, files in imported_modules().items()
        if normalize(DISTRIBUTION.get(module, module)) not in declared_all
    }
    assert not missing, f"imported but not in pyproject.toml: {missing}"


def test_the_tracker_needs_neither_the_server_nor_the_ble_extra():
    """Modules of the extras are imported where they are used (OIDC, BLE) and
    nowhere a tracker's web service loads at startup."""
    core, extras = declared()
    only_extra = extras - core
    modules = imported_modules()
    imported_by_web_service = {
        module
        for module, files in modules.items()
        if normalize(DISTRIBUTION.get(module, module)) in only_extra
        for file in files
        if not file.startswith(("app/bluetooth/", "app/ble_gateway.py", "app/auth/oidc_handler.py"))
    }
    assert not imported_by_web_service, imported_by_web_service
