"""
AI Usage Tray - Windows tray app for monitoring Codex usage
"""
import os
import sys
import signal
import ctypes
from ctypes import wintypes
from pathlib import Path

# Add current directory to path
sys.path.insert(0, str(Path(__file__).parent))

from config import load_config
from logger import Logger


_instance_mutex = None
_kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)
_kernel32.CreateMutexW.argtypes = [ctypes.c_void_p, wintypes.BOOL, ctypes.c_wchar_p]
_kernel32.CreateMutexW.restype = ctypes.c_void_p
_kernel32.CloseHandle.argtypes = [ctypes.c_void_p]
_kernel32.CloseHandle.restype = wintypes.BOOL


def acquire_single_instance() -> bool:
    global _instance_mutex
    _instance_mutex = _kernel32.CreateMutexW(None, False, "AIUsageTray.SingleInstance")
    last_error = ctypes.get_last_error()
    if not _instance_mutex:
        Logger.error("main", f"Could not create single-instance mutex (Windows error {last_error})")
        return False
    if last_error == 183:
        _kernel32.CloseHandle(_instance_mutex)
        _instance_mutex = None
        return False
    return True


def main():
    """Main entry point"""
    Logger.info("main", "Starting AI Usage Tray...")
    Logger.info("main", "Process started: pid={} argv={}".format(os.getpid(), sys.argv))

    # Load config
    config = load_config()
    Logger.info("main", f"Config loaded: refresh={config.refresh_interval_seconds}s, cache_ttl={config.cache_ttl_seconds}s")

    if "--dashboard" in sys.argv:
        from dashboard import parse_dashboard_anchor, show_dashboard
        show_dashboard(config, parse_dashboard_anchor(sys.argv[1:]))
        return

    if "--settings" in sys.argv:
        from auth import AuthManager
        from settings_window import show_settings
        show_settings(AuthManager(), config)
        return

    if not acquire_single_instance():
        Logger.info("main", "AI Usage Tray is already running (pid={})".format(os.getpid()))
        return
    Logger.info("main", "Single-instance lock acquired (pid={})".format(os.getpid()))
    
    # Handle Ctrl+C gracefully
    def signal_handler(sig, frame):
        Logger.info("main", "Shutting down...")
        sys.exit(0)
    
    signal.signal(signal.SIGINT, signal_handler)
    signal.signal(signal.SIGTERM, signal_handler)
    
    # Start tray manager (lazy import to avoid tray stack in dashboard child)
    from tray import TrayManager
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
