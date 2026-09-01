"""
AI Usage Tray - Windows tray app for monitoring Codex usage
"""
import sys
import signal
from pathlib import Path

# Add current directory to path
sys.path.insert(0, str(Path(__file__).parent))

from config import load_config
from tray import TrayManager
from logger import Logger


def main():
    """Main entry point"""
    Logger.info("main", "Starting AI Usage Tray...")
    
    # Load config
    config = load_config()
    Logger.info("main", f"Config loaded: refresh={config.refresh_interval_seconds}s, cache_ttl={config.cache_ttl_seconds}s")
    
    # Handle Ctrl+C gracefully
    def signal_handler(sig, frame):
        Logger.info("main", "Shutting down...")
        sys.exit(0)
    
    signal.signal(signal.SIGINT, signal_handler)
    signal.signal(signal.SIGTERM, signal_handler)
    
    # Start tray manager
    tray = TrayManager(config)
    
    try:
        tray.run()
    except KeyboardInterrupt:
        Logger.info("main", "Interrupted by user")
    except Exception as e:
        Logger.error("main", f"Fatal error: {e}")
        sys.exit(1)


if __name__ == "__main__":
    main()
