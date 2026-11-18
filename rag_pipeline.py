import os, re, json, hashlib
from collections import OrderedDict
import numpy as np, yaml, chromadb

BASE_DIR = os.path.dirname(os.path.abspath(__file__))
KB_DIR = os.path.join(BASE_DIR, "kb")                    # kb/<tenant_id>/**/*.md
STORE_DIR = os.path.join(BASE_DIR, "vector_store")
EMBEDDING_MODEL = "all-MiniLM-L6-v2"   # swap after your bake-off
INGEST_VERSION = "v4"        # bump when chunking changes -> everything re-ingests

BREAKPOINT_PERCENTILE = 90   # higher = fewer, larger chunks
MAX_CHUNK_TOKENS = 180       # model limit 128 minus ~25 for the "title: " prefix and special tokens

MIN_CHUNK_SENTENCES = 2
MIN_CHUNK_WORDS = 15
MIN_SCORE = 0.20             # placeholder: measure it
CACHE_MAX = 500
SKIP_FILES = {"index.md", "log.md", "changelog.md"}

_model_obj = None; _client = None; _cache = OrderedDict()

def _model():
    global _model_obj
    if _model_obj is None:
        from sentence_transformers import SentenceTransformer
        try:    _model_obj = SentenceTransformer(EMBEDDING_MODEL, local_files_only=True)
        except Exception: _model_obj = SentenceTransformer(EMBEDDING_MODEL)
    return _model_obj

def _col(tenant_id):
    global _client
    if _client is None:
        _client = chromadb.PersistentClient(path=STORE_DIR)
    return _client.get_or_create_collection(f"tenant_{tenant_id}", metadata={"hnsw:space": "cosine"})

_SENT_RE = re.compile(r"(?<=[।॥!?])\s+|(?<=\.)\s+(?=[A-Z0-9\"'(]|[^\x00-\x7F])")

def split_sentences(text):
    text = re.sub(r"\s+", " ", text).strip()
    return [s.strip() for s in _SENT_RE.split(text) if s.strip()] if text else []

def _tok():
    return _model().tokenizer

def _token_counts(texts):
    return [len(ids) for ids in _tok()(texts, add_special_tokens=False)["input_ids"]]

def ntokens(text):
    return _token_counts([text])[0]

def _fit(sentence, n_tok):
    if n_tok <= MAX_CHUNK_TOKENS:
        return [sentence]
    words = sentence.split()
    wt = _token_counts(words)
    pieces, cur, t = [], [], 0
    for w, n in zip(words, wt):
        if cur and t + n > MAX_CHUNK_TOKENS:
            pieces.append(" ".join(cur)); cur, t = [], 0
        cur.append(w); t += n
    if cur:
        pieces.append(" ".join(cur))
    return pieces

def semantic_chunks(text, model):
    sents = split_sentences(text)
    tcount = _token_counts(sents) if sents else []
    if len(sents) <= MIN_CHUNK_SENTENCES:
        return [p for s, n in zip(sents, tcount) for p in _fit(s, n)]
    vecs = np.asarray(model.encode(sents, normalize_embeddings=True))
    dists = 1 - (vecs[:-1] * vecs[1:]).sum(axis=1)
    cutoff = np.percentile(dists, BREAKPOINT_PERCENTILE)
    chunks, cur, toks = [], [], 0
    def flush():
        nonlocal cur, toks
        if cur: chunks.append(" ".join(cur))
        cur, toks = [], 0
    for i, (s, n) in enumerate(zip(sents, tcount)):
        if n > MAX_CHUNK_TOKENS:
            flush(); chunks.extend(_fit(s, n)); continue
        if cur and toks + n > MAX_CHUNK_TOKENS:
            flush()
        cur.append(s); toks += n
        if i < len(dists) and dists[i] > cutoff and len(cur) >= MIN_CHUNK_SENTENCES:
            flush()
    flush()
    return chunks

def merge_tiny(chunks):
    out = []
    for c in chunks:
        if out and len(c.split()) < MIN_CHUNK_WORDS and ntokens(out[-1] + " " + c) <= MAX_CHUNK_TOKENS:
            out[-1] += " " + c
        else:
            out.append(c)
    if (len(out) > 1 and len(out[0].split()) < MIN_CHUNK_WORDS
            and ntokens(out[0] + " " + out[1]) <= MAX_CHUNK_TOKENS):
        out[1] = out[0] + " " + out[1]; out = out[1:]
    return out

def chunk_body(body, model):
    body = body.strip()
    if not body:
        return []
    if ntokens(body) <= MAX_CHUNK_TOKENS:             # an OKF concept stays whole
        return [body]
    return merge_tiny(semantic_chunks(body, model))

def chunk_body(body, model):
    body = body.strip()
    if len(body.split()) <= MAX_CHUNK_TOKENS:          # an OKF concept stays whole
        return [body] if body else []
    return merge_tiny(semantic_chunks(body, model))


_FM_RE = re.compile(r"^---\s*\n(.*?)\n---\s*\n?(.*)$", re.S)

