# Sensor Schedule Manager

A web-based configuration manager for sensor station schedules. This application provides a user-friendly interface to manage the schedule configuration for sensor stations, including sunrise/sunset-based scheduling.

## Features

- Web-based configuration interface
- Support for sunrise/sunset-based scheduling
- YAML configuration file management
- Responsive design using Bootstrap

## Setup

1. Install PDM (Python Dependency Manager):
```bash
curl -sSL https://raw.githubusercontent.com/pdm-project/pdm/main/install-pdm.py | python3 -
```

2. Install dependencies:
```bash
pdm install
```

3. Run the application:
```bash
pdm run uvicorn app.main:app --reload
```

The application will be available at http://localhost:8000

## Development

This project uses:
- FastAPI for the backend
- Bootstrap for the frontend
- PDM for dependency management
- PyYAML for configuration file handling 