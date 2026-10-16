"""Which config types this device supports.

A config that belongs to a service (registry.SERVICE_MAPPING) is only
supported if that service's systemd unit exists on the device: a radiotracking
image has no soundscapepipe.service, so a soundscapepipe.yml there configures
nothing - but its presence used to make tsconfig show a soundscapepipe tab.
Configs of the base system (authorized_keys, cmdline.txt, ...) are always
supported.

Only meaningful in tracker mode; a server has none of the trackers' units.
"""

import subprocess
from collections.abc import Iterable

from app.configs.registry import SERVICE_MAPPING
from app.logging_config import get_logger
from app.utils.subprocess_async import run_subprocess_async

logger = get_logger(__name__)


async def unsupported_config_types(config_types: Iterable[str]) -> dict[str, str]:
    """Return {config type: missing unit} for those of `config_types` whose
    service unit does not exist on this device.

    If systemd cannot be asked, every type counts as supported: refusing all
    service configs would be worse than the old behaviour.
    """
    units = {
        config_type: SERVICE_MAPPING[config_type] for config_type in config_types if config_type in SERVICE_MAPPING
    }
    if not units:
        return {}

    names = sorted(set(units.values()))
    cmd = ["systemctl", "show", "--property=LoadState", *(f"{name}.service" for name in names)]
    try:
        result = await run_subprocess_async(cmd, timeout=10)
    except (OSError, subprocess.TimeoutExpired):
        logger.warning("Could not query systemd units, treating all configs as supported", exc_info=True)
        return {}

    # One "LoadState=..." block per unit, in argument order, separated by blank lines
    states = [block.strip().removeprefix("LoadState=") for block in result.stdout.strip().split("\n\n")]
    if result.returncode != 0 or len(states) != len(names):
        logger.warning(
            f"Unexpected systemctl show output (exit {result.returncode}), treating all configs as supported: "
            f"{result.stdout!r} {result.stderr!r}"
        )
        return {}

    missing = {name for name, state in zip(names, states, strict=True) if state == "not-found"}
    return {config_type: unit for config_type, unit in units.items() if unit in missing}


def unsupported_message(unsupported: dict[str, str], files_by_type: dict[str, str]) -> str:
    """Describe rejected files, e.g. "soundscapepipe.yml (no soundscapepipe service on this device)"."""
    parts = [
        f"{files_by_type[config_type]} (no {unit} service on this device)" for config_type, unit in unsupported.items()
    ]
    return "Configuration not supported on this device: " + ", ".join(parts)
