FROM python:3.11-slim-bookworm

WORKDIR /app

RUN pip install --no-cache-dir flask requests gunicorn

COPY src/ ./src/

# Run as nobody for maximum isolation
USER 65534:65534

CMD ["gunicorn", "--bind", "0.0.0.0:8000", "src.main:app"]
