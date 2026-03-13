# Use a minimal Python base image
FROM python:3.11-slim-bookworm as builder

WORKDIR /app

COPY agent/requirements.txt .
RUN pip install --no-cache-dir --prefix=/install -r requirements.txt

# Final stage
FROM python:3.11-slim-bookworm

# Create a non-root user
RUN groupadd -g 1000 agent && \
    useradd -u 1000 -g agent -m agent

COPY --from=builder /install /usr/local

WORKDIR /app

# Copy proto and generate stubs
COPY proto/ ./proto/
COPY agent/src/ ./src/
RUN python3 -m grpc_tools.protoc -I./proto --python_out=./src --grpc_python_out=./src ./proto/agent.proto

# Set ownership to the non-root user
RUN chown -R agent:agent /app

USER agent

# Environment variables
ENV PYTHONUNBUFFERED=1
ENV PYTHONPATH=/app/src

CMD ["python", "src/main.py"]
