# Use a minimal Python base image
FROM python:3.11-slim-bookworm as builder

WORKDIR /app

# Install build dependencies if needed
# RUN apt-get update && apt-get install -y build-essential

COPY requirements.txt .
RUN pip install --no-cache-dir --prefix=/install -r requirements.txt

# Final stage
FROM python:3.11-slim-bookworm

# Create a non-root user
RUN groupadd -g 1000 agent && \
    useradd -u 1000 -g agent -m agent

COPY --from=builder /install /usr/local

WORKDIR /app
COPY src/ ./src/

# Set ownership to the non-root user
RUN chown -R agent:agent /app

USER agent

# Environment variables
ENV PYTHONUNBUFFERED=1

CMD ["python", "src/main.py"]
