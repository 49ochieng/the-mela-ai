FROM python:3.12-slim

WORKDIR /app

# Install Microsoft ODBC Driver 18 for SQL Server (required by aioodbc / pyodbc).
# python:3.12-slim is Debian 12 (Bookworm).
RUN apt-get update \
    && apt-get install -y --no-install-recommends curl gnupg2 apt-transport-https ca-certificates \
    && curl -fsSL https://packages.microsoft.com/keys/microsoft.asc | gpg --dearmor -o /usr/share/keyrings/microsoft.gpg \
    && echo "deb [arch=amd64 signed-by=/usr/share/keyrings/microsoft.gpg] https://packages.microsoft.com/debian/12/prod bookworm main" \
       > /etc/apt/sources.list.d/mssql-release.list \
    && apt-get update \
    && ACCEPT_EULA=Y apt-get install -y --no-install-recommends msodbcsql18 unixodbc-dev gcc g++ \
    && rm -rf /var/lib/apt/lists/*

COPY requirements.txt .
RUN pip install --upgrade pip setuptools \
    && pip install --no-cache-dir -r requirements.txt

COPY . .

# Defense-in-depth: refuse to build an image that has any .env file
# baked in. The .dockerignore already excludes them, but a misconfigured
# build context (or a CI step that copies dev secrets in) would silently
# leak credentials into a published layer. Fail loud here.
RUN set -e; \
    leaked="$(find /app -maxdepth 4 -type f \( -name '.env' -o -name '.env.*' \) ! -name '.env.example' ! -name '.env.prod.sample' ! -name '.env.dev.sample' ! -name '.env.local.sample' 2>/dev/null || true)"; \
    if [ -n "$leaked" ]; then \
      echo "ERROR: .env file(s) leaked into image — refusing to build:"; \
      echo "$leaked"; \
      exit 1; \
    fi

EXPOSE 8000
CMD ["gunicorn", "-k", "uvicorn.workers.UvicornWorker", "app.main:app", \
     "--bind", "0.0.0.0:8000", "--workers", "2", "--timeout", "120"]
