"""Authorized keys configuration management."""

import os
from pathlib import Path
from typing import Any, Dict, List, Optional

from app.configs import BaseConfig


class AuthorizedKeysConfig(BaseConfig):
    """Authorized keys configuration management.

    In tracker mode, manages keys in three locations:
    - /home/pi/.ssh/authorized_keys - User keys (read/write via UI)
    - /home/pi/.ssh/authorized_keys2 - Server keys (read-only, synced from /boot/firmware)
    - /boot/firmware/authorized_keys - Server keys source (write-only, for config upload)

    In server mode, manages keys in the config group directory.
    """

    def __init__(self, config_dir: Path | None = None, is_config_upload: bool = False):
        # In tracker mode (config_dir is None), we'll handle multiple locations
        # In server mode, config_dir points to the group's latest directory
        if config_dir is None:
            # Tracker mode: use a sentinel to indicate multi-file mode
            super().__init__(Path("/home/pi/.ssh"))
            self._is_tracker_mode = True
            self._is_config_upload = is_config_upload
        else:
            # Server mode: use the provided directory
            super().__init__(config_dir)
            self._is_tracker_mode = False
            self._is_config_upload = False

    @property
    def config_file(self) -> Path:
        """Return the primary configuration file path."""
        if self._is_tracker_mode:
            if self._is_config_upload:
                # Config upload writes to /boot/firmware/authorized_keys
                return Path("/boot/firmware/authorized_keys")
            else:
                # Regular UI operations use user keys file
                return Path("/home/pi/.ssh/authorized_keys")
        else:
            # In server mode, use the config directory
            return self.config_dir / "authorized_keys"
    
    @property
    def user_keys_file(self) -> Path:
        """Return the user keys file path (tracker mode only)."""
        return Path("/home/pi/.ssh/authorized_keys")
    
    @property
    def server_keys_file(self) -> Path:
        """Return the server keys file path (tracker mode only)."""
        return Path("/home/pi/.ssh/authorized_keys2")

    def _ensure_directory_with_permissions(self, file_path: Path) -> None:
        """Ensure the parent directory exists with proper SSH permissions.

        Note:
            Permission setting is best-effort and will not fail the operation.
        """
        parent_dir = file_path.parent
        if not parent_dir.exists():
            try:
                parent_dir.mkdir(mode=0o700, parents=True, exist_ok=True)
                # Explicitly set permissions in case umask interfered (best-effort)
                try:
                    os.chmod(parent_dir, 0o700)
                except (OSError, PermissionError):
                    # Ignore permission errors
                    pass
            except (OSError, PermissionError) as e:
                # If we can't create the directory, that's a real error
                raise OSError(f"Cannot create directory {parent_dir}: {str(e)}")

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

        In tracker mode (non-config-upload), loads from both:
        - User keys: /home/pi/.ssh/authorized_keys (source: "user")
        - Server keys: /home/pi/.ssh/authorized_keys2 (source: "server")

        In config upload mode or server mode, loads from single file.

        Returns:
            Dictionary with 'keys' list containing parsed key information
        """
        keys = []

        if self._is_tracker_mode and not self._is_config_upload:
            # Load user keys
            if self.user_keys_file.exists():
                try:
                    with open(self.user_keys_file, "r") as f:
                        for idx, line in enumerate(f):
                            parsed = self._parse_key_line(line, idx)
                            if parsed:
                                parsed["source"] = "user"
                                keys.append(parsed)
                except (IOError, OSError):
                    # File might not be readable, that's okay
                    pass
            
            # Load server keys
            if self.server_keys_file.exists():
                try:
                    with open(self.server_keys_file, "r") as f:
                        for idx, line in enumerate(f):
                            parsed = self._parse_key_line(line, len(keys) + idx)
                            if parsed:
                                parsed["source"] = "server"
                                keys.append(parsed)
                except (IOError, OSError):
                    # File might not be readable, that's okay
                    pass
        else:
            # Config upload mode or server mode: read from single file
            primary_file = self.config_file
            if primary_file.exists():
                try:
                    with open(primary_file, "r") as f:
                        for idx, line in enumerate(f):
                            parsed = self._parse_key_line(line, idx)
                            if parsed:
                                # Mark as server source in config upload mode
                                if self._is_tracker_mode and self._is_config_upload:
                                    parsed["source"] = "server"
                                keys.append(parsed)
                except (IOError, OSError):
                    # File might not be readable, that's okay
                    pass

        return {"keys": keys}

    def save(self, config: Dict[str, Any]) -> None:
        """Save the authorized keys configuration to disk.

        In tracker mode:
        - Config upload: writes to /boot/firmware/authorized_keys (server keys source)
        - Regular mode: writes only user keys to /home/pi/.ssh/authorized_keys

        In server mode: writes to config directory.

        Args:
            config: Dictionary with 'keys' list

        Note:
            Permission setting is best-effort and will not fail the operation.
            In server mode, permissions are not set as they're not needed for file storage.
            In tracker mode on FAT filesystems (like /boot/firmware), chmod will be silently ignored.
        """
        keys = config.get("keys", [])

        if self._is_tracker_mode and not self._is_config_upload:
            # Regular mode: save only user keys to authorized_keys
            user_keys = [k for k in keys if k.get("source") != "server"]
            
            # Generate the file content for user keys
            lines = []
            for key_info in user_keys:
                lines.append(key_info["full_line"])

            content = "\n".join(lines)
            if content:
                content += "\n"  # Ensure trailing newline

            # Write to user keys file
            target_file = self.user_keys_file
            self._ensure_directory_with_permissions(target_file)

            with open(target_file, "w") as f:
                f.write(content)

            # Try to set permissions (best-effort)
            try:
                self._set_file_permissions(target_file)
            except (OSError, PermissionError):
                # Ignore permission errors
                pass
        else:
            # Config upload mode or server mode: write all keys to primary file
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

            # Try to set permissions (best-effort, don't fail)
            # Skip in server mode as it's not needed for file storage
            # In tracker mode, ignore failures (e.g., FAT filesystem, non-owned files)
            if self._is_tracker_mode:
                try:
                    self._set_file_permissions(primary_file)
                except (OSError, PermissionError):
                    # Ignore permission errors (e.g., FAT filesystem, file owned by another user)
                    pass

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
        """Add a new key to the user keys configuration.

        In tracker mode, only adds to user keys and checks for duplicates
        in both user and server keys.

        Args:
            key_line: The SSH public key line to add

        Returns:
            Updated configuration dictionary

        Raises:
            ValueError: If the key format is invalid or if the key already exists
        """
        # Load current config (includes both user and server keys in tracker mode)
        config = self.load()
        keys = config["keys"]

        # Count user keys for proper indexing
        user_keys = [k for k in keys if k.get("source") != "server"]
        
        # Parse the new key with proper index
        new_key = self._parse_key_line(key_line, len(user_keys))

        if not new_key:
            raise ValueError("Invalid SSH key format")

        # Check if the key already exists in either user or server keys
        # Compare by key_data (the base64 part) to detect duplicates
        new_key_data = new_key.get("key_data", "")
        
        for existing_key in keys:
            existing_key_data = existing_key.get("key_data", "")
            # For complex keys (with options), compare the full line
            if new_key.get("is_complex") or existing_key.get("is_complex"):
                # Compare full lines, but strip whitespace for comparison
                if new_key["full_line"].strip() == existing_key["full_line"].strip():
                    source = existing_key.get("source", "unknown")
                    raise ValueError(f"SSH key already exists in {source} keys")
            elif new_key_data and new_key_data == existing_key_data:
                # For standard keys, compare the key data (base64 part)
                source = existing_key.get("source", "unknown")
                raise ValueError(f"SSH key already exists in {source} keys")

        # Mark as user key and add it
        new_key["source"] = "user"
        keys.append(new_key)
        config["keys"] = keys

        return config

    def remove_key(self, key_index: int) -> Dict[str, Any]:
        """Remove a key from the user keys configuration by index.

        In tracker mode, only allows removing user keys. Server keys cannot be removed.

        Args:
            key_index: The index of the key to remove

        Returns:
            Updated configuration dictionary
        
        Raises:
            ValueError: If the index is invalid or if trying to remove a server key
        """
        # Load current config
        config = self.load()
        keys = config["keys"]

        if key_index < 0 or key_index >= len(keys):
            raise ValueError(f"Invalid key index: {key_index}")

        # Check if this is a server key (cannot be removed)
        key_to_remove = keys[key_index]
        if key_to_remove.get("source") == "server":
            raise ValueError("Cannot remove server-managed SSH keys. These keys are managed via config upload.")

        # Remove the key
        keys.pop(key_index)

        # Re-index the remaining keys
        for idx, key in enumerate(keys):
            key["index"] = idx

        config["keys"] = keys

        return config
