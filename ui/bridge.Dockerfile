FROM python:3.11-slim-bookworm

WORKDIR /app

# Install dependencies
COPY ui/requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

# Copy source and proto generated files
COPY proto/ ./proto/
COPY ui/src/ ./src/

# Generate gRPC code
RUN mkdir -p src/proto && \
    python3 -m grpc_tools.protoc -I./proto --python_out=./src/proto --grpc_python_out=./src/proto ./proto/agent.proto

EXPOSE 5001

CMD ["python", "src/bridge.py"]
