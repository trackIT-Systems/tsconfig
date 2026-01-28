"""Deployment proxy endpoint for forwarding deployment requests to external API."""

import os
from typing import Any, Dict

import httpx
from fastapi import APIRouter, HTTPException, Path, Request

from app.logging_config import get_logger

logger = get_logger(__name__)
router = APIRouter(prefix="/api/deploy", tags=["deployment"])


@router.post("/{config_group_id}", summary="Deploy configuration to tracker stations")
async def deploy_config(
    request: Request, config_group_id: str = Path(..., description="Configuration group ID to deploy")
) -> Dict[str, Any]:
    """Proxy endpoint to trigger deployment of a configuration group to tracker stations.

    This endpoint forwards the deployment request to the external deployment API,
    which is now behind a firewall and only accessible from localhost.

    Args:
        config_group_id: The ID of the configuration group to deploy

    Returns:
        Deployment result with station details and message

    Raises:
        HTTPException: If deployment fails or service is unavailable
    """
    # Get deployment API base URL from environment (default to localhost:8000)
    deployment_api_url = os.environ.get("DEPLOYMENT_API_URL", "http://localhost:8000")

    # Construct the full deployment endpoint URL
    deploy_url = f"{deployment_api_url}/api/config-groups/{config_group_id}/deploy"

    logger.info(f"Deployment request for config group: {config_group_id}")
    logger.debug(f"Deployment API URL: {deploy_url}")

    # Extract authentication token from the incoming request to forward it
    auth_token = None
    auth_header = request.headers.get("authorization")
    if auth_header and auth_header.startswith("Bearer "):
        auth_token = auth_header[7:]  # Remove "Bearer " prefix
        logger.debug("Found auth token in Authorization header")
    else:
        # Try to get token from cookie
        auth_token = request.cookies.get("auth_token")
        if auth_token:
            logger.debug("Found auth token in cookie")
        else:
            logger.warning("No auth token found in request (neither header nor cookie)")

    # Prepare headers for the deployment API request
    headers = {"Content-Type": "application/json"}
    if auth_token:
        headers["Authorization"] = f"Bearer {auth_token}"
        logger.debug(f"Forwarding auth token (length: {len(auth_token)})")
    else:
        logger.warning("No auth token to forward to deployment API")

    try:
        async with httpx.AsyncClient(timeout=30.0) as client:
            response = await client.post(deploy_url, headers=headers)

            # Handle 404 - config group not found
            if response.status_code == 404:
                error_data = response.json() if response.content else {}
                error_message = error_data.get(
                    "error", f"Config group '{config_group_id}' not found in deployment system"
                )
                logger.error(f"Deployment API 404: {error_data}")
                raise HTTPException(status_code=404, detail=error_message)

            # Handle other non-200 responses
            if response.status_code != 200:
                try:
                    error_data = response.json()
                    error_message = (
                        error_data.get("error")
                        or error_data.get("message")
                        or f"Deployment failed with status {response.status_code}"
                    )
                    logger.error(f"Deployment API error (status {response.status_code}): {error_data}")
                except Exception as parse_error:
                    error_message = f"Deployment failed with status {response.status_code}"
                    logger.error(
                        f"Deployment API error (status {response.status_code}), failed to parse response: {parse_error}"
                    )
                raise HTTPException(status_code=response.status_code, detail=error_message)

            # Return successful deployment result
            return response.json()

    except httpx.TimeoutException:
        raise HTTPException(status_code=504, detail="Deployment service request timed out. Please try again.")
    except httpx.ConnectError:
        raise HTTPException(
            status_code=503,
            detail=f"Deployment service unavailable. Please check if the deployment API is running at {deployment_api_url}.",
        )
    except httpx.RequestError as e:
        raise HTTPException(status_code=503, detail=f"Failed to connect to deployment service: {str(e)}")
    except HTTPException:
        # Re-raise HTTPExceptions we created above
        raise
    except Exception as e:
        # Catch any other unexpected errors
        raise HTTPException(status_code=500, detail=f"Unexpected error during deployment: {str(e)}")
