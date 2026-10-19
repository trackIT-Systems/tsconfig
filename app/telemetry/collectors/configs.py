"""The configuration files on the boot partition: which exist, and when they last changed."""

import datetime

from app.telemetry.collectors.base import Collector, Reading


class ConfigsCollector(Collector):
    name = "configs"
    columns = ("file", "present", "mtime", "size")

    def read(self) -> list[Reading]:
        # Imported here: the registry imports every config class
        from app.configs import registry

        readings = []
        for config_type, info in registry.CONFIG_TYPES.items():
            path = registry.get_privileged_config_instance(config_type).config_file
            try:
                stat = path.stat()
            except FileNotFoundError:
                readings.append({"file": info["basename"], "present": False})
                continue
            mtime = datetime.datetime.fromtimestamp(stat.st_mtime).astimezone().isoformat(timespec="seconds")
            readings.append({"file": info["basename"], "present": True, "mtime": mtime, "size": stat.st_size})
        return readings
