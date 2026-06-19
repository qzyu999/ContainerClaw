FROM python:3.11-slim-bookworm

WORKDIR /app

# Force apt to use HTTPS (required on networks that block port 80)
RUN sed -i 's|http://|https://|g' /etc/apt/sources.list.d/debian.sources 2>/dev/null; \
    sed -i 's|http://|https://|g' /etc/apt/sources.list 2>/dev/null; true
RUN apt-get update && apt-get install -y ca-certificates curl dnsutils iputils-ping && rm -rf /var/lib/apt/lists/*

RUN pip install --no-cache-dir fastapi uvicorn httpx tenacity gunicorn pyyaml pydantic

COPY src/ ./src/

# Run as nobody for maximum isolation
# USER 65534:65534

CMD ["gunicorn", "src.main:app", "-w", "4", "-k", "uvicorn.workers.UvicornWorker", "--bind", "0.0.0.0:8000"]
