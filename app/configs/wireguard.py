"""Wireguard configuration management."""

import re
from configparser import ConfigParser
from pathlib import Path
from typing import Any

from app.configs import BaseConfig

# wg-quick(8) executes these keys as shell commands (as root, since wg-quick@.service
# runs as root) when it brings the interface up or down. A config uploaded through
# tsconfig must never be allowed to set them - that would be arbitrary root code
# execution via a "VPN config" upload. Matched case-insensitively at the start of a
# line (optionally indented), same as wg-quick's own key parsing.
_HOOK_KEY_PATTERN = re.compile(r"^\s*(PreUp|PostUp|PreDown|PostDown|SaveConfig)\s*=", re.IGNORECASE | re.MULTILINE)


class WireguardConfig(BaseConfig):
    """Wireguard configuration management.

    This file contains WireGuard VPN configuration and is written to /boot/firmware/wireguard.conf.
    It must be a valid INI file containing [Interface] and [Peer] sections.
    """

    def __init__(self, config_dir: Path | None = None):
        # Always use /boot/firmware regardless of config_dir
        # (no server-mode support for this config type)
        super().__init__(Path("/boot/firmware"))

    @property
    def config_file(self) -> Path:
        """Return the configuration file path."""
        return Path("/boot/firmware/wireguard.conf")

    def load(self) -> dict[str, Any]:
        """Load the wireguard configuration from disk.

        Returns:
            Dictionary with 'content' key containing the file content
        """
        if self.config_file.exists():
            try:
                with open(self.config_file, "r") as f:
                    content = f.read()
                return {"content": content}
            except OSError:
                pass

        return {"content": ""}

    def save(self, config: dict[str, Any]) -> None:
        """Save the wireguard configuration to disk.

        Args:
            config: Dictionary with 'content' key containing the file content
        """
        content = config.get("content", "")

        # Write content to file
        with open(self.config_file, "w") as f:
            f.write(content)

    def validate(self, config: dict[str, Any]) -> list[str]:
        """Validate the wireguard configuration.

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

        # Reject wg-quick hook keys before attempting to parse: they run as root
        # shell commands when the interface is brought up/down, so allowing them
        # here would be arbitrary root code execution via a config upload. Checked
        # on the raw text (not the parsed INI) so this can't be bypassed by a
        # config that's otherwise malformed enough to dodge ConfigParser.
        hook_match = _HOOK_KEY_PATTERN.search(content)
        if hook_match:
            errors.append(
                f"Configuration must not contain '{hook_match.group(1)}' - "
                "hook directives (PreUp/PostUp/PreDown/PostDown/SaveConfig) are not allowed"
            )
            return errors

        # Parse as INI file to check structure
        try:
            parser = ConfigParser()
            parser.read_string(content)

            sections = parser.sections()

            if not sections:
                errors.append("Configuration file has no sections")
                return errors

            # Check for required sections
            if "Interface" not in sections:
                errors.append("Configuration must contain [Interface] section")

            if "Peer" not in sections:
                errors.append("Configuration must contain [Peer] section")

        except Exception as e:
            errors.append(f"Invalid INI file format: {e!s}")

        return errors
