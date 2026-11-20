"""notify.py - texts and WhatsApp messages through Twilio. Recipients come from the server, never the model."""
import os, re, json, logging
import tenant_config as tc
import urllib.request, urllib.parse, smtplib, time
from email.message import EmailMessage

log = logging.getLogger("voicedesk.notify")
_client = None

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
            
        elif channel == "email":
            m = EmailMessage()
            m["From"], m["To"], m["Subject"] = os.environ["SMTP_USER"], to, "Message from VoiceDesk"
            m.set_content(body)
            with smtplib.SMTP_SSL("smtp.gmail.com", 465, timeout=10) as s:
                s.login(os.environ["SMTP_USER"], os.environ["SMTP_APP_PASSWORD"])
                s.send_message(m)
                
        elif channel == "whatsapp":      # Meta Cloud API; free-form text only inside the 24h window
            _post(f"https://graph.facebook.com/{os.environ.get('WA_GRAPH_VERSION', 'v26.0')}"
                  f"/{os.environ['WA_PHONE_NUMBER_ID']}/messages",
                  json.dumps({"messaging_product": "whatsapp", "to": to, "type": "text",
                              "text": {"body": body}}).encode(),
                  {"Authorization": "Bearer " + os.environ["WA_TOKEN"], "Content-Type": "application/json"})
            
        else:
            return {"ok": False, "error": "unknown_channel"}
        return {"ok": True}
    
    except Exception as e:
        log.exception("send failed channel=%s", channel)
        return {"ok": False, "error": type(e).__name__}

def booking_alerts(tenant_id, out, start, caller_name, service, caller_phone, address="", contact_phone=""):
    cfg = tc.load_tenant(tenant_id)
    n = cfg.get("notify") or {}
    biz = cfg["business_name"]
    code, when = out.get("code"), _clean(start, 16).replace("T", " ")   # CHECK: your book() key
    res = {}
    if caller_phone:
        who = f"{caller_phone} (caller ID)"
    elif contact_phone:
        who = f"{_clean(contact_phone, 20)} (given by caller, not verified)"
    else:
        who = "not available"
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

def note_alert(tenant_id, note, caller_phone):
    n = tc.load_tenant(tenant_id).get("notify") or {}
    if not n.get("owner_to"):
        return {}
    return {"owner": send(n.get("owner_channel", "outbox"), n["owner_to"],
            f"Message from caller {caller_phone or 'unknown'}: {_clean(note, 300)}")}