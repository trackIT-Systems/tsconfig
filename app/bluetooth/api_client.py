"""HTTP API client for communicating with tsconfig REST API.

This module provides an async HTTP client that bridges BLE GATT operations
to the tsconfig HTTP API endpoints.
"""

import asyncio
import base64
import logging
from typing import Any, Dict, Optional

import httpx

logger = logging.getLogger(__name__)


class TsConfigApiClient:
    """Async HTTP client for tsconfig API."""

    def __init__(self, base_url: str = "http://localhost:8000", timeout: float = 30.0):
        """Initialize the API client.

        Args:
            base_url: Base URL of the tsconfig API
            timeout: Request timeout in seconds
        """
        self.base_url = base_url.rstrip("/")
        self.timeout = timeout
        self.client: Optional[httpx.AsyncClient] = None

    async def __aenter__(self):
        """Async context manager entry."""
        self.client = httpx.AsyncClient(base_url=self.base_url, timeout=self.timeout)
        return self

    async def __aexit__(self, exc_type, exc_val, exc_tb):
        """Async context manager exit."""
        if self.client:
            await self.client.aclose()
            self.client = None

    async def _ensure_client(self):
        """Ensure the HTTP client is initialized."""
        if not self.client:
            self.client = httpx.AsyncClient(base_url=self.base_url, timeout=self.timeout)

    async def _get(self, endpoint: str, params: Optional[Dict[str, Any]] = None) -> Dict[str, Any]:
        """Perform a GET request.

        Args:
            endpoint: API endpoint path
            params: Optional query parameters

        Returns:
            Response JSON as dictionary

        Raises:
            httpx.HTTPError: On HTTP errors
        """
        await self._ensure_client()
        try:
            response = await self.client.get(endpoint, params=params)
            response.raise_for_status()
            return response.json()
        except httpx.HTTPError as e:
            logger.error(f"HTTP GET error for {endpoint}: {e}")
            raise

    async def _post(self, endpoint: str, json_data: Optional[Dict[str, Any]] = None, **kwargs) -> Dict[str, Any]:
        """Perform a POST request.

        Args:
            endpoint: API endpoint path
            json_data: JSON data to send
            **kwargs: Additional arguments to pass to httpx

        Returns:
            Response JSON as dictionary

        Raises:
            httpx.HTTPError: On HTTP errors
        """
        await self._ensure_client()
        try:
            response = await self.client.post(endpoint, json=json_data, **kwargs)
            response.raise_for_status()
            return response.json()
        except httpx.HTTPError as e:
            logger.error(f"HTTP POST error for {endpoint}: {e}")
            raise

    async def _put(self, endpoint: str, json_data: Optional[Dict[str, Any]] = None) -> Dict[str, Any]:
        """Perform a PUT request.

        Args:
            endpoint: API endpoint path
            json_data: JSON data to send

        Returns:
            Response JSON as dictionary

        Raises:
            httpx.HTTPError: On HTTP errors
        """
        await self._ensure_client()
        try:
            response = await self.client.put(endpoint, json=json_data)
            response.raise_for_status()
            return response.json()
        except httpx.HTTPError as e:
            logger.error(f"HTTP PUT error for {endpoint}: {e}")
            raise

    # System Service endpoints

    async def get_system_status(self) -> Dict[str, Any]:
        """Get system status information.

        Returns:
            System status data
        """
        return await self._get("/api/system-status")

    async def get_server_mode(self) -> Dict[str, Any]:
        """Get server mode configuration.

        Returns:
            Server mode information
        """
        return await self._get("/api/server-mode")

    async def get_timedatectl_status(self) -> Dict[str, Any]:
        """Get timedatectl status.

        Returns:
            Timedatectl status data
        """
        return await self._get("/api/timedatectl-status")

    async def get_available_services(self, config_group: Optional[str] = None) -> Dict[str, Any]:
        """Get list of available configuration services.

        Args:
            config_group: Optional config group name

        Returns:
            Available services data
        """
        params = {"config_group": config_group} if config_group else None
        return await self._get("/api/available-services", params=params)

    # Systemd Service endpoints

    async def get_systemd_services(self) -> Dict[str, Any]:
        """Get status of all systemd services.

        Returns:
            List of service status information
        """
        return await self._get("/api/systemd/services")

    async def systemd_service_action(self, service: str, action: str) -> Dict[str, Any]:
        """Perform an action on a systemd service.

        Args:
            service: Service name
            action: Action to perform (start, stop, restart)

        Returns:
            Action result
        """
        data = {"service": service, "action": action}
        return await self._post("/api/systemd/action", json_data=data)

    async def systemd_reboot(self) -> Dict[str, Any]:
        """Reboot the system.

        Returns:
            Reboot confirmation
        """
        return await self._post("/api/systemd/reboot")

    async def get_systemd_logs(self, service: str, lines: int = 100) -> str:
        """Get logs for a systemd service.

        Args:
            service: Service name
            lines: Number of lines to retrieve

        Returns:
            Log content as string
        """
        await self._ensure_client()
        try:
            # Note: This endpoint returns plain text, not JSON
            response = await self.client.get(f"/api/systemd/logs/{service}", params={"lines": lines})
            response.raise_for_status()
            return response.text
        except httpx.HTTPError as e:
            logger.error(f"HTTP GET error for logs/{service}: {e}")
            raise

    # Upload Service endpoints

    async def upload_config(
        self,
        filename: str,
        content: str,
        config_group: Optional[str] = None,
        restart_service: bool = False,
    ) -> Dict[str, Any]:
        """Upload a configuration file.

        Args:
            filename: Name of the config file
            content: File content (base64 encoded for binary files)
            config_group: Optional config group name
            restart_service: Whether to restart the service after upload

        Returns:
            Upload result
        """
        await self._ensure_client()
        try:
            # Decode base64 content if it looks like it's encoded
            try:
                file_content = base64.b64decode(content)
            except Exception:
                # If not base64, use as-is
                file_content = content.encode("utf-8")

            # Create multipart form data
            files = {"file": (filename, file_content)}
            data = {
                "restart_service": str(restart_service).lower(),
            }
            if config_group:
                data["config_group"] = config_group

            response = await self.client.post("/api/upload", files=files, data=data)
            response.raise_for_status()
            return response.json()
        except httpx.HTTPError as e:
            logger.error(f"HTTP POST error for upload: {e}")
            raise

    async def upload_zip(
        self,
        filename: str,
        content: str,
        config_group: Optional[str] = None,
        restart_services: bool = False,
        pedantic: bool = False,
    ) -> Dict[str, Any]:
        """Upload a zip file containing multiple configuration files.

        Args:
            filename: Name of the zip file
            content: File content (base64 encoded)
            config_group: Optional config group name
            restart_services: Whether to restart services after upload
            pedantic: Reject upload if unknown files are present

        Returns:
            Upload result
        """
        await self._ensure_client()
        try:
            # Decode base64 content
            file_content = base64.b64decode(content)

            # Create multipart form data
            files = {"file": (filename, file_content)}
            data = {
                "restart_services": str(restart_services).lower(),
                "pedantic": str(pedantic).lower(),
            }
            if config_group:
                data["config_group"] = config_group

            response = await self.client.post("/api/upload/zip", files=files, data=data)
            response.raise_for_status()
            return response.json()
        except httpx.HTTPError as e:
            logger.error(f"HTTP POST error for upload/zip: {e}")
            raise

    async def close(self):
        """Close the HTTP client."""
        if self.client:
            await self.client.aclose()
            self.client = None
