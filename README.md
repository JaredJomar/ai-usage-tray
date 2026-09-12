# AI Usage Tray

Windows system tray app for monitoring OpenAI Codex usage, with browser-based PKCE authentication.

## Features

- **System tray icon** that changes color based on usage:
  - 🟢 Green: 0–59%
  - 🟠 Orange: 60–84%
  - 🔴 Red: 85%+
- **Tooltip with compact progress bars** and reset countdowns
- **Browser-based OAuth** — PKCE flow matching Codex CLI (no device codes)
- **Live usage display** in settings window (5h hourly + 7d weekly windows)
- **Reset credits** counter with countdown timers
- **Dashboard theme toggle** with persistent light and dark modes
- **Recent model activity** showing models used within the last 72 hours
- **Launch on Startup** via Windows registry toggle
- **Catppuccin Mocha** themed settings window with PyQt6
- **Standalone .exe** — no Python installation required

## Screenshots

### Tray tooltip

```
CODEX USAGE
5h | [#####-] |  86% | reset 3h 8m
7d | [##----] |  34% | reset 5d 1h
RESETS: 1 | expires 42m
```

The tray icon is green below 60%, orange from 60% through 84%, and red at
85% or higher. Reset-credit notifications appear one hour and fifteen minutes
before the nearest available credit expires.

### Dashboard

```text
┌────────────────────────────────────────────┐
│ CODEX • LIVE LIMITS    Usage dashboard  X  │
│ Current limits loaded               Refresh│
│                                            │
│ 5-HOUR WINDOW       86%   WEEKLY WINDOW 34%│
│ Resets: 3h 8m             Resets: 5d 1h    │
│                                            │
│ Plan: ChatGPT Plus                         │
│ Reset credits: 1                           │
│ Next reset credit expiry: 42m              │
│                                            │
│ Local activity                             │
│ LAST 24 HOURS  3  LAST 7 DAYS  12  ALL  48 │
│ Usage history: collecting snapshots…       │
│                                            │
│ Tokens & models                            │
│ Tokens used (local aggregate): 12,345      │
└────────────────────────────────────────────┘
```

The dashboard opens from the tray icon and includes live limit cards, reset
credit details, local session activity, usage history, and locally observed
token/model metadata when available. The model section shows only activity from
the last 72 hours, and the theme button switches between light and dark mode.

## Quick Start

```bash
cd python
pip install -r requirements.txt
python main.py
```

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
  "reset_credit_notify_60_minutes": 60,
  "reset_credit_notify_15_minutes": 15,
  "codex_enabled": true,
  "launch_on_startup": false,
  "display_mode": "icon",
  "dark_mode": false
}
```

## API

Usage data is fetched from:
```
GET https://chatgpt.com/backend-api/wham/usage
Authorization: Bearer {access_token}
User-Agent: codex_cli_rs
```

Response includes `rate_limit.primary_window` (5h), `rate_limit.secondary_window` (7d),
`rate_limit_reset_credits`, and `plan_type`. Available reset-credit expiry details
are fetched from:
```
GET https://chatgpt.com/backend-api/wham/rate-limit-reset-credits
Authorization: Bearer {access_token}
```

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
