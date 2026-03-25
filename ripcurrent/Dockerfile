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

# Install Rust
RUN curl --proto '=https' --tlsv1.2 -sSf https://sh.rustup.rs | sh -s -- -y --no-modify-path

WORKDIR /app

# Compile Fluss-Rust bindings
COPY vendor/fluss-rust /opt/fluss-rust
WORKDIR /opt/fluss-rust/bindings/python
RUN pip install maturin
RUN maturin build --release --out /wheels

WORKDIR /app
COPY ripcurrent/requirements.txt .
RUN pip install --no-cache-dir --prefix=/install -r requirements.txt

# Final stage
FROM python:3.12-slim-bookworm

WORKDIR /app

COPY --from=builder /install /usr/local
# Copy and install the custom Rust-backed Fluss wheel
COPY --from=builder /wheels/*.whl /tmp/
RUN pip install /tmp/*.whl && rm /tmp/*.whl

COPY ripcurrent/src/ ./src/

ENV PYTHONUNBUFFERED=1
ENV PYTHONPATH=/app/src

CMD ["python", "src/main.py"]
