FROM python:3.12-slim-bookworm

# Force apt to use HTTPS (required on networks that block port 80)
RUN sed -i 's|http://|https://|g' /etc/apt/sources.list.d/debian.sources 2>/dev/null; \
    sed -i 's|http://|https://|g' /etc/apt/sources.list 2>/dev/null; true

# Create a non-root user
RUN groupadd -g 1000 agent && \
    useradd -u 1000 -g agent -m agent

# Install git for DiffFile/DiffTool support
RUN apt-get update && apt-get install -y --no-install-recommends git && rm -rf /var/lib/apt/lists/*

WORKDIR /app

# Install pip dependencies (includes pyfluss from PyPI)
COPY agent/requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

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
