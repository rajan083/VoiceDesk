import os, io, re, json, time, hashlib, logging
import yaml
from google.genai import types
from pypdf import PdfReader, PdfWriter

BASE_DIR = os.path.dirname(os.path.abspath(__file__))
SOURCE_DIR = os.path.join(BASE_DIR, "manual_sources")     # manual_sources/<tenant_id>/*.pdf
KB_DIR = os.path.join(BASE_DIR, "kb")
CACHE_DIR = os.path.join(BASE_DIR, "cache")
VISION_MODEL = "gemini-3.1-flash-lite"    # verify the name
PAGES_PER_CALL = 4
PROMPT_VERSION = "v1"

TRANSCRIBE_PROMPT = """You are preparing a product or service manual for a search index.
The attached PDF contains {count} consecutive pages of the document "{title}".

For EACH page output exactly this format, numbering pages 1 to {count} in the order
they appear in THIS attached file:

## Page <number>
<content>

Rules:
- This is an excerpt. Do not comment on missing pages; transcribe exactly what is attached.
- Keep the original language and script. Never translate.
- Transcribe faithfully: headings, notes, warnings, specifications and step-by-step instructions.
  Keep numbers, units and model names exactly as printed.
- Write every numbered step as "Step N: <text>" and end every step, note and warning with a
  full stop ("." or "।").
- No markdown tables. Write each table row as one line: "<Column>: <value>; <Column>: <value>."
- For each diagram or figure, write a line starting "Figure:" saying what it shows, with every
  label exactly as printed.
- Do not add commentary, summaries or anything that is not on the page."""

PAGE_HEADER = re.compile(r"^##\s*Page\s+(\d+)\s*$", re.MULTILINE)
_REFUSAL_MARKERS = ("please provide", "only contains pages", "i don't have access",
                    "cannot see", "no pages were", "the pdf provided")

def _looks_like_refusal(markdown, expected_pages):
    if not markdown.strip() or len(PAGE_HEADER.findall(markdown)) < expected_pages:
        return True
    return any(m in markdown.lower() for m in _REFUSAL_MARKERS)

def _split_pages_relative(markdown, first, last):
    matches = list(PAGE_HEADER.finditer(markdown))
    pages = {}
    for i, m in enumerate(matches):
        real = first + int(m.group(1)) - 1          # model numbers the slice from 1
        end = matches[i + 1].start() if i + 1 < len(matches) else len(markdown)
        if first <= real <= last:
            pages[real] = markdown[m.end():end].strip()
    return pages

def _is_transient(e):
    code = getattr(e, "code", None) or getattr(e, "status_code", None)
    text = str(e).lower()
    return code in (429, 500, 502, 503, 504) or any(
        s in text for s in ("resource_exhausted", "unavailable", "overloaded", "rate limit"))

def _call_vision(client, pdf_bytes, prompt, retries=3):
    for attempt in range(retries + 1):
        try:
            r = client.models.generate_content(
                model=VISION_MODEL,
                contents=[types.Content(role="user", parts=[
                    types.Part.from_bytes(data=pdf_bytes, mime_type="application/pdf"),
                    types.Part.from_text(text=prompt)])],
                config=types.GenerateContentConfig(max_output_tokens=8192))
            return (r.text or "").strip()
        except Exception as e:
            if _is_transient(e) and attempt < retries:
                time.sleep(2 ** (attempt + 1)); continue
            raise

def _slice_pdf(reader, start, end):
    w = PdfWriter()
    for i in range(start, end):
        w.add_page(reader.pages[i])
    buf = io.BytesIO(); w.write(buf)
    return buf.getvalue()

def _cache_path(t): return os.path.join(CACHE_DIR, f"manual_pages_{t}.json")

def _load_cache(t):
    try:    return json.load(open(_cache_path(t), encoding="utf-8"))
    except (OSError, json.JSONDecodeError): return {}

