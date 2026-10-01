"""userconf.txt configuration management (pi's password, applied by
usr/lib/userconf-pi/userconf-service - see docs/security.md)."""

import re
from pathlib import Path
from typing import Any, Dict, List

from app.configs import BaseConfig

# `pi:` only - deliberately does not accept userconf's rename path (a
# different username), which unconditionally rewrites /etc/subuid//etc/subgid
# with no existence check, a known, deliberately-unpatched risk. The hash
# must look like an `openssl passwd -6` SHA-512 crypt hash - the only format
# used elsewhere in this repo (e.g. the TSOS_ROOT_PASSWORD build-time path).
_USERCONF_LINE_PATTERN = re.compile(r"^pi:\$6\$[A-Za-z0-9./]{1,16}\$[A-Za-z0-9./]+$")


class UserconfConfig(BaseConfig):
    """pi's password, expressed as a userconf.txt line (`pi:<crypt-hash>`).

    In tracker mode this is written to /boot/firmware/userconf.txt, where
    userconfig.service picks it up (and deletes it) on next boot - the write
    itself never touches the pi account directly. In server mode it's just
    another file in a config group's versioned directory, to be deployed to
    a real device later.
    """

    def __init__(self, config_dir: Path | None = None):
        if config_dir is None:
            # Tracker mode: always /boot/firmware, root-owned - there is no
            # unprivileged variant of this file, unlike authorized_keys.
            super().__init__(Path("/boot/firmware"))
            self._is_tracker_mode = True
        else:
            super().__init__(config_dir)
            self._is_tracker_mode = False

    @property
    def config_file(self) -> Path:
        """Return the configuration file path."""
        if self._is_tracker_mode:
            return Path("/boot/firmware/userconf.txt")
        return self.config_dir / "userconf.txt"

    def load(self) -> Dict[str, Any]:
        """Load the userconf.txt content from disk.

        Returns:
            Dictionary with 'content' key containing the file content. Empty
            in the common tracker-mode case where userconfig.service has
            already consumed and deleted the file.
        """
        if self.config_file.exists():
            try:
                with open(self.config_file, "r") as f:
                    return {"content": f.read()}
            except (IOError, OSError):
                pass

        return {"content": ""}

    def save(self, config: Dict[str, Any]) -> None:
        """Save the userconf.txt content to disk.

        Args:
            config: Dictionary with 'content' key containing the file content
        """
        content = config.get("content", "")

        with open(self.config_file, "w") as f:
            f.write(content)

    def validate(self, config: Dict[str, Any]) -> List[str]:
        """Validate the userconf.txt content.

        Args:
            config: Dictionary with 'content' key

        Returns:
            List of validation error messages (empty if valid)
        """
        errors = []

        if "content" not in config:
            errors.append("Configuration must contain 'content' field")
            return errors

        content = config.get("content", "")

        if not content or not content.strip():
            errors.append("Configuration file is empty")
            return errors

        # Exactly one line (an optional single trailing newline is fine),
        # matching `pi:$6$<salt>$<hash>` - see _USERCONF_LINE_PATTERN above.
        lines = content.splitlines()
        if len(lines) != 1:
            errors.append("userconf.txt must contain exactly one line")
            return errors

        if not _USERCONF_LINE_PATTERN.match(lines[0]):
            errors.append(
                "userconf.txt must be a single 'pi:$6$...' line (an openssl passwd -6 hash for pi) - "
                "other usernames and hash formats are not accepted"
            )

        return errors
