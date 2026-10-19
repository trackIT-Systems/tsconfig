"""pyproject.toml declares every third-party module the application imports.

The tsOS image installs tsconfig with `pip --no-deps` and lists the Debian
packages itself (tsOS-base.Pifile), so a module that is imported but not
declared here is easy to miss until a device or a container lacks it."""

import ast
import os
import re
import subprocess
import sys
import tomllib
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent

# import name -> distribution name, for those that differ
DISTRIBUTION = {
    "yaml": "pyyaml",
    "gi": "pygobject",
    "dbus": "dbus-python",
    "paho": "paho-mqtt",
}

# tsOS packages the image installs next to tsconfig (not on PyPI). Only
# telemetry collectors import them, and those skip themselves without them.
TSOS_PACKAGES = {"vcgencmd", "tsschedule"}


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
        if normalize(DISTRIBUTION.get(module, module)) not in declared_all and module not in TSOS_PACKAGES
    }
    assert not missing, f"imported but not in pyproject.toml: {missing}"


def extra_modules(*extras: str) -> set[str]:
    """Top-level modules of the distributions in `extras`, minus those that core also provides"""
    project = tomllib.loads((ROOT / "pyproject.toml").read_text())["project"]
    core, _ = declared()
    dists = {normalize(dep) for extra in extras for dep in project["optional-dependencies"][extra]} - core
    module_of = {normalize(dist): module for module, dist in DISTRIBUTION.items()}
    return {module_of.get(dist, dist.replace("-", "_")) for dist in dists}


def import_web_service(server_mode: bool, blocked: set[str]) -> subprocess.CompletedProcess:
    """Import app.main in a fresh interpreter in which `blocked` cannot be imported."""
    code = f"import sys\nfor m in {sorted(blocked)!r}:\n    sys.modules[m] = None\nimport app.main\n"
    # Server mode reads its OIDC settings from DOMAIN at import
    env = {**os.environ, "TSCONFIG_SERVER_MODE": "true" if server_mode else "", "DOMAIN": "tsconfig.example"}
    return subprocess.run([sys.executable, "-c", code], cwd=ROOT, env=env, capture_output=True, text=True, check=False)


def test_the_server_web_service_needs_neither_the_tracker_nor_the_ble_extra():
    blocked = extra_modules("tracker", "ble") - extra_modules("server")
    assert "psutil" in blocked and "paho" in blocked
    result = import_web_service(server_mode=True, blocked=blocked)
    assert result.returncode == 0, result.stderr


def test_the_tracker_web_service_needs_neither_the_server_nor_the_ble_extra():
    blocked = extra_modules("server", "ble") - extra_modules("tracker")
    assert "authlib" in blocked and "gi" in blocked
    result = import_web_service(server_mode=False, blocked=blocked)
    assert result.returncode == 0, result.stderr


def test_tsos_packages_are_only_imported_by_telemetry_collectors():
    for module in TSOS_PACKAGES:
        for file in imported_modules().get(module, ()):
            assert file.startswith("app/telemetry/collectors/"), (module, file)
