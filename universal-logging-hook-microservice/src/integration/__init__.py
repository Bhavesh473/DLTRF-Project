# Import key components (to be populated as other files are developed)
from .auto_discovery import discover_containers  # Docker container detection
from .log_forwarder import forward_logs         # Legacy log forwarding

__all__ = ['discover_containers', 'forward_logs']