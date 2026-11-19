"""tenant_docs.py - one tenant's documents: add, review, index, remove.

Flow for a manual (run from the VoiceDesk folder, after tenant_config.py created the tenant):
    python tenant_docs.py add    <tenant> <file.pdf>                 validate, copy, transcribe to kb/<tenant>/raw/
    python tenant_docs.py status <tenant>                            what is transcribed, reviewed, indexed
    python tenant_docs.py verify <tenant> <doc> --pages 1-20,25      mark pages you have checked against the PDF
    python tenant_docs.py ingest <tenant>                            index verified pages only
    python tenant_docs.py remove <tenant> <doc> --yes                delete a document's pages and its PDF

<doc> is the name shown by `status` (the PDF name in lowercase with underscores).
`ingest --include-unverified` is for development only: it serves unreviewed transcriptions.
After `remove`, run `ingest` again so the document leaves the search index.
"""
import argparse, os, re, shutil, sys
import yaml
import tenant_config as tc

SOURCE_DIR = os.path.join(tc.BASE_DIR, "manual_sources")     # manual_sources/<tenant>/*.pdf
MAX_MB, MAX_PAGES = 50, 400
_PAGE_RE = re.compile(r"^(?P<slug>.+)_p(?P<n>\d{3,})\.md$")


def _slug(pdf_name):                       # must match manual_qa.write_raw_pages
    return re.sub(r"[^a-z0-9]+", "_", os.path.splitext(pdf_name)[0].lower()).strip("_") or "manual"


def _pdfs(tenant):
    d = os.path.join(SOURCE_DIR, tenant)
    return sorted(f for f in os.listdir(d) if f.lower().endswith(".pdf")) if os.path.isdir(d) else []


def _pages(tenant):
    """{doc: {page_number: path}} for the tenant's transcribed pages."""
    out, d = {}, os.path.join(tc.KB_DIR, tenant, "raw")
    if os.path.isdir(d):
        for f in sorted(os.listdir(d)):
            m = _PAGE_RE.match(f)
            if m:
                out.setdefault(m["slug"], {})[int(m["n"])] = os.path.join(d, f)
    return out


def _read(path):
    with open(path, encoding="utf-8") as f:
        parts = f.read().split("---", 2)
    if len(parts) != 3:
        raise ValueError(f"{path}: missing front matter")
    return yaml.safe_load(parts[1]) or {}, parts[2]


def _write(path, meta, body):              # same layout manual_qa writes, body kept byte-for-byte
    with open(path, "w", encoding="utf-8") as f:
        f.write("---\n" + yaml.safe_dump(meta, sort_keys=False, allow_unicode=True) + "---" + body)


def _parse_pages(spec):
    pages = set()
    for part in spec.split(","):
        a, _, b = part.strip().partition("-")
        pages.update(range(int(a), int(b or a) + 1))
    return pages


def _ranges(nums):
    out = []
    for n in sorted(nums):
        if out and n == out[-1][1] + 1:
            out[-1][1] = n
        else:
            out.append([n, n])
    return ",".join(str(a) if a == b else f"{a}-{b}" for a, b in out)


def add(tenant, pdf_path, replace=False):
    tc.load_tenant(tenant)                                   # the tenant must exist
    if not os.path.isfile(pdf_path):
        sys.exit(f"file not found: {pdf_path}")
    with open(pdf_path, "rb") as f:
        if f.read(5) != b"%PDF-":
            sys.exit("not a PDF file")
    if os.path.getsize(pdf_path) / 1e6 > MAX_MB:
        sys.exit(f"file is larger than {MAX_MB} MB")
    from pypdf import PdfReader
    try:
        pages = len(PdfReader(pdf_path).pages)
    except Exception as e:
        sys.exit(f"could not read the PDF: {e}")
    if pages > MAX_PAGES:
        sys.exit(f"{pages} pages is over the {MAX_PAGES}-page limit")

    dest_dir = os.path.join(SOURCE_DIR, tenant)
    os.makedirs(dest_dir, exist_ok=True)
    dest = os.path.join(dest_dir, os.path.basename(pdf_path))
    if os.path.exists(dest) and not replace and os.path.abspath(dest) != os.path.abspath(pdf_path):
        sys.exit(f"{os.path.basename(dest)} already exists for this tenant; use --replace to upload a new version")
    if os.path.abspath(dest) != os.path.abspath(pdf_path):
        shutil.copyfile(pdf_path, dest)

    import manual_qa
    from dotenv import load_dotenv
    from google import genai
    load_dotenv()
    client = genai.Client(api_key=os.environ["GEMINI_API_KEY"])
    print(f"transcribing {pages} pages (resumable: rerun the same command if it stops)")
    result, total = manual_qa.transcribe_pdf(tenant, dest, client)
    print(manual_qa.write_raw_pages(tenant, os.path.basename(dest), result), f"- {len(result)}/{total} pages")
    print("next: status, check pages against the PDF, verify, ingest")


