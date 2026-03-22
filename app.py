from flask import Flask, render_template, request, jsonify, Response, redirect, url_for, session, send_file
import sqlite3
from datetime import datetime, timedelta
import csv
from io import StringIO, BytesIO
import secrets
import time
import os
import logging
from logging.handlers import RotatingFileHandler
from functools import wraps
import re
from collections import defaultdict
from werkzeug.security import generate_password_hash, check_password_hash
from dotenv import load_dotenv

load_dotenv()

app = Flask(__name__)
app.secret_key = os.environ.get("SECRET_KEY") or secrets.token_hex(32)

DB_NAME = "timeclock.db"

# ── Logging ──────────────────────────────────────────────────────────────────
if not os.path.exists("logs"):
    os.makedirs("logs")
file_handler = RotatingFileHandler("logs/timeclock.log", maxBytes=1_000_000, backupCount=5)
file_handler.setFormatter(logging.Formatter("[%(asctime)s] %(levelname)s %(message)s"))
file_handler.setLevel(logging.INFO)
app.logger.addHandler(file_handler)
app.logger.setLevel(logging.INFO)
app.logger.info("Timeclock app gestart")

# ── Rate limiting (in-memory, per IP) ────────────────────────────────────────
_rate_limit = {}
RATE_LIMIT_MAX = 15
RATE_LIMIT_WINDOW = 60

def check_rate_limit(ip):
    now = time.time()
    if ip not in _rate_limit:
        _rate_limit[ip] = []
    _rate_limit[ip] = [t for t in _rate_limit[ip] if now - t < RATE_LIMIT_WINDOW]
    if len(_rate_limit[ip]) >= RATE_LIMIT_MAX:
        return False
    _rate_limit[ip].append(now)
    return True

def get_real_ip():
    return request.headers.get("X-Forwarded-For", request.remote_addr).split(",")[0].strip()


# ── DB connection ────────────────────────────────────────────────────────────

def get_db_connection():
    conn = sqlite3.connect(DB_NAME)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA journal_mode=WAL")
    conn.execute("PRAGMA foreign_keys=ON")
    return conn

def ensure_tables():
    conn = get_db_connection()
    conn.execute("""CREATE TABLE IF NOT EXISTS admin_users (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        username TEXT NOT NULL UNIQUE,
        password_hash TEXT NOT NULL,
        display_name TEXT NOT NULL,
        active INTEGER NOT NULL DEFAULT 1,
        created_at TEXT NOT NULL DEFAULT (datetime('now','localtime'))
    )""")
    conn.execute("""CREATE TABLE IF NOT EXISTS audit_log (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        admin_user TEXT NOT NULL,
        action TEXT NOT NULL,
        target_type TEXT,
        target_id TEXT,
        old_value TEXT,
        new_value TEXT,
        timestamp TEXT NOT NULL DEFAULT (datetime('now','localtime'))
    )""")
    conn.execute("CREATE INDEX IF NOT EXISTS idx_audit_timestamp ON audit_log(timestamp)")
    existing = conn.execute("SELECT COUNT(*) FROM admin_users").fetchone()[0]
    if existing == 0:
        conn.execute("INSERT INTO admin_users (username, password_hash, display_name) VALUES (?, ?, ?)",
                      ("admin", generate_password_hash("admin1234"), "Beheerder"))
    conn.commit()
    conn.close()

try:
    ensure_tables()
except:
    pass


# ── CSRF ─────────────────────────────────────────────────────────────────────

@app.before_request
def csrf_protect():
    if request.method == "POST":
        # Skip CSRF for JSON API endpoints (they use Content-Type check)
        if request.is_json:
            return
        token = session.get("csrf_token")
        form_token = request.form.get("csrf_token")
        if not token or token != form_token:
            return "CSRF-token ongeldig. Vernieuw de pagina.", 403

@app.context_processor
def inject_csrf():
    if "csrf_token" not in session:
        session["csrf_token"] = secrets.token_hex(32)
    return {"csrf_token": session["csrf_token"]}


# ── Settings helpers ─────────────────────────────────────────────────────────

def get_setting(key, default=None):
    conn = get_db_connection()
    row = conn.execute("SELECT value FROM settings WHERE key=?", (key,)).fetchone()
    conn.close()
    return row["value"] if row else default

def set_setting(key, value):
    conn = get_db_connection()
    conn.execute("INSERT OR REPLACE INTO settings (key, value) VALUES (?, ?)", (key, str(value)))
    conn.commit()
    conn.close()

def get_admin_password_hash():
    h = get_setting("admin_password_hash")
    if h:
        return h
    return generate_password_hash("admin1234")

def get_pin_mode():
    return get_setting("pin_mode", "0") == "1"

def get_alert_threshold_minutes():
    val = get_setting("alert_threshold_minutes", "600")
    return int(val)


# ── Auth ─────────────────────────────────────────────────────────────────────

def admin_required(f):
    @wraps(f)
    def decorated(*args, **kwargs):
        if not session.get("admin"):
            return redirect(url_for("admin_login", next=request.path))
        return f(*args, **kwargs)
    return decorated

def get_current_admin():
    return session.get("admin_user", "admin")


# ── Audit log ────────────────────────────────────────────────────────────────

def audit_log(action, target_type=None, target_id=None, old_value=None, new_value=None):
    conn = get_db_connection()
    conn.execute(
        "INSERT INTO audit_log (admin_user, action, target_type, target_id, old_value, new_value) VALUES (?,?,?,?,?,?)",
        (get_current_admin(), action, target_type, str(target_id) if target_id else None, old_value, new_value)
    )
    conn.commit()
    conn.close()
    app.logger.info(f"AUDIT [{get_current_admin()}] {action} {target_type}:{target_id}")


# ── Employee helpers ─────────────────────────────────────────────────────────

def get_employee_by_code(code):
    conn = get_db_connection()
    row = conn.execute(
        "SELECT id, code, name, active, pin FROM employees WHERE code = ? AND active = 1", (code,)
    ).fetchone()
    conn.close()
    return row

def get_last_action(code):
    conn = get_db_connection()
    row = conn.execute(
        "SELECT id, action, timestamp FROM time_entries WHERE code = ? ORDER BY datetime(timestamp) DESC, id DESC LIMIT 1",
        (code,)
    ).fetchone()
    conn.close()
    return row

def save_entry(code, action, reason=None):
    timestamp = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    conn = get_db_connection()
    conn.execute(
        "INSERT INTO time_entries (code, action, timestamp, reason) VALUES (?, ?, ?, ?)",
        (code, action, timestamp, reason)
    )
    conn.commit()
    conn.close()
    return timestamp

