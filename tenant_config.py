"""tenant_config.py - per-tenant settings and the dialed-number -> tenant lookup.

One folder per tenant, named by a stable slug ID (never the business name):
    tenants/<id>/config.json     settings and assigned phone numbers
    kb/<id>/raw/                 transcribed pages (read by rag_pipeline)

The tenant is always resolved by the server from the dialed number. It is never taken
from the caller's speech or from the model, and an unknown number never falls back to a
default tenant.

Tenant IDs are generated (t_ plus 12 random hex characters). The ID is a label, not a secret:
isolation comes from the server resolving the tenant itself, never from the ID being hard to guess.
Pass --id only for a fixed name such as the "demo" tenant that already has data.

CLI (run from the VoiceDesk folder; the square brackets mean optional, do not type them):
    python tenant_config.py new "<Business name>" [--id demo] [--number +14155550100]
    python tenant_config.py list
    python tenant_config.py check
"""
import argparse, copy, functools, json, os, re, uuid, secrets, time

BASE_DIR = os.path.dirname(os.path.abspath(__file__))
TENANTS_DIR = os.path.join(BASE_DIR, "tenants")
KB_DIR = os.path.join(BASE_DIR, "kb")

ID_RE = re.compile(r"^[a-z0-9][a-z0-9_-]{1,39}$")      # the ID becomes a folder name: no ../ tricks
E164_RE = re.compile(r"^\+[1-9]\d{6,14}$")

DEFAULTS = {
    "schema_version": 1,
    "language": "en",
    "greeting": "Thank you for calling {business_name}. How can I help?",
    "phone_numbers": [],
    "owner": {"notify_telegram_chat_id": None, "transfer_number": None},   # read server-side only
    "retrieval": {"top_k": 5, "min_score": 0.20},       # tune per tenant against its own eval set
}


def _merge(base, over):
    out = copy.deepcopy(base)
    for k, v in over.items():
        out[k] = _merge(out[k], v) if isinstance(v, dict) and isinstance(out.get(k), dict) else v
    return out


def new_tenant_id():
    for _ in range(10):
        t = "t_" + uuid.uuid4().hex[:12]
        if not os.path.exists(os.path.join(TENANTS_DIR, t)):
            return t
    raise RuntimeError("could not generate an unused tenant id")


def check_id(tenant_id):
    if not isinstance(tenant_id, str) or not ID_RE.match(tenant_id):
        raise ValueError("tenant id must be 2-40 characters: lowercase letters, digits, - or _")
    return tenant_id


def normalize_number(raw):
    n = re.sub(r"[\s().-]", "", str(raw))
    if not E164_RE.match(n):
        raise ValueError(f"phone number must be in +E.164 form, got {raw!r}")
    return n


def _config_path(tenant_id):
    return os.path.join(TENANTS_DIR, check_id(tenant_id), "config.json")


def load_tenant(tenant_id):
    path = _config_path(tenant_id)
    if not os.path.exists(path):
        raise FileNotFoundError(f"unknown tenant {tenant_id!r}")
    with open(path, encoding="utf-8") as f:
        cfg = _merge(DEFAULTS, json.load(f))
    if cfg.get("tenant_id") != tenant_id:
        raise ValueError(f"{path}: tenant_id inside the file must equal the folder name")
    if not str(cfg.get("business_name", "")).strip():
        raise ValueError(f"{path}: business_name is required")
    cfg["phone_numbers"] = [normalize_number(n) for n in cfg["phone_numbers"]]
    t = (cfg.get("owner") or {}).get("transfer_number")
    if t:
        cfg["owner"]["transfer_number"] = normalize_number(t)
    r = cfg["retrieval"]
    if not (1 <= int(r["top_k"]) <= 10 and 0.0 <= float(r["min_score"]) <= 1.0):
        raise ValueError(f"{path}: retrieval.top_k must be 1-10 and min_score 0-1")
    return cfg


def all_tenant_ids():
    if not os.path.isdir(TENANTS_DIR):
        return []
    return sorted(d for d in os.listdir(TENANTS_DIR)
                  if ID_RE.match(d) and os.path.exists(os.path.join(TENANTS_DIR, d, "config.json")))


@functools.lru_cache(maxsize=1)
def _number_index():
    index = {}
    for t in all_tenant_ids():
        for n in load_tenant(t)["phone_numbers"]:
            if n in index:          # fail closed: never guess which tenant owns a number
                raise ValueError(f"{n} is assigned to both {index[n]!r} and {t!r}")
            index[n] = t
    return index


def reload():
    """Call after any config change (the portal will call this)."""
    _number_index.cache_clear()


def tenant_for_number(dialed):
    """Dialed number -> tenant id, or None if the number is not in service."""
    try:
        n = normalize_number(dialed)
    except ValueError:
        return None
    return _number_index().get(n)

def resolve_tenant(value):
    """tenant id, dialed number or business name -> tenant id, or None."""
    value = (value or "").strip()
    if not value:
        return None
    ids = all_tenant_ids()
    for t in ids:                                   # id, ignoring case
        if t.lower() == value.lower():
            return t
    t = tenant_for_number(value)                    # dialed number
    if t:
        return t
    names = [t for t in ids                         # business name, ignoring case
             if load_tenant(t)["business_name"].strip().lower() == value.lower()]
    return names[0] if len(names) == 1 else None

