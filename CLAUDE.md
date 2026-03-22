# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## Project Overview

Employee time-tracking (clock-in/clock-out) web application built for **VD Vleuten**. Dutch-language UI. Employees clock in/out via a kiosk interface; admins manage employees, view logs, reports, schedules, and export data.

## Tech Stack

- **Backend:** Python/Flask (single `app.py`), SQLite (`timeclock.db`), no ORM
- **Frontend:** Jinja2 templates, vanilla CSS (no JS framework), PWA-enabled (service worker + manifest)
- **Excel export:** `openpyxl` + `Pillow` in `excel_export.py`
- **Virtual environment:** `venv/` directory

## Commands

```bash
# Setup
python -m venv venv
source venv/Scripts/activate   # Windows Git Bash
pip install -r requirements.txt
pip install openpyxl Pillow     # for Excel export (not in requirements.txt)

# Initialize database (creates timeclock.db with tables + indexes + default settings)
python init_db.py

# Seed sample employees (optional)
python seed_employees.py

# Run dev server
python app.py                   # runs on http://0.0.0.0:5000, debug=True
```

## Architecture

### Single-file Flask app (`app.py`)
All routes, helpers, and business logic live in `app.py`. No blueprints or separate route modules.

**Key areas:**
- **Kiosk routes** (`/`, `/clock`, `/status/<code>`) — public-facing clock-in/out interface
- **Admin routes** (`/dashboard`, `/logs`, `/employees`, `/rapport`, `/roosters`, `/instellingen`) — protected by `@admin_required` decorator (session-based auth)
- **API routes** (`/api/alerts`, `/api/schedule-today/<code>`) — JSON endpoints for dashboard alerts and kiosk schedule hints
- **Export routes** (`/export/excel`, `/export/csv`, `/export/uren`) — data export for admins

### Database (`timeclock.db`, SQLite)
Four tables — schema defined in `init_db.py`:
- `employees` (id, code, name, active, pin)
- `time_entries` (id, code, action, timestamp, reason)
- `settings` (key, value) — admin password hash, pin_mode, alert_threshold
- `schedules` (id, employee_code, day_of_week, start_time, end_time)

### Clock action state machine
Actions follow strict transitions: `in → uit|pauze_in`, `pauze_in → pauze_uit`, `pauze_uit → in|uit|pauze_in`, `uit → in`. Validated both in `/clock` endpoint and `validate_entry_sequence()`.

### Excel export (`excel_export.py`)
Generates styled `.xlsx` with VD Vleuten branding (blue/lime/orange color scheme). Tab 1: summary overview of all employees. Tab 2+: one sheet per employee with daily detail. Uses company logo from `static/vdv_logo.png`.

### Templates (`templates/`)
Jinja2 templates. Admin pages include `sidebar.html` for navigation. `index.html` is the kiosk view with inline JS for clock interactions, PIN overlay, and late-reason dialog.

### Styling (`static/style.css`)
Single CSS file with VD Vleuten brand colors as CSS custom properties. Responsive design with mobile breakpoint at 768px. Fonts: Outfit (UI) + JetBrains Mono (numbers/code).

## Key Conventions

- **Language:** All UI text, variable names in templates, and user-facing messages are in Dutch
- **Auth:** Admin password stored as SHA-256 hash in `settings` table (default: `admin1234`)
- **PIN mode:** Optional kiosk PIN verification per employee (4-8 digit, SHA-256 hashed)
- **Late detection:** Compares clock-in time against employee schedule; >2 min late triggers a reason prompt
- **Time format:** Stored as `YYYY-MM-DD HH:MM:SS` strings in SQLite; `parse_timestamp()` also accepts `HH:MM`
- **Rate limiting:** In-memory per-IP, 15 requests per 60 seconds on `/clock`
- **DB connections:** Opened per-request via `get_db_connection()` with WAL mode and foreign keys enabled