def status(tenant):
    cfg = tc.load_tenant(tenant)
    print(f"tenant: {tenant} ({cfg['business_name']})")
    docs = _pages(tenant)
    for f in _pdfs(tenant):
        if _slug(f) not in docs:
            print(f"  {f}: uploaded, not transcribed yet")
    for doc, pages in docs.items():
        unver = [n for n, p in pages.items() if _read(p)[0].get("verified") is not True]
        print(f"  {doc}: {len(pages)} pages, {len(pages) - len(unver)} verified")
        if unver:
            print(f"    awaiting review: pages {_ranges(unver)}")
    try:
        import rag_pipeline as rag
        print("  indexed chunks:", rag._col(tenant).count())
    except Exception:
        print("  indexed chunks: unavailable here")


def verify(tenant, doc, pages_spec, all_pages):
    pages = _pages(tenant).get(doc)
    if not pages:
        sys.exit(f"no pages for {doc!r}; run status to see the document names")
    if not all_pages and not pages_spec:
        sys.exit("give --pages 1-20,25 (or --all if you have checked every page)")
    want = set(pages) if all_pages else _parse_pages(pages_spec)
    if want - set(pages):
        sys.exit(f"no such pages: {_ranges(want - set(pages))}")
    changed = 0
    for n in sorted(want):
        meta, body = _read(pages[n])
        if meta.get("verified") is not True:
            meta["verified"] = True
            _write(pages[n], meta, body)
            changed += 1
    print(f"marked {changed} pages verified")


def ingest(tenant, include_unverified):
    tc.load_tenant(tenant)
    import rag_pipeline as rag
    print(rag.ingest_tenant(tenant, only_verified=not include_unverified))


def remove(tenant, doc, yes):
    pages = _pages(tenant).get(doc)
    pdfs = [f for f in _pdfs(tenant) if _slug(f) == doc]
    if not pages and not pdfs:
        sys.exit(f"nothing found for {doc!r}")
    print(f"will delete {len(pages or {})} page files and PDFs: {pdfs or 'none'}")
    if not yes:
        sys.exit("add --yes to confirm")
    for p in (pages or {}).values():
        os.remove(p)
    for f in pdfs:
        os.remove(os.path.join(SOURCE_DIR, tenant, f))
    print("deleted. Run ingest to remove it from the search index.")


if __name__ == "__main__":
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    sub = ap.add_subparsers(dest="cmd", required=True)
    p = sub.add_parser("add");    p.add_argument("tenant"); p.add_argument("pdf"); p.add_argument("--replace", action="store_true")
    p = sub.add_parser("status"); p.add_argument("tenant")
    p = sub.add_parser("verify"); p.add_argument("tenant"); p.add_argument("doc"); p.add_argument("--pages"); p.add_argument("--all", action="store_true")
    p = sub.add_parser("ingest"); p.add_argument("tenant"); p.add_argument("--include-unverified", action="store_true")
    p = sub.add_parser("remove"); p.add_argument("tenant"); p.add_argument("doc"); p.add_argument("--yes", action="store_true")
    a = ap.parse_args()
    t = tc.resolve_tenant(a.tenant)
    if not t:
        sys.exit(f"no tenant matches {a.tenant!r}")
    a.tenant = t
    try:
        if a.cmd == "add":      add(a.tenant, a.pdf, a.replace)
        elif a.cmd == "status": status(a.tenant)
        elif a.cmd == "verify": verify(a.tenant, a.doc, a.pages, a.all)
        elif a.cmd == "ingest": ingest(a.tenant, a.include_unverified)
        else:                   remove(a.tenant, a.doc, a.yes)
    except (FileNotFoundError, FileExistsError, ValueError) as e:
        sys.exit(f"error: {e}")