FROM python:3.11-slim-bookworm

WORKDIR /app

RUN apt-get update && apt-get install -y ca-certificates curl dnsutils iputils-ping && rm -rf /var/lib/apt/lists/*

RUN pip install --no-cache-dir fastapi uvicorn httpx tenacity gunicorn pyyaml

COPY src/ ./src/

# Run as nobody for maximum isolation
# USER 65534:65534

CMD ["gunicorn", "src.main:app", "-w", "4", "-k", "uvicorn.workers.UvicornWorker", "--bind", "0.0.0.0:8000"]