def parse_okf(text):
    m = _FM_RE.match(text)
    if m:
        try:
            parsed = yaml.safe_load(m.group(1))
            if isinstance(parsed, dict):           # a bare '---' rule is not frontmatter
                return parsed, m.group(2)
        except yaml.YAMLError:
            pass
    return {}, text

def _manifest_path(t): return os.path.join(STORE_DIR, f"manifest_{t}.json")
def _load_manifest(t):
    p = _manifest_path(t)
    return json.load(open(p, encoding="utf-8")) if os.path.exists(p) else {}
def _save_manifest(t, m):
    os.makedirs(STORE_DIR, exist_ok=True)
    json.dump(m, open(_manifest_path(t), "w", encoding="utf-8"), indent=2)

def _file_hash(path, salt):
    h = hashlib.sha256(salt.encode()); h.update(open(path, "rb").read())
    return h.hexdigest()

def _iter_files(root):
    found = {}
    for d, _, files in os.walk(root):
        for f in files:
            if f.lower().endswith((".md", ".txt")) and f.lower() not in SKIP_FILES:
                p = os.path.join(d, f)
                found[os.path.relpath(p, root).replace(os.sep, "/")] = p
    return found

_EMPTY_FIELD = re.compile(r"[\w ./()\-]{1,40}:\s*;\s*")
_LEADER = re.compile(r"[.·_=\-–—*]{4,}")
_LONG_RUN = re.compile(r"(\S{60})\S+")

def is_toc(text):
    return len(re.findall(r"\.{4,}", text)) >= 5

def clean_body(body):
    body = _LEADER.sub(" ", body)
    body = _EMPTY_FIELD.sub(" ", body)
    body = _LONG_RUN.sub(r"\1", body)
    return re.sub(r"[ \t]+", " ", body)

def ingest_tenant(tenant_id, only_verified=True):
    root = os.path.join(KB_DIR, str(tenant_id))
    if not os.path.isdir(root):
        return {"error": f"no knowledge folder for tenant {tenant_id}"}
    model, col = _model(), _col(tenant_id)
    manifest, files = _load_manifest(tenant_id), _iter_files(root)
    salt = f"{INGEST_VERSION}|verified={only_verified}"      # flipping the mode re-ingests too

    for gone in set(manifest) - set(files):                   # file deleted from disk
        col.delete(where={"source": gone}); del manifest[gone]

    done = skipped = n_chunks = held_back = toc_skipped = 0
    toc_files = []
    for rel, path in files.items():
        h = _file_hash(path, salt)
        if manifest.get(rel) == h:
            skipped += 1; continue
        col.delete(where={"source": rel})
        meta, body = parse_okf(open(path, encoding="utf-8").read())
        if meta.get("type") == "manual_page":
            if is_toc(body):                         # check BEFORE cleaning
                toc_skipped += 1; manifest[rel] = h; continue
            body = clean_body(body)
        if only_verified and meta.get("verified") is not True:   # unreviewed stays out
            held_back += 1; manifest[rel] = h; continue
        title = str(meta.get("title", rel))
        chunks = chunk_body(body, model)
        if chunks:
            vecs = model.encode([f"{title}: {c}" for c in chunks], normalize_embeddings=True).tolist()
            col.upsert(ids=[f"{rel}::{i}" for i in range(len(chunks))],
                       embeddings=vecs, documents=chunks,
                       metadatas=[{"source": rel, "title": title,
                                   "type": str(meta.get("type", "unknown")), "chunk": i,
                                   "tier": "raw" if meta.get("type") == "manual_page" else "concept"}
                                  for i in range(len(chunks))])
            n_chunks += len(chunks)
        manifest[rel] = h; done += 1
    _save_manifest(tenant_id, manifest)
    clear_cache(tenant_id)
    return {"ingested": done, "chunks": n_chunks, "skipped": skipped, "held_back": held_back, "toc_skipped": toc_skipped, "toc_files": toc_files}


def clear_cache(tenant_id=None):
    for k in [k for k in _cache if tenant_id is None or k[0] == tenant_id]:
        del _cache[k]

def search(tenant_id, query, top_k=3, min_score=MIN_SCORE, tier=None):
    key = (tenant_id, re.sub(r"\s+", " ", query.strip().lower()), top_k, min_score, tier)
    if key in _cache:
        _cache.move_to_end(key)
        return [dict(h) for h in _cache[key]]            # copies, so callers can't corrupt the cache
    col = _col(tenant_id)
    if col.count() == 0:
        return []
    q = _model().encode([query], normalize_embeddings=True).tolist()
    kwargs = {"query_embeddings": q, "n_results": min(top_k, col.count())}
    if tier:
        kwargs["where"] = {"tier": tier}
    res = col.query(**kwargs)
    hits = [{"text": d, "source": m["source"], "title": m["title"], "tier": m["tier"],
             "score": round(1 - dist, 4)}
            for d, m, dist in zip(res["documents"][0], res["metadatas"][0], res["distances"][0])
            if 1 - dist >= min_score]
    _cache[key] = hits
    if len(_cache) > CACHE_MAX:
        _cache.popitem(last=False)
    return [dict(h) for h in hits]