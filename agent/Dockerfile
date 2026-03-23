# Use a minimal Python base image
FROM python:3.12-slim-bookworm as builder

# --- FLUSS BUILD TOOLS ---
RUN apt-get update && apt-get install -y \
    curl build-essential pkg-config libssl-dev protobuf-compiler \
    && rm -rf /var/lib/apt/lists/*

# Explicitly set Rust environment variables
ENV RUSTUP_HOME=/usr/local/rustup \
    CARGO_HOME=/usr/local/cargo \
    PATH=/usr/local/cargo/bin:$PATH

# Install Rust to the explicit location
RUN curl --proto '=https' --tlsv1.2 -sSf https://sh.rustup.rs | sh -s -- -y --no-modify-path

WORKDIR /app

# Compile Fluss-Rust bindings
COPY vendor/fluss-rust /opt/fluss-rust
WORKDIR /opt/fluss-rust/bindings/python
RUN pip install maturin
# Now maturin will find cargo in /usr/local/cargo/bin
RUN maturin build --release --out /wheels
# --- END FLUSS BUILD ---

COPY agent/requirements.txt .
RUN pip install --no-cache-dir --prefix=/install -r requirements.txt

# Final stage
FROM python:3.12-slim-bookworm

# Create a non-root user
RUN groupadd -g 1000 agent && \
    useradd -u 1000 -g agent -m agent

# Install git for DiffFile/DiffTool support
RUN apt-get update && apt-get install -y --no-install-recommends git && rm -rf /var/lib/apt/lists/*

COPY --from=builder /install /usr/local

# TEMPORARY FLUSS PYTHON LIBRARY WORKAROUND START
# Copy and install the custom Rust-backed Fluss wheel
COPY --from=builder /wheels/*.whl /tmp/
RUN pip install /tmp/*.whl && rm /tmp/*.whl
# TEMPORARY FLUSS PYTHON LIBRARY WORKAROUND END

WORKDIR /app

# Copy proto and generate stubs
# TEMPORARY FLUSS PYTHON LIBRARY WORKAROUND START
RUN pip install grpcio-tools
# TEMPORARY FLUSS PYTHON LIBRARY WORKAROUND END
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
