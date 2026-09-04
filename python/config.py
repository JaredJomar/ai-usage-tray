"""
Configuration management for AI Usage Tray
"""
import json
import os
import shutil
from pathlib import Path
from dataclasses import dataclass, asdict

import certifi


@dataclass
class AppConfig:
    refresh_interval_seconds: int = 300
    cache_ttl_seconds: int = 55
    notifications_enabled: bool = True
    notify_warn_threshold: int = 80
    notify_critical_threshold: int = 95
    notify_cooldown_minutes: int = 15
    reset_credit_notify_60_minutes: int = 60
    reset_credit_notify_15_minutes: int = 15
    codex_enabled: bool = True
    launch_on_startup: bool = False
    display_mode: str = "icon"


CONFIG_DIR = Path.home() / "AppData" / "Local" / "AI-Usage-Tray"
CONFIG_FILE = CONFIG_DIR / "config.json"


def load_config() -> AppConfig:
    """Load config from file or create default"""
    CONFIG_DIR.mkdir(parents=True, exist_ok=True)

    if CONFIG_FILE.exists():
        try:
            with open(CONFIG_FILE, "r") as f:
                data = json.load(f)
            return AppConfig(**{k: v for k, v in data.items() if k in AppConfig.__dataclass_fields__})
        except Exception:
            return AppConfig()

    config = AppConfig()
    save_config(config)
    return config


def save_config(config: AppConfig):
    """Save config to file"""
    CONFIG_DIR.mkdir(parents=True, exist_ok=True)
    with open(CONFIG_FILE, "w") as f:
        json.dump(asdict(config), f, indent=2)


def get_codex_auth_path() -> Path:
    """Get Codex auth file path"""
    return Path.home() / ".codex" / "auth.json"


def get_cache_dir() -> Path:
    """Get cache directory"""
    cache_dir = CONFIG_DIR / "cache"
    cache_dir.mkdir(parents=True, exist_ok=True)
    return cache_dir


def get_log_dir() -> Path:
    """Get log directory"""
    log_dir = CONFIG_DIR / "logs"
    log_dir.mkdir(parents=True, exist_ok=True)
    return log_dir


def get_ca_bundle_path() -> str | None:
    bundle_dir = CONFIG_DIR / "certifi"
    bundle_path = bundle_dir / "cacert.pem"
    source_path = Path(certifi.where())

    try:
        if source_path.is_file() and (
            not bundle_path.is_file() or source_path.stat().st_size != bundle_path.stat().st_size
        ):
            bundle_dir.mkdir(parents=True, exist_ok=True)
            temporary_path = bundle_path.with_suffix(".tmp")
            shutil.copyfile(source_path, temporary_path)
            temporary_path.replace(bundle_path)
        if bundle_path.is_file():
            return str(bundle_path)
    except OSError:
        pass
    return None
