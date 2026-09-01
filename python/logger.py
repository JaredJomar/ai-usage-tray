"""
Simple file logger
"""
import time
from pathlib import Path

from config import get_log_dir


class Logger:
    """Simple file-based logger"""
    
    _log_file = get_log_dir() / "ai-usage.log"
    _max_lines = 1000
    
    @classmethod
    def log(cls, level: str, provider: str, message: str):
        """Write a log entry"""
        try:
            timestamp = time.strftime("%Y-%m-%d %H:%M:%S")
            line = f"[{timestamp}] [{level:5}] [{provider}] {message}\n"
            
            with open(cls._log_file, "a") as f:
                f.write(line)
            
            # Rotate if too big
            cls._rotate_if_needed()
        except Exception:
            pass  # Logging should never crash the app
    
    @classmethod
    def _rotate_if_needed(cls):
        """Rotate log file if it exceeds max lines"""
        try:
            if cls._log_file.exists():
                lines = cls._log_file.read_text().splitlines()
                if len(lines) > cls._max_lines:
                    trimmed = lines[-cls._max_lines:]
                    cls._log_file.write_text("\n".join(trimmed) + "\n")
        except Exception:
            pass
    
    @classmethod
    def info(cls, provider: str, message: str):
        cls.log("INFO", provider, message)
    
    @classmethod
    def warn(cls, provider: str, message: str):
        cls.log("WARN", provider, message)
    
    @classmethod
    def error(cls, provider: str, message: str):
        cls.log("ERROR", provider, message)
