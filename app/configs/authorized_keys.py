"""Authorized keys configuration management."""

import os
from pathlib import Path
from typing import Any, Dict, List, Optional

from app.configs import BaseConfig


class AuthorizedKeysConfig(BaseConfig):
    """Authorized keys configuration management.

    In tracker mode, manages keys in two locations:
    - /home/pi/.ssh/authorized_keys
    - /boot/firmware/authorized_keys

    In server mode, manages keys in the config group directory.
    """

    def __init__(self, config_dir: Path | None = None):
        # In tracker mode (config_dir is None), we'll handle dual locations
        # In server mode, config_dir points to the group's latest directory
        if config_dir is None:
            # Tracker mode: use a sentinel to indicate dual-file mode
            super().__init__(Path("/boot/firmware"))
            self._is_tracker_mode = True
        else:
            # Server mode: use the provided directory
            super().__init__(config_dir)
            self._is_tracker_mode = False

    @property
    def config_file(self) -> Path:
        """Return the primary configuration file path."""
        if self._is_tracker_mode:
            # In tracker mode, we use /boot/firmware/authorized_keys as primary
            return Path("/boot/firmware/authorized_keys")
        else:
            # In server mode, use the config directory
            return self.config_dir / "authorized_keys"

    @property
    def secondary_config_file(self) -> Optional[Path]:
        """Return the secondary configuration file path (tracker mode only)."""
        if self._is_tracker_mode:
            return Path("/home/pi/.ssh/authorized_keys")
        return None

    def _ensure_directory_with_permissions(self, file_path: Path) -> None:
        """Ensure the parent directory exists with proper SSH permissions."""
        parent_dir = file_path.parent
        if not parent_dir.exists():
            parent_dir.mkdir(mode=0o700, parents=True, exist_ok=True)
            # Explicitly set permissions in case umask interfered
            os.chmod(parent_dir, 0o700)

    def _set_file_permissions(self, file_path: Path) -> None:
        """Set proper SSH permissions on the file."""
        if file_path.exists():
            os.chmod(file_path, 0o600)

    def _parse_key_line(self, line: str, index: int) -> Optional[Dict[str, Any]]:
        """Parse a single authorized_keys line into a structured format.

        Args:
            line: The line to parse
            index: The index of this key in the file

        Returns:
            Dictionary with key information or None if invalid/comment
        """
        line = line.strip()

        # Skip empty lines and comments
        if not line or line.startswith("#"):
            return None

        # SSH key format: [options] <key-type> <base64-key> [comment]
        # We'll focus on the standard format: <key-type> <base64-key> [comment]
        parts = line.split(None, 2)  # Split into max 3 parts

        if len(parts) < 2:
            return None

        key_type = parts[0]
        key_data = parts[1]
        comment = parts[2] if len(parts) > 2 else ""

        # Basic validation: check if it looks like a valid key type
        valid_key_types = [
            "ssh-rsa",
            "ssh-ed25519",
            "ssh-dss",
            "ecdsa-sha2-nistp256",
            "ecdsa-sha2-nistp384",
            "ecdsa-sha2-nistp521",
            "sk-ecdsa-sha2-nistp256@openssh.com",
            "sk-ssh-ed25519@openssh.com",
        ]

        if key_type not in valid_key_types:
            # Might have options prepended, try to find key type in the line
            for valid_type in valid_key_types:
                if valid_type in line:
                    # This is a complex key with options, include the full line
                    return {
                        "index": index,
                        "key_type": valid_type,
                        "full_line": line,
                        "comment": comment,
                        "is_complex": True,
                    }
            return None

        return {
            "index": index,
            "key_type": key_type,
            "key_data": key_data,
            "comment": comment,
            "full_line": line,
            "is_complex": False,
        }

    def load(self) -> Dict[str, Any]:
        """Load the authorized keys configuration from disk.

        Returns:
            Dictionary with 'keys' list containing parsed key information
        """
        keys = []

        # Read from primary file
        primary_file = self.config_file
        if primary_file.exists():
            try:
                with open(primary_file, "r") as f:
                    for idx, line in enumerate(f):
                        parsed = self._parse_key_line(line, idx)
                        if parsed:
                            keys.append(parsed)
            except (IOError, OSError):
                # File might not be readable, that's okay
                pass

        # In tracker mode, also read from secondary file and merge
        # (prefer keys from secondary file if they differ)
        if self._is_tracker_mode and self.secondary_config_file:
            secondary_file = self.secondary_config_file
            if secondary_file.exists():
                try:
                    secondary_keys = []
                    with open(secondary_file, "r") as f:
                        for idx, line in enumerate(f):
                            parsed = self._parse_key_line(line, idx)
                            if parsed:
                                secondary_keys.append(parsed)

                    # If secondary has more keys or different keys, prefer it
                    if len(secondary_keys) > len(keys):
                        keys = secondary_keys
                except (IOError, OSError):
                    pass

        return {"keys": keys}

    def save(self, config: Dict[str, Any]) -> None:
        """Save the authorized keys configuration to disk.

        Args:
            config: Dictionary with 'keys' list
        """
        keys = config.get("keys", [])

        # Generate the file content
        lines = []
        for key_info in keys:
            lines.append(key_info["full_line"])

        content = "\n".join(lines)
        if content:
            content += "\n"  # Ensure trailing newline

        # Write to primary file
        primary_file = self.config_file
        self._ensure_directory_with_permissions(primary_file)

        with open(primary_file, "w") as f:
            f.write(content)

        self._set_file_permissions(primary_file)

        # In tracker mode, also write to secondary file
        if self._is_tracker_mode and self.secondary_config_file:
            secondary_file = self.secondary_config_file
            self._ensure_directory_with_permissions(secondary_file)

            with open(secondary_file, "w") as f:
                f.write(content)

            self._set_file_permissions(secondary_file)

    def validate(self, config: Dict[str, Any]) -> List[str]:
        """Validate the authorized keys configuration.

        Args:
            config: Dictionary with 'keys' list

        Returns:
            List of validation error messages (empty if valid)
        """
        errors = []

        if "keys" not in config:
            errors.append("Configuration must contain 'keys' field")
            return errors

        keys = config.get("keys", [])

        if not isinstance(keys, list):
            errors.append("'keys' must be a list")
            return errors

        # Validate each key
        valid_key_types = [
            "ssh-rsa",
            "ssh-ed25519",
            "ssh-dss",
            "ecdsa-sha2-nistp256",
            "ecdsa-sha2-nistp384",
            "ecdsa-sha2-nistp521",
            "sk-ecdsa-sha2-nistp256@openssh.com",
            "sk-ssh-ed25519@openssh.com",
        ]

        for idx, key_info in enumerate(keys):
            if not isinstance(key_info, dict):
                errors.append(f"Key {idx}: must be a dictionary")
                continue

            if "full_line" not in key_info:
                errors.append(f"Key {idx}: missing 'full_line' field")
                continue

            full_line = key_info["full_line"].strip()

            # Check if line is empty or comment
            if not full_line or full_line.startswith("#"):
                continue

            # Validate key type is present in the line
            has_valid_type = any(key_type in full_line for key_type in valid_key_types)

            if not has_valid_type:
                errors.append(f"Key {idx}: invalid or missing key type. Must be one of: {', '.join(valid_key_types)}")

            # Basic format validation - should have at least 2 space-separated parts
            parts = full_line.split()
            if len(parts) < 2:
                errors.append(f"Key {idx}: invalid format. Expected at least key type and key data")

        return errors

    def add_key(self, key_line: str) -> Dict[str, Any]:
        """Add a new key to the configuration.

        Args:
            key_line: The SSH public key line to add

        Returns:
            Updated configuration dictionary
        """
        # Load current config
        config = self.load()
        keys = config["keys"]

        # Parse the new key
        new_key = self._parse_key_line(key_line, len(keys))

        if not new_key:
            raise ValueError("Invalid SSH key format")

        # Add the new key
        keys.append(new_key)
        config["keys"] = keys

        return config

    def remove_key(self, key_index: int) -> Dict[str, Any]:
        """Remove a key from the configuration by index.

        Args:
            key_index: The index of the key to remove

        Returns:
            Updated configuration dictionary
        """
        # Load current config
        config = self.load()
        keys = config["keys"]

        if key_index < 0 or key_index >= len(keys):
            raise ValueError(f"Invalid key index: {key_index}")

        # Remove the key
        keys.pop(key_index)

        # Re-index the remaining keys
        for idx, key in enumerate(keys):
            key["index"] = idx

        config["keys"] = keys

        return config
