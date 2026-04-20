# API Specification for Universal Logging Hook Microservice

This document outlines the OpenAPI/Swagger specifications for the core API endpoints. The microservice uses FastAPI, which automatically generates interactive Swagger UI documentation at /docs and OpenAPI JSON at /openapi.json when the server is running.

## Base URL
- / (all endpoints are relative to the server host, e.g., http://localhost:8000)

## Authentication
- All endpoints require an API key passed in the header: X-API-KEY.
- Unauthorized requests return 403 Forbidden.

## Endpoints

### # POST /logs
- *Description: **Request body*: Submit a log entry for processing and storage.
- *Request Body* (JSON):
json
{
    "level": "string" (e.g., "INFO", "ERROR"),
    "message": "string",
    "source": "string" (e.g., "juice-proxy"),
    "metadata": {} (optional dictionary)
}
