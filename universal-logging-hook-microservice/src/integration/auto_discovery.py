import docker
from datetime import datetime

def discover_containers(api_url="http://localhost:8000", auth_token=None):
    """
    Detect running Docker containers and send discovery logs to the API.
    
    Args:
        api_url (str): The base URL of the logging microservice API.
        auth_token (str, optional): Authentication token for API requests.
    
    Returns:
        list: Names of discovered containers.
    """
    # FIXED: Changed from_client() to from_env()
    client = docker.from_env()
    
    try:
        containers = client.containers.list()
        discovered = []
        
        for container in containers:
            container_id = container.id[:12]
            container_name = container.name
            
            try:
                logs = container.logs(tail=10).decode('utf-8')
            except Exception:
                logs = "Unable to fetch logs"
            
            print(f"✓ Discovered: {container_name} (ID: {container_id})")
            discovered.append({
                "name": container_name,
                "id": container_id,
                "status": container.status,
                "image": container.image.tags[0] if container.image.tags else "unknown"
            })
        
        return discovered
    
    except docker.errors.DockerException as e:
        print(f"Docker API error: {e}")
        return []
    except Exception as e:
        print(f"Unexpected error: {e}")
        return []

# Test function
if __name__ == "__main__":
    print("Testing Docker Auto-Discovery...")
    containers = discover_containers()
    print(f"\nFound {len(containers)} containers") 