"""What the business owner sees.
  python report.py <tenant>               bookings, notes and recent calls (Markdown)
  python report.py <tenant> --call <id>   one call's full transcript
Redirect to a file if you want one:  python report.py <tenant> > report.md
"""
import pathlib, sys
from datetime import datetime
from zoneinfo import ZoneInfo

import bookings
import tenant_config as tc
from calllog import read_call


def _local(utc_text, tz):
    """Accepts '2026-11-24T04:30Z' (bookings) or '2026-11-24 04:30:00' (created_at)."""
    fmt = "%Y-%m-%dT%H:%MZ" if "T" in utc_text else "%Y-%m-%d %H:%M:%S"
    d = datetime.strptime(utc_text, fmt).replace(tzinfo=ZoneInfo("UTC"))
    return d.astimezone(tz).strftime("%a %d %b, %I:%M %p")


def transcript(tenant_id, call_id):
    path = pathlib.Path("calls") / tenant_id / f"{call_id}.jsonl"
    if not path.exists():
        return f"no such call: {call_id}"
    out = []
    for r in read_call(path):
        if r["role"] == "caller":
            out.append(f"Caller: {r['text']}")
        elif r["role"] == "agent":
            out.append(f"Agent:  {r['text']}")
        elif r["role"] == "tool":
            out.append(f"  [{r['text']}] -> {r.get('result')}")
    return "\n".join(out)


def report(tenant_id):
    cfg = tc.load_tenant(tenant_id)
    tz = ZoneInfo(cfg["scheduling"]["timezone"])
    lines = [f"# {cfg['business_name']}", "", "## Upcoming bookings"]
    rows = bookings.upcoming_bookings(tenant_id)
    lines += [f"- {_local(r[0], tz)}: {r[1]}" + (f" ({r[2]})" if r[2] else "")
              + (f", {r[3]}" if r[3] else "") + (f". Note: {r[4]}" if r[4] else "") + f" [code {r[5]}]"
              for r in rows] or ["- none"]
    lines += ["", "## Notes"]
    lines += [f"- {_local(r[0], tz)} (call {r[1]}): {r[2]}"
              for r in bookings.recent_notes(tenant_id)] or ["- none"]
    lines += ["", "## Recent calls"]
    files = sorted(pathlib.Path("calls", tenant_id).glob("*.jsonl"),
                   key=lambda p: p.stat().st_mtime, reverse=True)[:20]
    for f in files:
        when = datetime.fromtimestamp(f.stat().st_mtime, tz).strftime("%a %d %b, %I:%M %p")
        summ = f.with_suffix(".summary.txt")
        lines.append(f"### {when} (call {f.stem})")
        lines.append(summ.read_text(encoding="utf-8").strip() if summ.exists() else "(no summary yet)")
        lines.append("")
    return "\n".join(lines) if files else "\n".join(lines + ["- none"])



if __name__ == "__main__":
    if len(sys.argv) < 2:
        sys.exit(__doc__)
    TENANT_ID = tc.resolve_tenant(sys.argv[1])
    if not TENANT_ID:
        sys.exit(f"no tenant matches {sys.argv[1]!r}")
    if len(sys.argv) == 2:
        print(report(TENANT_ID))
    elif len(sys.argv) == 4 and sys.argv[2] == "--call":
        print(transcript(TENANT_ID, sys.argv[3]))
    else:
        sys.exit(__doc__)