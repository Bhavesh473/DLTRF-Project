#!/bin/bash
# Framework entrypoint with validation

set -e

echo "🚀 Starting DLTRF Framework..."

# Run validation
python3 /app/startup_validator.py

if [ $? -ne 0 ]; then
    echo "❌ Validation failed. Exiting."
    exit 1
fi

# Start services
echo "✅ Starting services..."
exec "$@"