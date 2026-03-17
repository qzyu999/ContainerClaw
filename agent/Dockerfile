# Use a minimal Python base image
FROM python:3.11-slim-bookworm as builder

# TEMPORARY FLUSS PYTHON LIBRARY WORKAROUND START
# Install Rust and System Build Tools
RUN apt-get update && apt-get install -y \
    curl build-essential pkg-config libssl-dev protobuf-compiler \
    && rm -rf /var/lib/apt/lists/*

# Install Rust via rustup
RUN curl --proto '=https' --tlsv1.2 -sSf https://sh.rustup.rs | sh -s -- -y
ENV PATH="/root/.cargo/bin:${PATH}"
# TEMPORARY FLUSS PYTHON LIBRARY WORKAROUND END

WORKDIR /app

# TEMPORARY FLUSS PYTHON LIBRARY WORKAROUND START
# Compile your custom Fluss-Rust bindings
# Assumes you added the submodule at vendor/fluss-rust
COPY vendor/fluss-rust /opt/fluss-rust
WORKDIR /opt/fluss-rust/bindings/python
RUN pip install maturin
# This creates a Linux-compatible .whl file in /wheels
RUN maturin build --release --out /wheels
# TEMPORARY FLUSS PYTHON LIBRARY WORKAROUND END

COPY agent/requirements.txt .
RUN pip install --no-cache-dir --prefix=/install -r requirements.txt

# Final stage
FROM python:3.11-slim-bookworm

# Create a non-root user
RUN groupadd -g 1000 agent && \
    useradd -u 1000 -g agent -m agent

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
