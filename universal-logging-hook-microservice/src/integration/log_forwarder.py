import time
from datetime import datetime
import sys
import os

# FIXED: Proper path handling
sys.path.insert(0, os.path.join(os.path.dirname(__file__), 'client_libs', 'python'))

try:
    from universal_logger import UniversalLogger
except ImportError:
    print("Warning: UniversalLogger not found. Using fallback.")
    UniversalLogger = None

def forward_logs(log_file_path, api_url="http://localhost:8000", auth_token=None, interval=0.1):
    """
    Continuously reads a log file and forwards new lines to the logging microservice API.
    
    Args:
        log_file_path (str): Path to the log file to monitor.
        api_url (str): The base URL of the logging microservice API.
        auth_token (str, optional): Authentication token for API requests.
        interval (float, optional): Time to sleep between checks (seconds).
    """
    if not UniversalLogger:
        print("UniversalLogger not available. Exiting.")
        return
    
    logger = UniversalLogger(api_url, auth_token)
    
    try:
        with open(log_file_path, 'r') as file:
            file.seek(0, 2)  # Move to end of file
            print(f"Monitoring log file: {log_file_path}")
            
            while True:
                line = file.readline()
                if line:
                    logger.log(
                        'INFO',
                        line.strip(),
                        'legacy_forwarder',
                        {'file': log_file_path}
                    )
                time.sleep(interval)
                
    except FileNotFoundError:
        print(f"Log file not found: {log_file_path}")
    except KeyboardInterrupt:
        print("\nLog forwarding stopped")
    except Exception as e:
        print(f"Error forwarding logs: {e}") 