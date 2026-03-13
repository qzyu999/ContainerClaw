FROM python:3.11-slim-bookworm

WORKDIR /app

# Install dependencies
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

# Copy source and proto generated files
# We expect the generated protos to be in a shared location or copied
COPY src/ ./src/
# Protos will be handled by the build script or volume

EXPOSE 5001

CMD ["python", "src/bridge.py"]
