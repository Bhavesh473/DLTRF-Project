# Client Libraries

This section provides SDKs and examples for integrating with the Universal Logging Hook API from various languages. The core API is RESTful (see [api-specification.md](../docs/api-specification.md)).

## Python Client (Recommended)
Use requests library. Install via pip install requests.

```python
import requests
import json

API_BASE = "http://localhost:8000"
API_KEY = "dev-secret-key"

def submit_log(level, message, source, metadata=None):
    headers = {"X-API-KEY": API_KEY, "Content-Type": "application/json"}
    payload = {
        "level": level,
        "message": message,
        "source": source,
        "metadata": metadata or {}
    }
    response = requests.post(f"{API_BASE}/logs", headers=headers, json=payload)
    return response.json() if response.status_code == 201 else response.json()["detail"]

# Example
result = submit_log("INFO", "User added to cart", "juice-proxy", {"path": "/api/BasketItems/"})
print(result)  # {"id": "uuid", "status": "enqueued"}
