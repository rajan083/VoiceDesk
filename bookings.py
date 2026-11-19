import sqlite3, secrets
from datetime import datetime, timedelta, date, time
from zoneinfo import ZoneInfo

DB = "voicedesk.db"
SCHEMA = """
CREATE TABLE IF NOT EXISTS bookings(
  id INTEGER PRIMARY KEY,
  tenant_id TEXT NOT NULL, start_utc TEXT NOT NULL,
  caller_name TEXT NOT NULL, caller_phone TEXT, service TEXT, notes TEXT,
  code TEXT NOT NULL, status TEXT NOT NULL DEFAULT 'booked',
  created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP);
CREATE UNIQUE INDEX IF NOT EXISTS one_booking_per_slot
  ON bookings(tenant_id, start_utc) WHERE status = 'booked';
CREATE TABLE IF NOT EXISTS notes(
  id INTEGER PRIMARY KEY,
  tenant_id TEXT NOT NULL, call_id TEXT NOT NULL, note TEXT NOT NULL,
  created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP);
CREATE INDEX IF NOT EXISTS notes_by_tenant ON notes(tenant_id, created_at);
"""

def db():
    c = sqlite3.connect(DB, timeout=5)
    c.executescript(SCHEMA)
    return c

def _slots(cfg, day):
    """All slot starts (aware datetimes) the business offers on `day`."""
    tz = ZoneInfo(cfg["timezone"])
    window = cfg["hours"].get(day.strftime("%a").lower())      # e.g. ["09:00","17:00"] or None
    if not window:
        return []
    start = datetime.combine(day, time.fromisoformat(window[0]), tz)
    end = datetime.combine(day, time.fromisoformat(window[1]), tz)
    step, out = timedelta(minutes=cfg["slot_minutes"]), []
    while start + step <= end:
        out.append(start); start += step
    return out

def _utc(dt):
    return dt.astimezone(ZoneInfo("UTC")).strftime("%Y-%m-%dT%H:%MZ")

def free_slots(tenant_id, cfg, day, now=None):
    tz = ZoneInfo(cfg["timezone"])
    now = now or datetime.now(tz)
    earliest = now + timedelta(minutes=cfg.get("min_notice_minutes", 60))
    with db() as c:
        taken = {r[0] for r in c.execute(
            "SELECT start_utc FROM bookings WHERE tenant_id=? AND status='booked'", (tenant_id,))}
    return [s for s in _slots(cfg, day) if s >= earliest and _utc(s) not in taken]

def book(tenant_id, cfg, slot_local_iso, caller_name, phone=None, service=None, notes=None, now=None):
    tz = ZoneInfo(cfg["timezone"])
    try:
        want = datetime.fromisoformat(slot_local_iso).replace(tzinfo=tz)
    except ValueError:
        return {"ok": False, "error": "bad_time_format"}
    # never trust the model's slot: it must be one we would have offered
    if want not in free_slots(tenant_id, cfg, want.date(), now):
        return {"ok": False, "error": "slot_not_available"}
    code = secrets.token_hex(3).upper()
    try:
        with db() as c:
            c.execute("INSERT INTO bookings(tenant_id,start_utc,caller_name,caller_phone,service,notes,code)"
                      " VALUES (?,?,?,?,?,?,?)",
                      (tenant_id, _utc(want), caller_name[:80], (phone or "")[:20],
                       (service or "")[:80], (notes or "")[:500], code))
    except sqlite3.IntegrityError:
        return {"ok": False, "error": "slot_not_available"}   # lost a race
    return {"ok": True, "code": code, "when": want.strftime("%a %d %b, %I:%M %p")}


# ---------- notes ----------
MAX_NOTES_PER_CALL = 10

def add_note(tenant_id, call_id, note):
    # caller-influenced text: one line, printable characters only, capped
    note = "".join(ch for ch in " ".join((note or "").split()) if ch.isprintable())[:500]
    if not note:
        return {"ok": False, "error": "empty_note"}
    with db() as c:
        n = c.execute("SELECT COUNT(*) FROM notes WHERE tenant_id=? AND call_id=?",
                      (tenant_id, call_id)).fetchone()[0]
        if n >= MAX_NOTES_PER_CALL:
            return {"ok": False, "error": "note_limit_reached"}
        c.execute("INSERT INTO notes(tenant_id, call_id, note) VALUES (?,?,?)",
                  (tenant_id, call_id, note))
    return {"ok": True}


# ---------- read side: used by report.py, never exposed to the model ----------
def recent_notes(tenant_id, limit=50):
    with db() as c:
        return c.execute("SELECT created_at, call_id, note FROM notes WHERE tenant_id=?"
                         " ORDER BY id DESC LIMIT ?", (tenant_id, limit)).fetchall()

def upcoming_bookings(tenant_id, now=None):
    now_utc = (now or datetime.now(ZoneInfo("UTC"))).astimezone(ZoneInfo("UTC")).strftime("%Y-%m-%dT%H:%MZ")
    with db() as c:
        return c.execute("SELECT start_utc, caller_name, caller_phone, service, notes, code FROM bookings"
                         " WHERE tenant_id=? AND status='booked' AND start_utc>=? ORDER BY start_utc",
                         (tenant_id, now_utc)).fetchall()