def get_entry_by_id(entry_id):
    conn = get_db_connection()
    row = conn.execute("SELECT id, code, action, timestamp, reason FROM time_entries WHERE id = ?", (entry_id,)).fetchone()
    conn.close()
    return row

def get_all_entries_for_employee(code):
    conn = get_db_connection()
    rows = conn.execute(
        "SELECT id, code, action, timestamp FROM time_entries WHERE code = ? ORDER BY datetime(timestamp) ASC, id ASC",
        (code,)
    ).fetchall()
    conn.close()
    return rows

def validate_entry_sequence(code, edited_entry_id=None, new_action=None, new_timestamp=None):
    entries = get_all_entries_for_employee(code)
    simulated = []
    for row in entries:
        row_id, action, timestamp = row["id"], row["action"], row["timestamp"]
        if edited_entry_id is not None and row_id == edited_entry_id:
            action, timestamp = new_action, new_timestamp
        simulated.append({"id": row_id, "action": action, "timestamp": timestamp})
    simulated.sort(key=lambda x: (parse_timestamp(x["timestamp"]), x["id"]))
    if not simulated:
        return True, None

    valid_transitions = {
        None: ["in"],
        "in": ["uit", "pauze_in"],
        "pauze_in": ["pauze_uit"],
        "pauze_uit": ["in", "uit", "pauze_in"],
        "uit": ["in"],
    }
    prev_action = None
    for i, current in enumerate(simulated):
        action = current["action"]
        allowed = valid_transitions.get(prev_action, [])
        if action not in allowed:
            return False, f"Ongeldige volgorde: '{action}' na '{prev_action}'."
        if i > 0:
            prev_time = parse_timestamp(simulated[i-1]["timestamp"])
            curr_time = parse_timestamp(current["timestamp"])
            if curr_time <= prev_time:
                return False, "Tijdstip moet later zijn dan de vorige registratie."
        prev_action = action
    return True, None


def parse_timestamp(ts):
    for fmt in ("%Y-%m-%d %H:%M:%S", "%Y-%m-%d %H:%M"):
        try:
            return datetime.strptime(ts, fmt)
        except ValueError:
            continue
    raise ValueError(f"Onbekend tijdformaat: {ts}")

def compute_worked_seconds(entries, exclude_breaks=True):
    worked = 0
    breaks = 0
    pending_in = None
    pending_pause = None

    for row in sorted(entries, key=lambda r: (r["timestamp"], r["id"] if "id" in r.keys() else 0)):
        act = row["action"]
        try:
            ts = parse_timestamp(row["timestamp"])
        except ValueError:
            continue

        if act == "in":
            pending_in = ts
            pending_pause = None
        elif act == "uit" and pending_in:
            worked += (ts - pending_in).total_seconds()
            pending_in = None
        elif act == "pauze_in" and pending_in:
            pending_pause = ts
        elif act == "pauze_uit" and pending_pause:
            b = (ts - pending_pause).total_seconds()
            breaks += b
            pending_pause = None
            pending_in = ts

    net = worked - breaks if exclude_breaks else worked
    return max(0, net), breaks

