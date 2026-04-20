## 2. *architecture.md*
(New: High-level overview with diagram in Markdown, components breakdown.)

```markdown
# Architecture Overview

The Universal Logging Hook Microservice is a scalable, containerized system for capturing, processing, and visualizing logs from web applications (e.g., OWASP Juice Shop). It follows a microservices pattern with loose coupling via Docker networks.

## High-Level Components

| Component | Description | Tech Stack |
|-----------|-------------|------------|
| *Log Sources* | HTTP requests from Nginx proxy (Juice Shop). | Nginx (JSON logging) |
| *Ingestion* | Collects and forwards logs. | Fluentd (aggregator) |
| *Storage/Cache* | Temporary storage for fast queries. | Redis (in-memory) |
| *Processing* | Detects sensitive events (e.g., POST/DELETE). | Python/FastAPI (core logic) |
| *Visualization* | Real-time dashboard with filters/metrics. | Flask (web UI) + Chart.js |
| *Replay Engine* (Future) | Deterministic replay of log sequences. | Python (HTTP client) |

## Data Flow Diagram
