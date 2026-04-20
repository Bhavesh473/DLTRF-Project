FROM python:3.11-slim

WORKDIR /app

# Install dependencies
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

# Copy forwarder code
COPY redis_forwarder.py .

# Expose port
EXPOSE 8200

# Run forwarder
CMD ["uvicorn", "redis_forwarder:app", "--host", "0.0.0.0", "--port", "8200"]