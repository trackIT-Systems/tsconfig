"""tsOS Configuration Manager."""

import socket
from pathlib import Path

from fastapi import FastAPI, Request
from fastapi.staticfiles import StaticFiles
from fastapi.templating import Jinja2Templates

from app import __version__
from app.routers import radiotracking, schedule

app = FastAPI(title="tsOS Configuration")

# Include routers
app.include_router(schedule.router)
app.include_router(radiotracking.router)

# Mount static files
app.mount("/static", StaticFiles(directory="app/static"), name="static")

# Templates
templates = Jinja2Templates(directory="app/templates")


@app.get("/")
async def home(request: Request):
    """Render the main configuration page."""
    hostname = socket.gethostname()
    return templates.TemplateResponse("index.html", {"request": request, "title": hostname, "version": __version__})
