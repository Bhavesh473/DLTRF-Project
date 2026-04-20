import logging
from logging.handlers import RotatingFileHandler
import os

class ReplayLogger:
    """
    Custom logger for replay engine with replay/session context.
    """
    
    def __init__(self, name: str, replay_id: str = None, session_id: str = None, component: str = "general"):
        self.name = name
        self.replay_id = replay_id
        self.session_id = session_id
        self.component = component
        
        # Create logger
        self.logger = logging.getLogger(name)
        self.logger.setLevel(logging.INFO)
        
        # Create formatter
        formatter = logging.Formatter(
            '%(asctime)s - %(name)s - %(levelname)s - %(message)s - replay_id: %(replay_id)s - session_id: %(session_id)s - component: %(component)s'
        )
        
        # Console handler
        console_handler = logging.StreamHandler()
        console_handler.setFormatter(formatter)
        self.logger.addHandler(console_handler)
        
        # File handler
        log_dir = "logs"
        os.makedirs(log_dir, exist_ok=True)
        file_handler = RotatingFileHandler(
            f'{log_dir}/{name}.log',
            maxBytes=10*1024*1024,  # 10MB
            backupCount=5
        )
        file_handler.setFormatter(formatter)
        self.logger.addHandler(file_handler)

    def debug(self, message: str):
        """Log debug message."""
        extra = {
            "replay_id": self.replay_id,
            "session_id": self.session_id,
            "component": self.component
        }
        self.logger.debug(message, extra=extra)

    def info(self, message: str):
        """Log info message."""
        extra = {
            "replay_id": self.replay_id,
            "session_id": self.session_id,
            "component": self.component
        }
        self.logger.info(message, extra=extra)

    def warning(self, message: str):
        """Log warning message."""
        extra = {
            "replay_id": self.replay_id,
            "session_id": self.session_id,
            "component": self.component
        }
        self.logger.warning(message, extra=extra)

    def error(self, message: str, exc_info: bool = False):
        """Log error message FIXED - no extra 'exc_info' key"""
        extra = {
            "replay_id": self.replay_id,
            "session_id": self.session_id,
            "component": self.component
        }
        if exc_info:
            self.logger.error(message, extra=extra, exc_info=True)
        else:
            self.logger.error(message, extra=extra)

    def critical(self, message: str, exc_info: bool = False):
        """Log critical message."""
        extra = {
            "replay_id": self.replay_id,
            "session_id": self.session_id,
            "component": self.component
        }
        if exc_info:
            self.logger.critical(message, extra=extra, exc_info=True)
        else:
            self.logger.critical(message, extra=extra)