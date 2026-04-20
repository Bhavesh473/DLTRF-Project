FROM python:3.11-slim

WORKDIR /app

# 🎯 THE FIX: Ripped out Node.js, npm, and Newman. 
# We only need gcc (for compiling Python packages) and curl (for the Docker healthcheck).
RUN apt-get update && apt-get install -y \
    gcc \
    curl \
    && rm -rf /var/lib/apt/lists/*

# Install Python dependencies
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

# Install OpenTelemetry for SigNoz (Observability)
RUN pip install --no-cache-dir \
    opentelemetry-api==1.21.0 \
    opentelemetry-sdk==1.21.0 \
    opentelemetry-exporter-otlp==1.21.0 \
    opentelemetry-exporter-otlp-proto-grpc==1.21.0 \
    opentelemetry-instrumentation-fastapi==0.42b0

# Copy application code
COPY . .

# Create non-root user for security
RUN useradd -m -u 1000 replay && chown -R replay:replay /app
USER replay

# Expose API and Dashboard ports
EXPOSE 8000 8050

# Health check to ensure the API is successfully bound before traffic flows
HEALTHCHECK --interval=30s --timeout=10s --start-period=20s \
  CMD curl -f http://localhost:8000/health || exit 1

# Run API & dashboard in parallel
CMD ["sh", "-c", "uvicorn src.api.control_api:app --host 0.0.0.0 --port 8000 --workers 2 & python src/dashboard/server.py"]