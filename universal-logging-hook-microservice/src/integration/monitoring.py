# src/integration/monitoring.py

import time
from datetime import datetime
from client_libs.python.universal_logger import UniversalLogger  # Assuming this path

def check_health(api_url, auth_token=None, interval=60):
    """
    Periodically checks the health of the logging microservice and logs the status.
    
    Args:
        api_url (str): The base URL of the logging microservice API.
        auth_token (str, optional): Authentication token for API requests.
        interval (int, optional): Time interval between checks in seconds (default: 60).
    
    Raises:
        Exception: If the health check fails or API request encounters an error.
    """
    logger = UniversalLogger(api_url, auth_token)
    
    while True:
        try:
            # Mock health check (replace with actual endpoint when available)
            # import requests
            # response = requests.get(f'{api_url}/health', headers={'Authorization': f'Bearer {auth_token}'})
            # is_healthy = response.status_code == 200
            is_healthy = True  # Placeholder; replace with real check
            
            payload = {
                'timestamp': datetime.utcnow().isoformat() + 'Z',
                'level': 'INFO' if is_healthy else 'ERROR',
                'message': f'Health check: {"Healthy" if is_healthy else "Unhealthy"}',
                'source': 'monitoring',
                'metadata': {'status': 'up' if is_healthy else 'down'}
            }
            logger.log(payload['level'], payload['message'], payload['source'], payload['metadata'])
            
            if not is_healthy:
                raise Exception("Service is unhealthy")
                
        except Exception as e:
            print(f"Monitoring error: {e}")
        
        time.sleep(interval)

def collect_metrics(api_url, auth_token=None, interval=300):
    """
    Periodically collects and logs basic metrics (e.g., request count, latency).
    
    Args:
        api_url (str): The base URL of the logging microservice API.
        auth_token (str, optional): Authentication token for API requests.
        interval (int, optional): Time interval between metric collections in seconds (default: 300).
    
    Raises:
        Exception: If metric collection or logging fails.
    """
    logger = UniversalLogger(api_url, auth_token)
    
    while True:
        try:
            # Mock metrics (replace with actual data collection when available)
            metrics = {
                'requests_per_minute': 50,
                'average_latency_ms': 25
            }
            
            payload = {
                'timestamp': datetime.utcnow().isoformat() + 'Z',
                'level': 'INFO',
                'message': 'Collected metrics',
                'source': 'monitoring',
                'metadata': metrics
            }
            logger.log(payload['level'], payload['message'], payload['source'], payload['metadata'])
            
        except Exception as e:
            print(f"Metrics collection error: {e}")
        
        time.sleep(interval)

# Example usage (uncomment to test locally)
# if __name__ == "__main__":
#     check_health('http://localhost:8000', interval=10)  # Run health check every 10 seconds
#     # collect_metrics('http://localhost:8000', interval=30)  # Run metrics every 5 minutes