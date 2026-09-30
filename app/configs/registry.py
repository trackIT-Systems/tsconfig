"""Central registry mapping config type names to their config classes.

This is the single source of truth for which config types exist and which
class implements each one. It is imported both by the FastAPI routers
(app.routers.configs) and by the privileged CLI ops (app.privileged), which
must agree exactly on this mapping: the router performs an initial validation
as `pi` for fast user feedback, and the privileged ops independently
re-resolve and re-validate the same config type as `root` before writing.
Keeping one mapping means those two call sites cannot drift apart.

Only config types that are ever written to the root-owned boot partition are
listed here. `AuthorizedKeysConfig`'s regular (non-upload) mode writes to
`/home/pi/.ssh/authorized_keys`, which `pi` owns directly and which therefore
never needs privilege elevation - only the config-upload variant (writing to
`/boot/firmware/authorized_keys`) does, so it is registered with
`is_config_upload=True`.
"""

from typing import Any, Dict

from app.configs import BaseConfig
from app.configs.authorized_keys import AuthorizedKeysConfig
from app.configs.cmdline import CmdlineConfig
from app.configs.geolocation import GeolocationConfig
from app.configs.mosquitto_cert import MosquittoCertConfig
from app.configs.mosquitto_conf import MosquittoConfConfig
from app.configs.mqttutil import MqttUtilConfig
from app.configs.radiotracking import RadioTrackingConfig
from app.configs.schedule import ScheduleConfig
from app.configs.soundscapepipe import SoundscapepipeConfig
from app.configs.tsupdate import TsupdateConfig
from app.configs.wireguard import WireguardConfig

# config_type -> (config_file basename, config class, extra __init__ kwargs)
# The basename is what identifies the type on the wire (CLI argv, zip file
# member names) and is checked against the class's actual `config_file` name
# as a sanity check in get_privileged_config_instance().
CONFIG_TYPES: Dict[str, Dict[str, Any]] = {
    "radiotracking": {"basename": "radiotracking.ini", "cls": RadioTrackingConfig, "kwargs": {}},
    "schedule": {"basename": "schedule.yml", "cls": ScheduleConfig, "kwargs": {}},
    "soundscapepipe": {"basename": "soundscapepipe.yml", "cls": SoundscapepipeConfig, "kwargs": {}},
    "authorized_keys": {"basename": "authorized_keys", "cls": AuthorizedKeysConfig, "kwargs": {"is_config_upload": True}},
    "cmdline": {"basename": "cmdline.txt", "cls": CmdlineConfig, "kwargs": {}},
    "wireguard": {"basename": "wireguard.conf", "cls": WireguardConfig, "kwargs": {}},
    "mosquitto_cert": {"basename": "server.crt", "cls": MosquittoCertConfig, "kwargs": {}},
    "mosquitto_conf": {"basename": "server.conf", "cls": MosquittoConfConfig, "kwargs": {}},
    "geolocation": {"basename": "geolocation", "cls": GeolocationConfig, "kwargs": {}},
    "tsupdate": {"basename": "tsupdate.yml", "cls": TsupdateConfig, "kwargs": {}},
    "mqttutil": {"basename": "mqttutil.conf", "cls": MqttUtilConfig, "kwargs": {}},
}

# Zip/upload file basename -> config type name (unchanged mapping, moved here
# from app.routers.configs.RECOGNIZED_CONFIG_FILES so both the router and the
# privileged ops use the identical table).
RECOGNIZED_CONFIG_FILES: Dict[str, str] = {
    info["basename"]: config_type for config_type, info in CONFIG_TYPES.items()
}

# config type -> systemd unit to restart after a successful save. Also serves
# as the source list for which units a config save is allowed to restart.
SERVICE_MAPPING: Dict[str, str] = {
    "radiotracking": "radiotracking",
    "schedule": "tsschedule",
    "soundscapepipe": "soundscapepipe",
    "wireguard": "wg-quick@wireguard",
    "mosquitto_cert": "mosquitto",
    "mosquitto_conf": "mosquitto",
    "tsupdate": "tsupdate",
    "mqttutil": "mqttutil",
}


def get_privileged_config_instance(config_type: str) -> BaseConfig:
    """Instantiate the config class for a given config type name, for writing
    to the root-owned boot partition.

    This always targets the boot-partition location for a given type (e.g.
    authorized_keys is always instantiated with is_config_upload=True here).
    The router's own get_config_instance() still exists separately for the
    non-privileged authorized_keys case (writing to /home/pi/.ssh, which pi
    owns directly and never goes through the privileged path).

    Raises:
        ValueError: If config_type is not a recognized type.
    """
    info = CONFIG_TYPES.get(config_type)
    if info is None:
        raise ValueError(f"Unknown config type: {config_type}")
    return info["cls"](**info["kwargs"])
