# =============================================================================
# Mela AI - Backend Dockerfile
# Multi-stage build for FastAPI Python 3.11 application
# =============================================================================

# ---------------------------------------------------------------------------
# Stage 1: Builder - install dependencies in a temporary image
# ---------------------------------------------------------------------------
FROM python:3.11-slim AS builder

WORKDIR /build

# Install system dependencies required for pyodbc and compilation
RUN apt-get update && \
    apt-get install -y --no-install-recommends \
        unixodbc-dev \
        g++ \
        curl \
        gnupg2 \
    && rm -rf /var/lib/apt/lists/*

# Copy requirements first to leverage Docker layer caching
COPY requirements.txt .

# Install Python dependencies into a virtual environment
RUN python -m venv /opt/venv
ENV PATH="/opt/venv/bin:$PATH"

RUN pip install --no-cache-dir --upgrade pip && \
    pip install --no-cache-dir -r requirements.txt

# ---------------------------------------------------------------------------
# Stage 2: Runtime - minimal image with only what we need to run
# ---------------------------------------------------------------------------
FROM python:3.11-slim AS runtime

WORKDIR /app

# Install only the runtime system libraries (no compilers)
RUN apt-get update && \
    apt-get install -y --no-install-recommends \
        unixodbc \
        curl \
    && rm -rf /var/lib/apt/lists/*

# Copy the virtual environment from the builder stage
COPY --from=builder /opt/venv /opt/venv
ENV PATH="/opt/venv/bin:$PATH"

# Create a non-root user for security
RUN groupadd --gid 1000 appuser && \
    useradd --uid 1000 --gid 1000 --create-home appuser

# Copy application code
COPY ./app ./app

# Switch to non-root user
USER appuser

# Expose the FastAPI port
EXPOSE 8000

# Health check
HEALTHCHECK --interval=30s --timeout=10s --start-period=15s --retries=3 \
    CMD curl -f http://localhost:8000/health || exit 1

# Run the application with uvicorn
CMD ["uvicorn", "app.main:app", "--host", "0.0.0.0", "--port", "8000", "--workers", "4"]
