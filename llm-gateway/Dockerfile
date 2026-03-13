FROM python:3.11-slim-bookworm

WORKDIR /app

RUN apt-get update && apt-get install -y ca-certificates curl dnsutils iputils-ping && rm -rf /var/lib/apt/lists/*

RUN pip install --no-cache-dir flask requests gunicorn certifi

COPY src/ ./src/

# Run as nobody for maximum isolation
USER 65534:65534

CMD ["gunicorn", "--bind", "0.0.0.0:8000", "src.main:app"]
