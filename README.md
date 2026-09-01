# AI Usage Tray

Windows system tray app for monitoring OpenAI Codex usage, with browser-based PKCE authentication.

## Features

- **System tray icon** that changes color based on usage (green → amber → red)
- **Tooltip with progress bars** using Unicode `▰▱` characters
- **Browser-based OAuth** — PKCE flow matching Codex CLI (no device codes)
- **Live usage display** in settings window (5h hourly + 7d weekly windows)
- **Reset credits** counter with countdown timers
- **Launch on Startup** via Windows registry toggle
- **Catppuccin Mocha** themed settings window with PyQt6
- **Standalone .exe** — no Python installation required

## Screenshot

```
Codex — AI Usage
─────────────────
5h   ▰▱▱▱▱▱   1%  ↻ 1h 51m
7d   ▰▱▱▱▱▱   0%  ↻ 6d 21h
Resets: 0
```

## Quick Start

### Run from Python

```bash
cd python
pip install -r requirements.txt
python main.py
```

### Run the .exe

Download `AIUsageTray.exe` from the Programs folder and double-click. No Python required.

## Authentication

1. Right-click the tray icon → **Login to Codex**
2. Browser opens to OpenAI login
3. Authenticate with your OpenAI account
4. Token is saved to `~/.codex/auth.json`

The app uses the same PKCE OAuth flow as the Codex CLI — authorization code with S256 code verifier, no device codes or API keys needed.

## Auto-Start

- **Tray menu**: Right-click → **Launch on Startup**
- **Settings window**: Startup tab → **Launch on Windows Startup**

Uses `HKCU\Software\Microsoft\Windows\CurrentVersion\Run` registry key.

## Configuration

Stored in `%LocalAppData%\AI-Usage-Tray\config.json`:

```json
{
  "refresh_interval_seconds": 300,
  "cache_ttl_seconds": 55,
  "notifications_enabled": true,
  "notify_warn_threshold": 80,
  "notify_critical_threshold": 95,
  "notify_cooldown_minutes": 15,
  "codex_enabled": true,
  "launch_on_startup": false,
  "display_mode": "icon"
}
```

## API

Usage data is fetched from:
```
GET https://chatgpt.com/backend-api/wham/usage
Authorization: Bearer {access_token}
User-Agent: codex_cli_rs
```

Response includes `rate_limit.primary_window` (5h), `rate_limit.secondary_window` (7d), `rate_limit_reset_credits`, and `plan_type`.

## Architecture

```
AI-Usage-Tray/
├── python/
│   ├── main.py              # Entry point
│   ├── config.py            # Configuration management
│   ├── auth.py              # PKCE browser OAuth + token refresh
│   ├── tray.py              # Tray icon, tooltip, menu, auto-start
│   ├── settings_window.py   # PyQt6 settings with live usage display
│   ├── cache.py             # TTL cache with atomic writes
│   ├── logger.py            # File logging
│   ├── providers/
│   │   ├── __init__.py
│   │   └── codex.py         # Codex usage API
│   ├── icon.ico             # App icon
│   └── requirements.txt
└── README.md
```

## Credits

- Inspired by [Omarchy](https://omarchy.org/)'s AI usage widget
- Codex OAuth flow based on [Codex CLI](https://github.com/openai/codex)

## License

MIT
