import sqlite3
from werkzeug.security import generate_password_hash

conn = sqlite3.connect("timeclock.db")
c = conn.cursor()

c.execute("""CREATE TABLE IF NOT EXISTS employees (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    code TEXT NOT NULL UNIQUE, name TEXT NOT NULL,
    active INTEGER NOT NULL DEFAULT 1, pin TEXT
)""")

c.execute("""CREATE TABLE IF NOT EXISTS time_entries (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    code TEXT NOT NULL, action TEXT NOT NULL,
    timestamp TEXT NOT NULL, reason TEXT
)""")

c.execute("""CREATE TABLE IF NOT EXISTS settings (
    key TEXT PRIMARY KEY, value TEXT NOT NULL
)""")

c.execute("""CREATE TABLE IF NOT EXISTS schedules (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    employee_code TEXT NOT NULL, day_of_week INTEGER NOT NULL,
    start_time TEXT NOT NULL, end_time TEXT NOT NULL,
    UNIQUE(employee_code, day_of_week)
)""")

c.execute("""CREATE TABLE IF NOT EXISTS admin_users (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    username TEXT NOT NULL UNIQUE,
    password_hash TEXT NOT NULL,
    display_name TEXT NOT NULL,
    active INTEGER NOT NULL DEFAULT 1,
    created_at TEXT NOT NULL DEFAULT (datetime('now','localtime'))
)""")

c.execute("""CREATE TABLE IF NOT EXISTS audit_log (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    admin_user TEXT NOT NULL,
    action TEXT NOT NULL,
    target_type TEXT,
    target_id TEXT,
    old_value TEXT,
    new_value TEXT,
    timestamp TEXT NOT NULL DEFAULT (datetime('now','localtime'))
)""")

# Safe migrations
for col, defn in [("active","INTEGER NOT NULL DEFAULT 1"),("pin","TEXT"),("language","TEXT NOT NULL DEFAULT 'nl'"),("birthdate","TEXT")]:
    try: c.execute(f"ALTER TABLE employees ADD COLUMN {col} {defn}")
    except: pass

try: c.execute("ALTER TABLE time_entries ADD COLUMN reason TEXT")
except: pass

# Migration version tracking
c.execute("INSERT OR IGNORE INTO settings VALUES ('db_version', '2')")

# Default settings
c.execute("INSERT OR IGNORE INTO settings VALUES ('admin_password_hash',?)",
          (generate_password_hash("admin1234"),))
c.execute("INSERT OR IGNORE INTO settings VALUES ('pin_mode','0')")
c.execute("INSERT OR IGNORE INTO settings VALUES ('alert_threshold_minutes','600')")

# Default admin user (if admin_users table is empty)
existing = c.execute("SELECT COUNT(*) FROM admin_users").fetchone()[0]
if existing == 0:
    c.execute("INSERT INTO admin_users (username, password_hash, display_name) VALUES (?, ?, ?)",
              ("admin", generate_password_hash("admin1234"), "Beheerder"))

# Indexes
for idx in [
    "CREATE INDEX IF NOT EXISTS idx_entries_code ON time_entries(code)",
    "CREATE INDEX IF NOT EXISTS idx_entries_timestamp ON time_entries(timestamp)",
    "CREATE INDEX IF NOT EXISTS idx_entries_code_ts ON time_entries(code, timestamp)",
    "CREATE INDEX IF NOT EXISTS idx_schedules_code ON schedules(employee_code)",
    "CREATE INDEX IF NOT EXISTS idx_audit_timestamp ON audit_log(timestamp)",
]:
    c.execute(idx)

conn.commit(); conn.close()
print("Database klaar.")