def format_duration(seconds):
    h = int(seconds // 3600)
    m = int((seconds % 3600) // 60)
    return f"{h}u {m:02d}m"


# ── Admin: Urenoverzicht rapport ─────────────────────────────────────────────

@app.route("/rapport")
@admin_required
def rapport():
    date_from = request.args.get("from", (datetime.now() - timedelta(days=6)).strftime("%Y-%m-%d"))
    date_to = request.args.get("to", datetime.now().strftime("%Y-%m-%d"))
    emp_filter = request.args.get("employee", "")

    query = """SELECT t.code, e.name, t.action, t.timestamp
               FROM time_entries t LEFT JOIN employees e ON t.code=e.code
               WHERE DATE(t.timestamp) >= ? AND DATE(t.timestamp) <= ?"""
    params = [date_from, date_to]
    if emp_filter:
        query += " AND t.code=?"
        params.append(emp_filter)
    query += " ORDER BY t.code, datetime(t.timestamp) ASC"

    conn = get_db_connection()
    rows = conn.execute(query, params).fetchall()
    employees_list = conn.execute("SELECT code, name FROM employees WHERE active=1 ORDER BY name").fetchall()
    conn.close()

    emp_day = defaultdict(lambda: defaultdict(list))
    emp_meta = {}
    for row in rows:
        key = row["code"]
        emp_meta[key] = row["name"] or key
        day = row["timestamp"][:10]
        emp_day[key][day].append(row)

    report = []
    for code in sorted(emp_meta.keys(), key=lambda c: emp_meta[c]):
        name = emp_meta[code]
        days = []
        total_worked = 0
        total_breaks = 0
        for day in sorted(emp_day[code].keys()):
            day_entries = emp_day[code][day]
            worked, brk = compute_worked_seconds(day_entries)
            total_worked += worked
            total_breaks += brk
            days.append({
                "date": day,
                "worked": format_duration(worked),
                "breaks": format_duration(brk),
                "worked_sec": worked,
            })
        report.append({
            "code": code,
            "name": name,
            "days": days,
            "total_worked": format_duration(total_worked),
            "total_breaks": format_duration(total_breaks),
            "total_worked_sec": total_worked,
        })

    return render_template("rapport.html",
        report=report, date_from=date_from, date_to=date_to,
        emp_filter=emp_filter, employees_list=employees_list)


# ── Admin: Instellingen ──────────────────────────────────────────────────────

@app.route("/instellingen", methods=["GET", "POST"])
@admin_required
def instellingen():
    error = None
    success = None
    conn = get_db_connection()

    if request.method == "POST":
        act = request.form.get("_action", "")

        if act == "change_password":
            current = request.form.get("current_password", "")
            new1 = request.form.get("new_password", "")
            new2 = request.form.get("confirm_password", "")
            stored_hash = get_admin_password_hash()
            # Support both old SHA-256 and new werkzeug hashes
            if stored_hash.startswith("pbkdf2:") or stored_hash.startswith("scrypt:"):
                pw_ok = check_password_hash(stored_hash, current)
            else:
                import hashlib
                pw_ok = hashlib.sha256(current.encode()).hexdigest() == stored_hash
            if not pw_ok:
                error = "Huidig wachtwoord is onjuist."
            elif len(new1) < 6:
                error = "Nieuw wachtwoord moet minimaal 6 tekens zijn."
            elif new1 != new2:
                error = "Wachtwoorden komen niet overeen."
            else:
                new_hash = generate_password_hash(new1)
                set_setting("admin_password_hash", new_hash)
                audit_log("change_password", "settings", "admin_password")
                success = "Wachtwoord succesvol gewijzigd."

        elif act == "toggle_pin":
            current_mode = get_pin_mode()
            new_val = "0" if current_mode else "1"
            set_setting("pin_mode", new_val)
            audit_log("toggle_pin", "settings", "pin_mode", str(int(current_mode)), new_val)
            success = f"PIN-modus {'ingeschakeld' if new_val == '1' else 'uitgeschakeld'}."

        elif act == "set_pin":
            emp_id = request.form.get("emp_id", "")
            pin_val = request.form.get("pin_value", "").strip()
            if not re.match(r"^\d{4,8}$", pin_val):
                error = "PIN moet 4 tot 8 cijfers zijn."
            else:
                pin_hash = generate_password_hash(pin_val)
                conn.execute("UPDATE employees SET pin=? WHERE id=?", (pin_hash, emp_id))
                conn.commit()
                audit_log("set_pin", "employee", emp_id)
                success = "PIN opgeslagen."

        elif act == "clear_pin":
            emp_id = request.form.get("emp_id", "")
            conn.execute("UPDATE employees SET pin=NULL WHERE id=?", (emp_id,))
            conn.commit()
            audit_log("clear_pin", "employee", emp_id)
            success = "PIN verwijderd."

    pin_mode = get_pin_mode()
    employees_list = conn.execute("SELECT id, code, name, pin FROM employees WHERE active=1 ORDER BY name").fetchall()
    conn.close()

    alert_threshold = get_alert_threshold_minutes()
    return render_template("instellingen.html",
        pin_mode=pin_mode, employees_list=employees_list,
        alert_threshold=alert_threshold,
        error=error, success=success)


# ── Auth routes ──────────────────────────────────────────────────────────────

@app.route("/admin/login", methods=["GET", "POST"])
def admin_login():
    error = None
    if request.method == "POST":
        username = request.form.get("username", "").strip()
        pwd = request.form.get("password", "")

        # Try multi-user login first
        conn = get_db_connection()
        user = conn.execute("SELECT * FROM admin_users WHERE username=? AND active=1", (username,)).fetchone()
        conn.close()

        if user and check_password_hash(user["password_hash"], pwd):
            session["admin"] = True
            session["admin_user"] = user["username"]
            session["admin_display"] = user["display_name"]
            app.logger.info(f"Admin login: {username}")
            return redirect(request.args.get("next") or url_for("dashboard"))

        # Fallback: legacy single-password login (no username required)
        if not username or username == "admin":
            stored_hash = get_admin_password_hash()
            if stored_hash.startswith("pbkdf2:") or stored_hash.startswith("scrypt:"):
                pw_ok = check_password_hash(stored_hash, pwd)
            else:
                import hashlib
                pw_ok = hashlib.sha256(pwd.encode()).hexdigest() == stored_hash
            if pw_ok:
                session["admin"] = True
                session["admin_user"] = "admin"
                session["admin_display"] = "Beheerder"
                app.logger.info("Admin login via legacy password")
                return redirect(request.args.get("next") or url_for("dashboard"))

        error = "Ongeldige inloggegevens."
    return render_template("admin_login.html", error=error)

@app.route("/admin/logout")
def admin_logout():
    session.clear()
    return redirect(url_for("index"))


# ── Admin Users management ───────────────────────────────────────────────────

@app.route("/admin-users", methods=["GET", "POST"])
@admin_required
def admin_users():
    error = None
    success = None
    conn = get_db_connection()

    if request.method == "POST":
        act = request.form.get("_action", "")

        if act == "add":
            username = request.form.get("username", "").strip().lower()
            display_name = request.form.get("display_name", "").strip()
            password = request.form.get("password", "").strip()
            if not username or not display_name or not password:
                error = "Alle velden zijn verplicht."
            elif len(password) < 6:
                error = "Wachtwoord moet minimaal 6 tekens zijn."
            elif not re.match(r"^[a-z0-9_]{2,20}$", username):
                error = "Gebruikersnaam: 2-20 tekens, alleen kleine letters, cijfers en underscore."
            else:
                try:
                    conn.execute("INSERT INTO admin_users (username, password_hash, display_name) VALUES (?,?,?)",
                                 (username, generate_password_hash(password), display_name))
                    conn.commit()
                    audit_log("add_admin", "admin_user", username)
                    success = f"Admin '{display_name}' toegevoegd."
                except sqlite3.IntegrityError:
                    error = "Gebruikersnaam bestaat al."

        elif act == "deactivate":
            uid = request.form.get("id", "")
            user = conn.execute("SELECT username FROM admin_users WHERE id=?", (uid,)).fetchone()
            if user and user["username"] == get_current_admin():
                error = "Je kunt jezelf niet deactiveren."
            elif user:
                conn.execute("UPDATE admin_users SET active=0 WHERE id=?", (uid,))
                conn.commit()
                audit_log("deactivate_admin", "admin_user", uid)
                success = "Admin gedeactiveerd."

        elif act == "activate":
            uid = request.form.get("id", "")
            conn.execute("UPDATE admin_users SET active=1 WHERE id=?", (uid,))
            conn.commit()
            audit_log("activate_admin", "admin_user", uid)
            success = "Admin geactiveerd."

        elif act == "reset_password":
            uid = request.form.get("id", "")
            new_pw = request.form.get("new_password", "").strip()
            if len(new_pw) < 6:
                error = "Wachtwoord moet minimaal 6 tekens zijn."
            else:
                conn.execute("UPDATE admin_users SET password_hash=? WHERE id=?",
                             (generate_password_hash(new_pw), uid))
                conn.commit()
                audit_log("reset_admin_password", "admin_user", uid)
                success = "Wachtwoord gereset."

    users = conn.execute("SELECT * FROM admin_users ORDER BY active DESC, display_name").fetchall()
    conn.close()
    return render_template("admin_users.html", users=users, error=error, success=success)


# ── Kiosk ────────────────────────────────────────────────────────────────────

@app.route("/")
def index():
    return render_template("index.html")

@app.route("/clock", methods=["POST"])
def clock():
    ip = get_real_ip()
    if not check_rate_limit(ip):
        return jsonify({"status": "error", "message": "Te veel verzoeken. Wacht even."}), 429

    data = request.get_json(silent=True)
    if not data:
        return jsonify({"status": "error", "message": "Ongeldig verzoek."}), 400

    code = str(data.get("code", "")).strip()
    action = str(data.get("action", "")).strip().lower()
    pin = str(data.get("pin", "")).strip()
    reason = str(data.get("reason", "")).strip() or None

    if not code:
        return jsonify({"status": "error", "message": "Voer eerst een code in."}), 400
    if not re.match(r"^[a-zA-Z0-9]{1,20}$", code):
        return jsonify({"status": "error", "message": "Ongeldige code formaat."}), 400
    if action not in ["in", "uit", "pauze_in", "pauze_uit"]:
        return jsonify({"status": "error", "message": "Ongeldige actie."}), 400

    employee = get_employee_by_code(code)
    if not employee:
        return jsonify({"status": "error", "message": "Onbekende medewerkerscode."}), 400

    # PIN check if enabled
    if get_pin_mode():
        if not pin:
            return jsonify({"status": "pin_required", "message": "Voer je PIN in.", "name": employee["name"]}), 200
        if not employee["pin"]:
            return jsonify({"status": "error", "message": "Geen PIN ingesteld. Neem contact op met de beheerder."}), 400
        pin_hash = employee["pin"]
        if pin_hash.startswith("pbkdf2:") or pin_hash.startswith("scrypt:"):
            pin_ok = check_password_hash(pin_hash, pin)
        else:
            import hashlib
            pin_ok = hashlib.sha256(pin.encode()).hexdigest() == pin_hash
        if not pin_ok:
            return jsonify({"status": "error", "message": "Onjuiste PIN."}), 400

    last_entry = get_last_action(code)
    last_action_val = last_entry["action"] if last_entry else None

    transitions = {
        None:         ["in"],
        "in":         ["uit", "pauze_in"],
        "pauze_in":   ["pauze_uit"],
        "pauze_uit":  ["in", "uit", "pauze_in"],
        "uit":        ["in"],
    }
    allowed = transitions.get(last_action_val, [])
    if action not in allowed:
        labels = {
            "in": "inklokte", "uit": "uitklokte",
            "pauze_in": "pauze startte", "pauze_uit": "pauze beëindigde"
        }
        current_label = labels.get(last_action_val, "niets")
        return jsonify({"status": "error", "message": f"Kan niet '{action}' — {employee['name']} heeft zojuist {current_label}."}), 400

    # ── Te laat detectie ─────────────────────────────────────────────────────
    late_info = None
    if action == "in":
        now = datetime.now()
        day_idx = now.weekday()
        conn = get_db_connection()
        schedule = conn.execute(
            "SELECT start_time FROM schedules WHERE employee_code=? AND day_of_week=?",
            (code, day_idx)
        ).fetchone()
        conn.close()

        if schedule:
            try:
                scheduled_start = datetime.strptime(
                    now.strftime("%Y-%m-%d") + " " + schedule["start_time"], "%Y-%m-%d %H:%M"
                )
                minutes_late = (now - scheduled_start).total_seconds() / 60
                if minutes_late > 2:
                    late_info = {
                        "minutes": int(minutes_late),
                        "scheduled": schedule["start_time"],
                        "actual": now.strftime("%H:%M")
                    }
            except ValueError:
                pass

    if late_info and reason is None:
        return jsonify({
            "status": "reason_required",
            "name": employee["name"],
            "late_minutes": late_info["minutes"],
            "scheduled": late_info["scheduled"],
            "actual": late_info["actual"],
            "message": f"{employee['name']} is {late_info['minutes']} min te laat (gepland: {late_info['scheduled']})"
        }), 200

    timestamp = save_entry(code, action, reason)
    action_labels = {"in": "ingeklokt", "uit": "uitgeklokt", "pauze_in": "pauze gestart", "pauze_uit": "pauze beëindigd"}
    label = action_labels.get(action, action)

    msg = f"{employee['name']} {label} om {timestamp[-8:]}"
    if late_info:
        msg += f" ({late_info['minutes']} min te laat)"

    app.logger.info(f"Clock: {code} {action} at {timestamp}")

    return jsonify({
        "status": "success",
        "message": msg,
        "name": employee["name"],
        "action": action,
        "time": timestamp[-8:],
        "late": late_info is not None,
        "reason": reason
    })

@app.route("/status/<code>")
def get_status(code):
    if not re.match(r"^[a-zA-Z0-9]{1,20}$", code):
        return jsonify({"error": "Ongeldige code"}), 400
    employee = get_employee_by_code(code)
    if not employee:
        return jsonify({"error": "Onbekend"}), 404
    last = get_last_action(code)
    return jsonify({
        "name": employee["name"],
        "status": last["action"] if last else None,
        "since": last["timestamp"] if last else None
    })


# ── Kiosk: Weekoverzicht ────────────────────────────────────────────────────

@app.route("/api/my-hours/<code>")
def api_my_hours(code):
    if not re.match(r"^[a-zA-Z0-9]{1,20}$", code):
        return jsonify({"error": "Ongeldige code"}), 400
    employee = get_employee_by_code(code)
    if not employee:
        return jsonify({"error": "Onbekend"}), 404

    today = datetime.now()
    week_start = (today - timedelta(days=today.weekday())).strftime("%Y-%m-%d")
    week_end = today.strftime("%Y-%m-%d")

    conn = get_db_connection()
    rows = conn.execute(
        """SELECT action, timestamp FROM time_entries
           WHERE code=? AND DATE(timestamp) >= ? AND DATE(timestamp) <= ?
           ORDER BY datetime(timestamp) ASC""",
        (code, week_start, week_end)
    ).fetchall()
    conn.close()

    # Group by day
    day_entries = defaultdict(list)
    for row in rows:
        day = row["timestamp"][:10]
        day_entries[day].append(row)

    DAYS_NL = ["Ma", "Di", "Wo", "Do", "Vr", "Za", "Zo"]
    days = []
    total = 0
    for i in range(today.weekday() + 1):
        d = (today - timedelta(days=today.weekday() - i)).strftime("%Y-%m-%d")
        entries = day_entries.get(d, [])
        worked, _ = compute_worked_seconds(entries) if entries else (0, 0)
        total += worked
        days.append({
            "day": DAYS_NL[i],
            "date": d,
            "hours": format_duration(worked)
        })

    return jsonify({
        "name": employee["name"],
        "days": days,
        "total": format_duration(total)
    })


# ── Admin: Dashboard ─────────────────────────────────────────────────────────

@app.route("/dashboard")
@admin_required
def dashboard():
    today = datetime.now().strftime("%Y-%m-%d")
    week_start = (datetime.now() - timedelta(days=datetime.now().weekday())).strftime("%Y-%m-%d")
    conn = get_db_connection()

    total_employees = conn.execute("SELECT COUNT(*) FROM employees WHERE active=1").fetchone()[0]
    today_count = conn.execute(
        "SELECT COUNT(*) FROM time_entries WHERE DATE(timestamp)=?", (today,)
    ).fetchone()[0]

    employees_all = conn.execute("SELECT code, name FROM employees WHERE active=1 ORDER BY name").fetchall()
    clocked_in = []
    on_break = []
    for emp in employees_all:
        last = conn.execute(
            "SELECT action, timestamp FROM time_entries WHERE code=? ORDER BY datetime(timestamp) DESC, id DESC LIMIT 1",
            (emp["code"],)
        ).fetchone()
        if last and last["action"] == "in":
            clocked_in.append({"name": emp["name"], "since": last["timestamp"][11:16]})
        elif last and last["action"] == "pauze_in":
            on_break.append({"name": emp["name"], "since": last["timestamp"][11:16]})

    week_entries = conn.execute(
        """SELECT t.code, e.name, t.action, t.timestamp
           FROM time_entries t LEFT JOIN employees e ON t.code=e.code
           WHERE DATE(t.timestamp) >= ? ORDER BY t.code, datetime(t.timestamp)""",
        (week_start,)
    ).fetchall()
    conn.close()

    emp_entries = defaultdict(list)
    for row in week_entries:
        emp_entries[(row["code"], row["name"] or row["code"])].append(row)

    weekly_hours = []
    for (code, name), entries in emp_entries.items():
        secs, brk = compute_worked_seconds(entries)
        weekly_hours.append({"name": name, "label": format_duration(secs), "seconds": secs})
    weekly_hours.sort(key=lambda x: x["seconds"], reverse=True)

    return render_template("dashboard.html",
        total_employees=total_employees, clocked_in=clocked_in, on_break=on_break,
        today_count=today_count, weekly_hours=weekly_hours,
        now=datetime.now().strftime("%H:%M"), today=today)


# ── Admin: Logs (met paginering) ─────────────────────────────────────────────

LOGS_PER_PAGE = 50

@app.route("/logs")
@admin_required
def logs():
    search = request.args.get("q", "").strip()
    date_filter = request.args.get("date", "").strip()
    employee_filter = request.args.get("employee", "").strip()
    page = max(1, int(request.args.get("page", 1)))

    query = "SELECT t.id, t.code, e.name, t.action, t.timestamp, t.reason FROM time_entries t LEFT JOIN employees e ON t.code=e.code WHERE 1=1"
    count_query = "SELECT COUNT(*) FROM time_entries t LEFT JOIN employees e ON t.code=e.code WHERE 1=1"
    params = []
    if search:
        query += " AND (t.code LIKE ? OR e.name LIKE ?)"
        count_query += " AND (t.code LIKE ? OR e.name LIKE ?)"
        params += [f"%{search}%", f"%{search}%"]
    if date_filter:
        query += " AND DATE(t.timestamp)=?"
        count_query += " AND DATE(t.timestamp)=?"
        params.append(date_filter)
    if employee_filter:
        query += " AND t.code=?"
        count_query += " AND t.code=?"
        params.append(employee_filter)

    conn = get_db_connection()
    total_count = conn.execute(count_query, params).fetchone()[0]
    total_pages = max(1, (total_count + LOGS_PER_PAGE - 1) // LOGS_PER_PAGE)
    page = min(page, total_pages)

    query += " ORDER BY datetime(t.timestamp) DESC, t.id DESC LIMIT ? OFFSET ?"
    rows = conn.execute(query, params + [LOGS_PER_PAGE, (page - 1) * LOGS_PER_PAGE]).fetchall()
    employees_list = conn.execute("SELECT code, name FROM employees WHERE active=1 ORDER BY name").fetchall()
    conn.close()

    return render_template("logs.html", rows=rows, search=search,
        date_filter=date_filter, employee_filter=employee_filter,
        employees_list=employees_list,
        page=page, total_pages=total_pages, total_count=total_count)


# ── Admin: Employees ─────────────────────────────────────────────────────────

@app.route("/employees", methods=["GET", "POST"])
@admin_required
def employees():
    error_message = None
    success_message = None

    if request.method == "POST":
        act = request.form.get("_action", "add")
        if act == "add":
            code = str(request.form.get("code", "")).strip()
            name = str(request.form.get("name", "")).strip()
            if not code or not name:
                error_message = "Code en naam zijn verplicht."
            elif not re.match(r"^[a-zA-Z0-9]{1,20}$", code):
                error_message = "Code mag alleen letters en cijfers bevatten (max 20)."
            else:
                try:
                    conn = get_db_connection()
                    conn.execute("INSERT INTO employees (code, name, active) VALUES (?, ?, 1)", (code, name))
                    conn.commit()
                    conn.close()
                    audit_log("add_employee", "employee", code, None, name)
                    success_message = f"Medewerker '{name}' toegevoegd."
                except sqlite3.IntegrityError:
                    error_message = "Deze medewerkerscode bestaat al."
        elif act in ("deactivate", "activate"):
            emp_id = request.form.get("id")
            val = 0 if act == "deactivate" else 1
            conn = get_db_connection()
            conn.execute("UPDATE employees SET active=? WHERE id=?", (val, emp_id))
            conn.commit()
            conn.close()
            audit_log(act + "_employee", "employee", emp_id)
            success_message = "Medewerker bijgewerkt."

        elif act == "delete":
            emp_id = request.form.get("id")
            conn = get_db_connection()
            emp = conn.execute("SELECT code, name FROM employees WHERE id=?", (emp_id,)).fetchone()
            if emp:
                entry_count = conn.execute(
                    "SELECT COUNT(*) FROM time_entries WHERE code=?", (emp["code"],)
                ).fetchone()[0]
                if entry_count > 0:
                    error_message = f"Kan '{emp['name']}' niet verwijderen — er zijn nog {entry_count} registraties gekoppeld. Deactiveer de medewerker in plaats daarvan, of verwijder eerst de registraties."
                else:
                    conn.execute("DELETE FROM employees WHERE id=?", (emp_id,))
                    conn.commit()
                    audit_log("delete_employee", "employee", emp_id, emp["name"])
                    success_message = f"Medewerker '{emp['name']}' verwijderd."
            conn.close()

    conn = get_db_connection()
    rows = conn.execute("SELECT * FROM employees ORDER BY active DESC, name ASC").fetchall()
    conn.close()
    return render_template("employees.html", rows=rows, error_message=error_message, success_message=success_message)


# ── Admin: Edit / Delete / Add entries ───────────────────────────────────────

@app.route("/delete-entry/<int:entry_id>", methods=["POST"])
@admin_required
def delete_entry(entry_id):
    entry = get_entry_by_id(entry_id)
    if not entry:
        return jsonify({"status": "error", "message": "Niet gevonden."}), 404
    old_val = f"{entry['code']} {entry['action']} {entry['timestamp']}"
    conn = get_db_connection()
    conn.execute("DELETE FROM time_entries WHERE id=?", (entry_id,))
    conn.commit()
    conn.close()
    audit_log("delete_entry", "time_entry", entry_id, old_val)
    return jsonify({"status": "success"})

@app.route("/edit-entry/<int:entry_id>", methods=["GET", "POST"])
@admin_required
def edit_entry(entry_id):
    entry = get_entry_by_id(entry_id)
    if not entry:
        return "Niet gevonden", 404
    error_message = None
    if request.method == "POST":
        action = str(request.form.get("action", "")).strip().lower()
        timestamp = str(request.form.get("timestamp", "")).strip()
        if action not in ["in", "uit", "pauze_in", "pauze_uit"]:
            error_message = "Ongeldige actie."
        elif not timestamp:
            error_message = "Tijdstip is verplicht."
        else:
            try:
                datetime.strptime(timestamp, "%Y-%m-%d %H:%M:%S")
            except ValueError:
                error_message = "Gebruik formaat: JJJJ-MM-DD UU:MM:SS"
        if not error_message:
            is_valid, msg = validate_entry_sequence(entry["code"], entry_id, action, timestamp)
            if not is_valid:
                error_message = msg
        if not error_message:
            old_val = f"{entry['action']} {entry['timestamp']}"
            new_val = f"{action} {timestamp}"
            conn = get_db_connection()
            conn.execute("UPDATE time_entries SET action=?, timestamp=? WHERE id=?", (action, timestamp, entry_id))
            conn.commit()
            conn.close()
            audit_log("edit_entry", "time_entry", entry_id, old_val, new_val)
            return redirect(url_for("logs"))
        entry = {"id": entry["id"], "code": entry["code"], "action": action, "timestamp": timestamp}
    return render_template("edit_entry.html", entry=entry, error_message=error_message)


@app.route("/add-entry", methods=["GET", "POST"])
@admin_required
def add_entry():
    conn = get_db_connection()
    employees_list = conn.execute("SELECT code, name FROM employees WHERE active=1 ORDER BY name").fetchall()
    conn.close()

    error_message = None
    success_message = None
    form_data = {}

    if request.method == "POST":
        code = request.form.get("code", "").strip()
        action = request.form.get("action", "").strip().lower()
        timestamp = request.form.get("timestamp", "").strip()
        reason = request.form.get("reason", "").strip() or None
        form_data = {"code": code, "action": action, "timestamp": timestamp, "reason": reason}

        if not code:
            error_message = "Selecteer een medewerker."
        elif action not in ["in", "uit", "pauze_in", "pauze_uit"]:
            error_message = "Ongeldige actie."
        elif not timestamp:
            error_message = "Tijdstip is verplicht."
        else:
            try:
                datetime.strptime(timestamp, "%Y-%m-%d %H:%M:%S")
            except ValueError:
                error_message = "Gebruik formaat: JJJJ-MM-DD UU:MM:SS"

        if not error_message:
            # Temporarily add entry to validate sequence
            temp_conn = get_db_connection()
            cur = temp_conn.execute(
                "INSERT INTO time_entries (code, action, timestamp, reason) VALUES (?, ?, ?, ?)",
                (code, action, timestamp, reason)
            )
            new_id = cur.lastrowid
            temp_conn.commit()

            is_valid, msg = validate_entry_sequence(code)
            if not is_valid:
                temp_conn.execute("DELETE FROM time_entries WHERE id=?", (new_id,))
                temp_conn.commit()
                error_message = msg
            else:
                audit_log("add_entry_manual", "time_entry", new_id, None, f"{code} {action} {timestamp}")
                success_message = f"Registratie toegevoegd: {code} {action.upper()} om {timestamp}"
                form_data = {}
            temp_conn.close()

    return render_template("add_entry.html",
        employees_list=employees_list, error_message=error_message,
        success_message=success_message, form_data=form_data)


# ── Exports ──────────────────────────────────────────────────────────────────

@app.route("/export/excel")
@admin_required
def export_excel():
    try:
        import openpyxl
    except ImportError:
        return """
        <h2>Ontbrekende module: openpyxl</h2>
        <p>Voer dit uit in je terminal en herstart Flask:</p>
        <pre style="background:#f0f0f0;padding:12px;border-radius:6px;">
pip install openpyxl Pillow</pre>
        <p><a href="/rapport">← Terug</a></p>
        """, 500

    try:
        from excel_export import generate_excel
    except Exception as e:
        return f"<h2>Fout bij laden excel_export.py</h2><pre>{e}</pre><p>Controleer of excel_export.py naast app.py staat.</p>", 500

    date_from = request.args.get("from", (datetime.now() - timedelta(days=30)).strftime("%Y-%m-%d"))
    date_to   = request.args.get("to",   datetime.now().strftime("%Y-%m-%d"))

    conn = get_db_connection()
    rows = conn.execute(
        """SELECT t.code, COALESCE(e.name, t.code) as name, t.action, t.timestamp
           FROM time_entries t LEFT JOIN employees e ON t.code=e.code
           WHERE DATE(t.timestamp) >= ? AND DATE(t.timestamp) <= ?
           ORDER BY t.code, datetime(t.timestamp)""",
        (date_from, date_to)
    ).fetchall()
    emp_rows = conn.execute("SELECT code, name FROM employees WHERE active=1 ORDER BY name").fetchall()
    conn.close()

    employees_meta = {row["code"]: row["name"] for row in emp_rows}

    logo_path = os.path.join(app.root_path, "static", "vdv_logo.png")
    if not os.path.exists(logo_path):
        logo_path = None

    try:
        xlsx_data = generate_excel(rows, employees_meta, date_from, date_to, logo_path)
    except Exception as e:
        import traceback
        return f"<h2>Fout bij genereren Excel</h2><pre>{traceback.format_exc()}</pre>", 500

    filename = f"VDV_Tijdregistratie_{date_from}_tm_{date_to}.xlsx"
    return Response(
        xlsx_data.getvalue(),
        mimetype="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
        headers={"Content-Disposition": f"attachment; filename={filename}"}
    )


@app.route("/export/csv")
@admin_required
def export_csv():
    date_from = request.args.get("from", "")
    date_to = request.args.get("to", "")
    query = "SELECT t.id, t.code, e.name, t.action, t.timestamp, t.reason FROM time_entries t LEFT JOIN employees e ON t.code=e.code WHERE 1=1"
    params = []
    if date_from:
        query += " AND DATE(t.timestamp)>=?"; params.append(date_from)
    if date_to:
        query += " AND DATE(t.timestamp)<=?"; params.append(date_to)
    query += " ORDER BY datetime(t.timestamp) ASC, t.id ASC"
    conn = get_db_connection()
    rows = conn.execute(query, params).fetchall()
    conn.close()
    output = StringIO()
    writer = csv.writer(output)
    writer.writerow(["ID", "Code", "Naam", "Actie", "Tijdstip", "Reden"])
    for row in rows:
        writer.writerow([row["id"], row["code"], row["name"] or "", row["action"].upper(), row["timestamp"], row["reason"] or ""])
    return Response(output.getvalue(), mimetype="text/csv",
        headers={"Content-Disposition": "attachment; filename=registraties.csv"})

@app.route("/export/uren")
@admin_required
def export_uren():
    date_from = request.args.get("from", "")
    date_to = request.args.get("to", "")
    query = "SELECT t.code, e.name, t.action, t.timestamp FROM time_entries t LEFT JOIN employees e ON t.code=e.code WHERE 1=1"
    params = []
    if date_from:
        query += " AND DATE(t.timestamp)>=?"; params.append(date_from)
    if date_to:
        query += " AND DATE(t.timestamp)<=?"; params.append(date_to)
    query += " ORDER BY t.code, datetime(t.timestamp) ASC"
    conn = get_db_connection()
    rows = conn.execute(query, params).fetchall()
    conn.close()
    emp_day = defaultdict(list)
    for row in rows:
        key = (row["code"], row["name"] or row["code"], row["timestamp"][:10])
        emp_day[key].append(row)
    output = StringIO()
    writer = csv.writer(output)
    writer.writerow(["Code", "Naam", "Datum", "Gewerkte uren (netto)", "Pauze uren"])
    for (code, name, date), entries in sorted(emp_day.items()):
        secs, brk = compute_worked_seconds(entries)
        writer.writerow([code, name, date, f"{secs/3600:.2f}", f"{brk/3600:.2f}"])
    return Response(output.getvalue(), mimetype="text/csv",
        headers={"Content-Disposition": "attachment; filename=uren.csv"})


# ── Roosters ─────────────────────────────────────────────────────────────────

DAYS_NL = ["Maandag", "Dinsdag", "Woensdag", "Donderdag", "Vrijdag", "Zaterdag", "Zondag"]

@app.route("/roosters", methods=["GET", "POST"])
@admin_required
def roosters():
    conn = get_db_connection()
    error = None
    success = None

    if request.method == "POST":
        act = request.form.get("_action", "")

        if act == "save":
            emp_code = request.form.get("emp_code", "").strip()
            conn.execute("DELETE FROM schedules WHERE employee_code=?", (emp_code,))
            for day_idx in range(7):
                active = request.form.get(f"active_{day_idx}") == "1"
                start  = request.form.get(f"start_{day_idx}", "").strip()
                end    = request.form.get(f"end_{day_idx}", "").strip()
                if active and start and end:
                    conn.execute(
                        "INSERT INTO schedules (employee_code, day_of_week, start_time, end_time) VALUES (?,?,?,?)",
                        (emp_code, day_idx, start, end)
                    )
            conn.commit()
            audit_log("save_schedule", "schedule", emp_code)
            success = "Rooster opgeslagen."

        elif act == "delete":
            emp_code = request.form.get("emp_code", "").strip()
            conn.execute("DELETE FROM schedules WHERE employee_code=?", (emp_code,))
            conn.commit()
            audit_log("delete_schedule", "schedule", emp_code)
            success = "Rooster verwijderd."

        elif act == "copy":
            source_code = request.form.get("source_code", "").strip()
            target_code = request.form.get("target_code", "").strip()
            if source_code and target_code and source_code != target_code:
                source_schedule = conn.execute(
                    "SELECT day_of_week, start_time, end_time FROM schedules WHERE employee_code=?",
                    (source_code,)
                ).fetchall()
                if source_schedule:
                    conn.execute("DELETE FROM schedules WHERE employee_code=?", (target_code,))
                    for row in source_schedule:
                        conn.execute(
                            "INSERT INTO schedules (employee_code, day_of_week, start_time, end_time) VALUES (?,?,?,?)",
                            (target_code, row["day_of_week"], row["start_time"], row["end_time"])
                        )
                    conn.commit()
                    audit_log("copy_schedule", "schedule", target_code, f"from {source_code}")
                    success = "Rooster gekopieerd."
                else:
                    error = "Bronmedewerker heeft geen rooster."
            else:
                error = "Selecteer twee verschillende medewerkers."

    employees_list = conn.execute(
        "SELECT code, name FROM employees WHERE active=1 ORDER BY name"
    ).fetchall()

    schedule_rows = conn.execute(
        "SELECT employee_code, day_of_week, start_time, end_time FROM schedules ORDER BY employee_code, day_of_week"
    ).fetchall()
    conn.close()

    schedules = defaultdict(dict)
    for row in schedule_rows:
        schedules[row["employee_code"]][row["day_of_week"]] = {
            "start": row["start_time"],
            "end":   row["end_time"]
        }

    selected_code = request.args.get("emp", "") or (employees_list[0]["code"] if employees_list else "")

    return render_template("roosters.html",
        employees_list=employees_list,
        schedules=schedules,
        selected_code=selected_code,
        days=DAYS_NL,
        error=error, success=success
    )


# ── Notificaties API ────────────────────────────────────────────────────────

@app.route("/api/alerts")
@admin_required
def api_alerts():
    now = datetime.now()
    today = now.strftime("%Y-%m-%d")
    threshold_minutes = get_alert_threshold_minutes()
    conn = get_db_connection()

    employees_all = conn.execute(
        "SELECT code, name FROM employees WHERE active=1"
    ).fetchall()

    alerts = []
    for emp in employees_all:
        last = conn.execute(
            "SELECT action, timestamp FROM time_entries WHERE code=? ORDER BY datetime(timestamp) DESC, id DESC LIMIT 1",
            (emp["code"],)
        ).fetchone()

        day_idx = now.weekday()
        schedule = conn.execute(
            "SELECT start_time, end_time FROM schedules WHERE employee_code=? AND day_of_week=?",
            (emp["code"], day_idx)
        ).fetchone()

        alert_reasons = []

        if schedule:
            first_in_today = conn.execute(
                """SELECT timestamp, reason FROM time_entries
                   WHERE code=? AND action='in' AND DATE(timestamp)=?
                   ORDER BY datetime(timestamp) ASC LIMIT 1""",
                (emp["code"], today)
            ).fetchone()

            if first_in_today:
                try:
                    scheduled_start = datetime.strptime(today + " " + schedule["start_time"], "%Y-%m-%d %H:%M")
                    actual_in = parse_timestamp(first_in_today["timestamp"])
                    minutes_late = (actual_in - scheduled_start).total_seconds() / 60
                    if minutes_late > 2:
                        reason_label = f" — reden: {first_in_today['reason']}" if first_in_today["reason"] else " — geen reden opgegeven"
                        alert_reasons.append(f"Te laat ingeklokt: {int(minutes_late)} min (gepland {schedule['start_time']}, actual {actual_in.strftime('%H:%M')}){reason_label}")
                except (ValueError, TypeError):
                    pass
            elif last is None or last["action"] == "uit":
                try:
                    scheduled_start = datetime.strptime(today + " " + schedule["start_time"], "%Y-%m-%d %H:%M")
                    minutes_late = (now - scheduled_start).total_seconds() / 60
                    if minutes_late > 2:
                        alert_reasons.append(f"Nog niet ingeklokt (gepland: {schedule['start_time']}, {int(minutes_late)} min geleden)")
                except (ValueError, TypeError):
                    pass

        if last and last["action"] == "in":
            clocked_in_at = parse_timestamp(last["timestamp"])
            minutes_in = (now - clocked_in_at).total_seconds() / 60

            if minutes_in >= threshold_minutes:
                h = int(minutes_in // 60)
                m = int(minutes_in % 60)
                alert_reasons.append(f"Al {h}u{m:02d}m ingeklokt")

            if schedule:
                try:
                    scheduled_end = datetime.strptime(today + " " + schedule["end_time"], "%Y-%m-%d %H:%M")
                    if now > scheduled_end + timedelta(minutes=15):
                        alert_reasons.append(f"Rooster eindigde om {schedule['end_time']}")
                except (ValueError, TypeError):
                    pass

        if alert_reasons:
            alerts.append({
                "code": emp["code"],
                "name": emp["name"],
                "since": last["timestamp"][11:16] if last and last["action"] == "in" else None,
                "reasons": alert_reasons
            })

    conn.close()
    return jsonify({"alerts": alerts, "checked_at": now.strftime("%H:%M:%S")})


@app.route("/api/schedule-today/<code>")
def api_schedule_today(code):
    if not re.match(r"^[a-zA-Z0-9]{1,20}$", code):
        return jsonify({}), 400
    day_idx = datetime.now().weekday()
    conn = get_db_connection()
    row = conn.execute(
        "SELECT start_time, end_time FROM schedules WHERE employee_code=? AND day_of_week=?",
        (code, day_idx)
    ).fetchone()
    conn.close()
    if row:
        return jsonify({"start": row["start_time"], "end": row["end_time"]})
    return jsonify({})


# ── Settings: alert threshold ────────────────────────────────────────────────

@app.route("/instellingen/alerts", methods=["POST"])
@admin_required
def save_alert_settings():
    threshold = request.form.get("threshold_minutes", "600").strip()
    try:
        threshold = max(30, min(int(threshold), 1440))
    except ValueError:
        threshold = 600
    old_val = str(get_alert_threshold_minutes())
    set_setting("alert_threshold_minutes", threshold)
    audit_log("change_alert_threshold", "settings", "alert_threshold", old_val, str(threshold))
    return redirect(url_for("instellingen"))


# ── Database backup ──────────────────────────────────────────────────────────

@app.route("/backup/db")
@admin_required
def backup_db():
    backup_buf = BytesIO()
    conn = sqlite3.connect(DB_NAME)
    backup_conn = sqlite3.connect(":memory:")
    conn.backup(backup_conn)
    # Write memory DB to bytes
    for line in backup_conn.iterdump():
        backup_buf.write(f"{line}\n".encode("utf-8"))
    conn.close()
    backup_conn.close()
    backup_buf.seek(0)

    # Actually just send the file directly
    timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
    audit_log("backup_database", "system", "timeclock.db")
    return send_file(
        DB_NAME,
        as_attachment=True,
        download_name=f"timeclock_backup_{timestamp}.db",
        mimetype="application/x-sqlite3"
    )


# ── Audit log viewer ────────────────────────────────────────────────────────

@app.route("/audit-log")
@admin_required
def view_audit_log():
    page = max(1, int(request.args.get("page", 1)))
    conn = get_db_connection()
    total = conn.execute("SELECT COUNT(*) FROM audit_log").fetchone()[0]
    total_pages = max(1, (total + 50 - 1) // 50)
    page = min(page, total_pages)
    rows = conn.execute(
        "SELECT * FROM audit_log ORDER BY timestamp DESC LIMIT 50 OFFSET ?",
        ((page - 1) * 50,)
    ).fetchall()
    conn.close()
    return render_template("audit_log.html", rows=rows, page=page, total_pages=total_pages)


# ── Health check ─────────────────────────────────────────────────────────────

@app.route("/health")
def health():
    try:
        conn = get_db_connection()
        conn.execute("SELECT 1").fetchone()
        emp_count = conn.execute("SELECT COUNT(*) FROM employees WHERE active=1").fetchone()[0]
        conn.close()
        return jsonify({
            "status": "ok",
            "database": "connected",
            "active_employees": emp_count,
            "timestamp": datetime.now().isoformat()
        })
    except Exception as e:
        return jsonify({"status": "error", "message": str(e)}), 500


# ── PWA manifest ─────────────────────────────────────────────────────────────

@app.route("/manifest.json")
def manifest():
    return jsonify({
        "name": "Tijdregistratie",
        "short_name": "Inklokken",
        "description": "In- en uitkloksysteem",
        "start_url": "/",
        "display": "standalone",
        "background_color": "#1a1916",
        "theme_color": "#1a1916",
        "orientation": "portrait-primary",
        "icons": [
            {"src": "/static/icon-192.svg", "sizes": "192x192", "type": "image/svg+xml"},
            {"src": "/static/icon-512.svg", "sizes": "512x512", "type": "image/svg+xml"}
        ]
    })

@app.route("/sw.js")
def service_worker():
    sw_content = """
const CACHE = 'timeclock-v2';
const OFFLINE_ASSETS = ['/', '/static/style.css'];

self.addEventListener('install', e => {
  e.waitUntil(
    caches.open(CACHE).then(c => c.addAll(OFFLINE_ASSETS)).then(() => self.skipWaiting())
  );
});

self.addEventListener('activate', e => {
  e.waitUntil(
    caches.keys().then(keys =>
      Promise.all(keys.filter(k => k !== CACHE).map(k => caches.delete(k)))
    ).then(() => self.clients.claim())
  );
});

self.addEventListener('fetch', e => {
  if (e.request.method !== 'GET') return;
  if (e.request.url.includes('/clock') || e.request.url.includes('/status') || e.request.url.includes('/api/')) return;
  e.respondWith(
    fetch(e.request)
      .then(res => {
        const clone = res.clone();
        caches.open(CACHE).then(c => c.put(e.request, clone));
        return res;
      })
      .catch(() => caches.match(e.request))
  );
});
"""
    return Response(sw_content, mimetype="application/javascript",
        headers={"Service-Worker-Allowed": "/"})


if __name__ == "__main__":
    app.run(host="0.0.0.0", port=5000, debug=True)
