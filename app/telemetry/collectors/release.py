"""What runs on the device: tsOS release (/etc/os-release), kernel and tsconfig version."""

import platform
from importlib.metadata import PackageNotFoundError, version

from app.telemetry.collectors.base import Collector, Reading

# os-release keys -> column
OS_RELEASE = {
    "NAME": "name",
    "VARIANT_ID": "variant",
    "VERSION_ID": "version",
    "VERSION_CODENAME": "codename",
    "VERSION_COMMIT": "commit",
}


class ReleaseCollector(Collector):
    name = "release"
    columns = (*OS_RELEASE.values(), "kernel", "tsconfig")

    def read(self) -> list[Reading]:
        try:
            os_release = platform.freedesktop_os_release()
        except OSError:
            os_release = {}
        reading: Reading = {column: os_release.get(key) for key, column in OS_RELEASE.items()}
        reading["kernel"] = platform.release()
        try:
            reading["tsconfig"] = version("tsconfig")
        except PackageNotFoundError:
            pass
        return [reading]
