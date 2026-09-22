FROM python:3.12-slim-bookworm

WORKDIR /app

# Install pip dependencies (includes pyfluss from PyPI)
COPY ripcurrent/requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

COPY ripcurrent/src/ ./src/

ENV PYTHONUNBUFFERED=1
ENV PYTHONPATH=/app/src

CMD ["python", "src/main.py"]
