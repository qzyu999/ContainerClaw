# ── Build stage: compile Fluss Python wheel from Rust bindings ────
FROM python:3.12-slim-bookworm AS fluss-builder

RUN apt-get update && apt-get install -y \
    curl build-essential pkg-config libssl-dev protobuf-compiler \
    && rm -rf /var/lib/apt/lists/*

ENV RUSTUP_HOME=/usr/local/rustup \
    CARGO_HOME=/usr/local/cargo \
    PATH=/usr/local/cargo/bin:$PATH

RUN curl --proto '=https' --tlsv1.2 -sSf https://sh.rustup.rs | sh -s -- -y --no-modify-path

COPY vendor/fluss-rust /opt/fluss-rust
WORKDIR /opt/fluss-rust/bindings/python
RUN pip install maturin
RUN maturin build --release --out /wheels

# ── Final stage ───────────────────────────────────────────────────
FROM python:3.12-slim-bookworm

WORKDIR /app

# Install pip dependencies
COPY bridge/requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

# Install the Fluss wheel built in the previous stage
COPY --from=fluss-builder /wheels/*.whl /tmp/
RUN pip install /tmp/*.whl && rm /tmp/*.whl

# Copy source and proto definition (single source of truth: proto/)
COPY proto/ ./proto/
COPY bridge/src/ ./src/

# Generate gRPC code
RUN mkdir -p src/proto && \
    python3 -m grpc_tools.protoc -I./proto --python_out=./src/proto --grpc_python_out=./src/proto ./proto/agent.proto

EXPOSE 5001

CMD ["python", "src/bridge.py"]