def connect_owner(tenant_id, minutes=10):
    """Print a one-time Telegram link and wait for the owner to tap it. Saves their chat id in the config."""
    import urllib.request, urllib.parse
    from dotenv import load_dotenv
    load_dotenv()
    token = os.environ.get("TELEGRAM_BOT_TOKEN")
    if not token:
        print("TELEGRAM_BOT_TOKEN is not set, skipping Telegram.")
        return False

    def call(method, **p):
        req = urllib.request.Request(f"https://api.telegram.org/bot{token}/{method}",
                                     data=urllib.parse.urlencode(p).encode())
        with urllib.request.urlopen(req, timeout=40) as r:
            return json.load(r)["result"]

    cfg, code = load_tenant(tenant_id), secrets.token_urlsafe(16)
    print(f"Send this link privately to the owner of {cfg['business_name']}:")
    print(f"https://t.me/{call('getMe')['username']}?start={code}")
    print(f"Waiting up to {minutes} minutes for the owner to tap it (Ctrl+C to skip)...")
    deadline, offset = time.time() + minutes * 60, None
    while time.time() < deadline:
        try:
            updates = call("getUpdates", timeout=25, **({"offset": offset} if offset else {}))
        except Exception:
            time.sleep(3)
            continue
        for u in updates:
            offset = u["update_id"] + 1
            m = u.get("message") or {}
            chat = m.get("chat") or {}
            if chat.get("type") == "private" and (m.get("text") or "").strip() == f"/start {code}":
                path = _config_path(tenant_id)
                with open(path, encoding="utf-8") as f:
                    raw = json.load(f)
                n = raw.setdefault("notify", {})
                n.update(owner_channel="telegram", owner_to=str(chat["id"]))
                n.setdefault("customer_channel", "outbox")
                n.setdefault("confirm_customer", True)
                with open(path + ".tmp", "w", encoding="utf-8") as f:
                    json.dump(raw, f, indent=2, ensure_ascii=False)
                os.replace(path + ".tmp", path)
                reload()
                call("sendMessage", chat_id=chat["id"],
                     text=f"Connected. Bookings and messages for {cfg['business_name']} will arrive here.")
                print("Owner connected, chat id saved.")
                return True
    print(f"Timed out. Run: python tenant_config.py link {tenant_id}")
    return False

def create_tenant(business_name, phone_numbers=(), tenant_id=None, **overrides):
    """Create a tenant and its folders. The ID is generated unless one is passed. Returns the config."""
    if not str(business_name).strip():
        raise ValueError("business_name is required")
    tenant_id = check_id(tenant_id) if tenant_id else new_tenant_id()
    path = _config_path(tenant_id)
    if os.path.exists(path):
        raise FileExistsError(f"tenant {tenant_id!r} already exists")
    numbers = [normalize_number(n) for n in phone_numbers]
    for n in numbers:
        owner = _number_index().get(n)
        if owner:
            raise ValueError(f"{n} already belongs to tenant {owner!r}")
    cfg = _merge(DEFAULTS, overrides)
    cfg.update(tenant_id=tenant_id, business_name=business_name.strip(), phone_numbers=numbers)
    os.makedirs(os.path.dirname(path), exist_ok=True)
    os.makedirs(os.path.join(KB_DIR, tenant_id, "raw"), exist_ok=True)
    tmp = path + ".tmp"
    with open(tmp, "w", encoding="utf-8") as f:
        json.dump(cfg, f, indent=2, ensure_ascii=False)
    os.replace(tmp, path)                          # atomic write
    reload()
    return cfg


if __name__ == "__main__":
    ap = argparse.ArgumentParser(description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    sub = ap.add_subparsers(dest="cmd", required=True)
    p = sub.add_parser("new")
    p.add_argument("business_name")
    p.add_argument("--id", dest="tenant_id", default=None)
    p.add_argument("--number", action="append", default=[])
    p.add_argument("--no-telegram", action="store_true") 
    p = sub.add_parser("link"); p.add_argument("tenant") 
    sub.add_parser("list")
    sub.add_parser("check")
    a = ap.parse_args()
    if a.cmd == "new":
        cfg = create_tenant(a.business_name, a.number, tenant_id=a.tenant_id)
        t = cfg["tenant_id"]
        print(f"created tenant {t} ({cfg['business_name']})")
        print("  config:", _config_path(t))
        print("  pages :", os.path.join(KB_DIR, t, "raw"))
        if not a.no_telegram:
            connect_owner(t)
    elif a.cmd == "link":
        connect_owner(resolve_tenant(a.tenant) or exit(f"no tenant matches {a.tenant!r}"))
    elif a.cmd == "list":
        for t in all_tenant_ids():
            c = load_tenant(t)
            print(f"{t:<20} {c['business_name']:<30} {', '.join(c['phone_numbers']) or '(no number)'}")
    else:
        ids = all_tenant_ids()
        for t in ids:
            load_tenant(t)
        _number_index()
        print(f"ok: {len(ids)} tenants, no duplicate numbers")