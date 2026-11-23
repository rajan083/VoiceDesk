"""notify.py - owner alerts and customer confirmations (outbox, Telegram, email, WhatsApp). Recipients come from the server, never the model."""

import os, re, json, logging
import tenant_config as tc
import urllib.request, urllib.parse, smtplib, time
from email.message import EmailMessage
import datetime as dt

log = logging.getLogger("voicedesk.notify")

def _clean(s, n=200):                      # model-written or caller-written text: one line, capped
    return re.sub(r"\s+", " ", str(s or "")).strip()[:n]

def _post(url, data, headers=None):
    req = urllib.request.Request(url, data=data, headers=headers or {})
    with urllib.request.urlopen(req, timeout=10) as r:
        return r.status

def send(channel, to, body):
    """channel: outbox | telegram | email | whatsapp. Returns {'ok': bool} and never raises."""
    try:
        if channel == "outbox":
            with open("outbox.jsonl", "a", encoding="utf-8") as f:
                f.write(json.dumps({"ts": time.strftime("%Y-%m-%d %H:%M:%S"), "to": to, "body": body},ensure_ascii=False) + "\n")
        elif channel == "telegram":
            _post(f"https://api.telegram.org/bot{os.environ['TELEGRAM_BOT_TOKEN']}/sendMessage",
                  urllib.parse.urlencode({"chat_id": to, "text": body}).encode())
        else:
            return {"ok": False, "error": "unknown_channel"}
        return {"ok": True}
    
    except Exception as e:
        log.exception("send failed channel=%s", channel)
        return {"ok": False, "error": type(e).__name__}

def _who(caller_phone, contact_phone):
    if caller_phone:
        return f"{caller_phone} (caller ID)"
    if contact_phone:
        return f"{_clean(contact_phone, 20)} (given by caller, not verified)"
    return "not available"


def _when(start):
    try:
        d = dt.datetime.fromisoformat(start)
        return d.strftime("%a %d %b %Y, ") + d.strftime("%I:%M %p").lstrip("0")
    except ValueError:
        return _clean(start, 16)
    
def booking_alerts(tenant_id, out, start, caller_name, service, caller_phone, address="", contact_phone=""):
    cfg = tc.load_tenant(tenant_id)
    n = cfg.get("notify") or {}
    biz = cfg["business_name"]
    if cfg.get("require_phone", True) and not (caller_phone or contact_phone):
        return {"owner": {"ok": False, "error": "phone_required"}}      # second lock: never send without a number
    code, when = out.get("code"), _when(start)
    res = {}
    who = _who(caller_phone, contact_phone)
    if caller_phone and n.get("confirm_customer"):
        office = (cfg["phone_numbers"] or [""])[0]
        tail = f" To change or cancel it, call {office}." if office else ""
        res["customer"] = send(n.get("customer_channel", "outbox"), caller_phone,
            f"{biz}: your appointment is confirmed for {when}. Confirmation code: {code}.{tail}")
    if n.get("owner_to"):
        res["owner"] = send(n.get("owner_channel", "outbox"), n["owner_to"],
            f"New booking at {biz}\n"
            f"Name: {_clean(caller_name, 60)}\n"
            f"When: {when}\n"
            f"Service: {_clean(service, 60) or 'not given'}\n"
            f"Address: {_clean(address, 150) or 'not given'}\n"
            f"Phone: {who}\n"
            f"Code: {code}")
    return res

def note_alert(tenant_id, note, caller_phone, contact_phone=""):
    n = tc.load_tenant(tenant_id).get("notify") or {}
    if not n.get("owner_to"):
        return {}
    who = _who(caller_phone, contact_phone)
    return {"owner": send(n.get("owner_channel", "outbox"), n["owner_to"],
            f"Message from caller {who}: {_clean(note, 300)}")}
    
def lead_alert(tenant_id, caller_name, need, address, callback_time, caller_phone, contact_phone=""):
    n = tc.load_tenant(tenant_id).get("notify") or {}
    if not n.get("owner_to"):
        return {}
    return {"owner": send(n.get("owner_channel", "outbox"), n["owner_to"],
            f"New lead\n"
            f"Name: {_clean(caller_name, 60)}\n"
            f"Need: {_clean(need, 150)}\n"
            f"Address: {_clean(address, 150) or 'not given'}\n"
            f"Call back: {_clean(callback_time, 60) or 'any time'}\n"
            f"Phone: {_who(caller_phone, contact_phone)}")}
    
def transfer_alert(tenant_id, reason, caller_phone, contact_phone=""):
    n = tc.load_tenant(tenant_id).get("notify") or {}
    if not n.get("owner_to"):
        return {}
    return {"owner": send(n.get("owner_channel", "outbox"), n["owner_to"],
            f"Caller asked to speak to you now\n"
            f"Reason: {_clean(reason, 150) or 'not given'}\n"
            f"Phone: {_who(caller_phone, contact_phone)}")}