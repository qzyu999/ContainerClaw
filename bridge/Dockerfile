FROM python:3.12-slim-bookworm

# Force apt to use HTTPS (required on networks that block port 80)
RUN sed -i 's|http://|https://|g' /etc/apt/sources.list.d/debian.sources 2>/dev/null; \
    sed -i 's|http://|https://|g' /etc/apt/sources.list 2>/dev/null; true

WORKDIR /app

# Install pip dependencies (includes pyfluss from PyPI)
COPY bridge/requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

# Copy source and proto definition (single source of truth: proto/)
COPY proto/ ./proto/
COPY bridge/src/ ./src/

# Generate gRPC code
RUN mkdir -p src/proto && \
    python3 -m grpc_tools.protoc -I./proto --python_out=./src/proto --grpc_python_out=./src/proto ./proto/agent.proto

EXPOSE 5001

CMD ["python", "src/bridge.py"]
