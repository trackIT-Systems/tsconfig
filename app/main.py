import socket
from pathlib import Path
from typing import Any, Dict, List

from fastapi import FastAPI, HTTPException, Request
from fastapi.staticfiles import StaticFiles
from fastapi.templating import Jinja2Templates
from pydantic import BaseModel, Field

from app import __version__
from app.config import load_config, save_config, validate_config

app = FastAPI(title="tsOS Configuration")

# Mount static files
app.mount("/static", StaticFiles(directory="app/static"), name="static")

# Templates
templates = Jinja2Templates(directory="app/templates")


# Pydantic models for request/response validation
class ScheduleEntry(BaseModel):
    name: str
    start: str
    stop: str


class ConfigUpdate(BaseModel):
    lat: float = Field(..., ge=-90, le=90)
    lon: float = Field(..., ge=-180, le=180)
    force_on: bool
    button_delay: str
    schedule: List[ScheduleEntry]


@app.get("/")
async def home(request: Request):
    """Render the main configuration page."""
    hostname = socket.gethostname()
    return templates.TemplateResponse(
        "index.html", {"request": request, "title": f"tsOS Configuration @ {hostname}", "version": __version__}
    )


@app.get("/api/config")
async def get_config() -> Dict[str, Any]:
    """Get the current configuration."""
    try:
        return load_config()
    except FileNotFoundError:
        raise HTTPException(status_code=404, detail="Configuration file not found")


@app.put("/api/config")
async def update_config(config: ConfigUpdate) -> Dict[str, Any]:
    """Update the configuration."""
    # Convert to dict for validation
    config_dict = config.model_dump()

    # Validate the configuration
    errors = validate_config(config_dict)
    if errors:
        raise HTTPException(status_code=400, detail={"message": "Invalid configuration", "errors": errors})

    # Save the configuration
    try:
        save_config(config_dict)
        return {"message": "Configuration updated successfully", "config": config_dict}
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))


@app.post("/api/config/validate")
async def validate_config_endpoint(config: ConfigUpdate) -> Dict[str, Any]:
    """Validate a configuration without saving it."""
    config_dict = config.model_dump()
    errors = validate_config(config_dict)

    if errors:
        return {"valid": False, "errors": errors}
    return {"valid": True, "message": "Configuration is valid"}
