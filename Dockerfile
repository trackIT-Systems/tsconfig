FROM python:3.13-trixie

# Set working directory
WORKDIR /app

# Install system dependencies
RUN apt-get update && apt-get install -y --no-install-recommends \
    curl \
    && rm -rf /var/lib/apt/lists/*

# Copy dependency files first for better caching
COPY pyproject.toml ./

# Copy application code (needed for editable install)
COPY app/ ./app/
COPY configs/ ./configs/

# Install the project in editable mode with dependencies
RUN pip install --no-cache-dir -e .

# Create groups directory for server mode config storage
RUN mkdir -p /app/groups

# Enable server mode via environment variable
ENV TSCONFIG_SERVER_MODE=true
ENV TSCONFIG_CONFIG_ROOT=/app/groups
ENV TSCONFIG_PORT=8000

# Health check to verify the application is running
HEALTHCHECK --interval=30s --timeout=10s --start-period=40s --retries=3 \
    CMD curl -f http://localhost:${TSCONFIG_PORT}/api/server-mode || exit 1

# Run the application
CMD sh -c "uvicorn app.main:app --host 0.0.0.0 --port ${TSCONFIG_PORT}"