def _save_cache(t, cache):
    os.makedirs(CACHE_DIR, exist_ok=True)
    tmp = _cache_path(t) + ".tmp"
    json.dump(cache, open(tmp, "w", encoding="utf-8"), ensure_ascii=False)
    os.replace(tmp, _cache_path(t))                    # atomic

def transcribe_pdf(tenant_id, pdf_path, client):
    raw = open(pdf_path, "rb").read()
    name = os.path.basename(pdf_path)
    key = f"{hashlib.sha256(raw).hexdigest()}:{PROMPT_VERSION}:{VISION_MODEL}"
    cache = _load_cache(tenant_id)
    entry = cache.setdefault(key, {"pdf": name, "pages": {}})
    reader = PdfReader(io.BytesIO(raw)); total = len(reader.pages)
    title = os.path.splitext(name)[0]

    for start in range(0, total, PAGES_PER_CALL):
        end = min(start + PAGES_PER_CALL, total)
        first, last, count = start + 1, end, end - start
        if all(str(p) in entry["pages"] for p in range(first, last + 1)):
            continue
        prompt = TRANSCRIBE_PROMPT.format(count=count, title=title)
        data = _slice_pdf(reader, start, end)
        md = _call_vision(client, data, prompt)
        if _looks_like_refusal(md, count):
            md = _call_vision(client, data, prompt + "\n\nReminder: you were given exactly the "
                              "pages attached. Transcribe them now without asking for anything else.")
        if _looks_like_refusal(md, count):
            continue                                   # not cached, retried on the next run
        got = _split_pages_relative(md, first, last)
        for p in range(first, last + 1):
            entry["pages"][str(p)] = got.get(p, "")
        _save_cache(tenant_id, cache)
    return {int(k): v for k, v in entry["pages"].items()}, total

def _body_of(path):
    parts = open(path, encoding="utf-8").read().split("---", 2)
    return parts[2].strip() if len(parts) == 3 else ""

def write_raw_pages(tenant_id, pdf_name, pages):
    title = os.path.splitext(pdf_name)[0]
    slug = re.sub(r"[^a-z0-9]+", "_", title.lower()).strip("_") or "manual"
    out = os.path.join(KB_DIR, str(tenant_id), "raw"); os.makedirs(out, exist_ok=True)
    want = {f"{slug}_p{n:03d}.md": (n, t.strip()) for n, t in pages.items() if t.strip()}
    for f in os.listdir(out):                          # stale pages from an older version
        if f.startswith(f"{slug}_p") and f.endswith(".md") and f not in want:
            os.remove(os.path.join(out, f))
    changed = 0
    for fname, (n, text) in want.items():
        path = os.path.join(out, fname)
        if os.path.exists(path) and _body_of(path) == text:
            continue                                   # unchanged, keeps verified: true
        fm = {"type": "manual_page", "title": f"{title}, page {n}", "page": n,
              "source_pdf": pdf_name, "verified": False}
        open(path, "w", encoding="utf-8").write(
            "---\n" + yaml.safe_dump(fm, sort_keys=False, allow_unicode=True) + "---\n\n" + text + "\n")
        changed += 1
    return {"pdf": pdf_name, "files": len(want), "written": changed,
            "blank_pages": sorted(n for n, t in pages.items() if not t.strip())}
    
if __name__ == "__main__":
    import sys
    from dotenv import load_dotenv
    from google import genai
    load_dotenv()
    client = genai.Client(api_key=os.environ["GEMINI_API_KEY"])
    tenant = sys.argv[1] if len(sys.argv) > 1 else "demo"
    pdfs = sorted(f for f in os.listdir(SOURCE_DIR) if f.lower().endswith(".pdf"))
    if not pdfs:
        sys.exit(f"No PDFs found in {SOURCE_DIR}")
    for f in pdfs:
        pages, total = transcribe_pdf(tenant, os.path.join(SOURCE_DIR, f), client)
        print(write_raw_pages(tenant, f, pages), f"- {len(pages)}/{total} pages transcribed")