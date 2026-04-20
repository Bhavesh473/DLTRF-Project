## 4. *deployment.md*
(New: Step-by-step deployment guide, matching your docker-compose.yml.)

markdown
# Deployment Guide

Deploy the Universal Logging Hook Microservice using Docker Compose for development or Kubernetes for production.

## Prerequisites
- Docker & Docker Compose installed.
- Python 3.10+ for dashboard.
- Git clone: `git clone https://github.com/Bhavesh473/universal-logging-hook-microservice && cd universal-logging-hook-microservice`

## Development Deployment (Local)
1. **Setup Configs:**
   - Copy `.env.example` to `.env` and set `API_KEY=dev-secret-key`.
   - Ensure `development.yml` is configured (see previous fixes).

2. **Start Services:**
   bash
   docker-compose up -d  # Starts Nginx, Juice Shop, Fluentd, Redis
   sleep 15  # Wait for init
   docker ps  # Verify all Up
3. Run Dashboard:
   pip install -r requirements.txt
   python dashboard.py  # Runs on http://localhost:5000
4. Test:
   Visit http://localhost:3000 (Juice Shop via Nginx).
   Perform actions (login, add to cart).
   Check dashboard: Real logs should appear.
5. Stop:
   docker-compose down
   Ctrl+C  # For dashboard

