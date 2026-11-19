import os, sys, logging, time
from dotenv import load_dotenv
from google import genai
from google.genai import types
import rag_pipeline as rag
import tenant_config as tc 

load_dotenv()
client = genai.Client(api_key=os.environ["GEMINI_API_KEY"])
MODEL = "gemini-3.1-flash-lite"   # verify
log = logging.getLogger("voicedesk.agent")


def llm(system, user):
    try:
        r = client.models.generate_content(
            model=MODEL, contents=user,
            config=types.GenerateContentConfig(system_instruction=system, temperature=0.2))
    except Exception:
        log.exception("gemini call failed")
        raise
    u = r.usage_metadata
    log.info("tokens prompt=%s output=%s thoughts=%s total=%s",
             getattr(u, "prompt_token_count", None), getattr(u, "candidates_token_count", None),
             getattr(u, "thoughts_token_count", None), getattr(u, "total_token_count", None))
    return r.text


def answer(tenant_id, question):
    r = tc.load_tenant(tenant_id)["retrieval"]
    kw = dict(top_k=r["top_k"], min_score=r["min_score"])
    hits = (rag.search(tenant_id, question, tier="concept", **kw)
            or rag.search(tenant_id, question, tier="raw", **kw))
    log.info("tenant=%s q=%r hits=%s", tenant_id, question,
             [(h["source"], h["tier"], h["score"]) for h in hits])
    if not hits:
        log.warning("tenant=%s no hits for q=%r", tenant_id, question)
    context = "\n\n".join(h["text"] for h in hits) or "(nothing relevant found)"
    system = "Answer using only the context. If it has no answer, say you don't know."
    user = f"<context>\n{context}\n</context>\n\nQuestion: {question}"
    t0 = time.time()
    reply = llm(system, user)
    log.info("tenant=%s llm_ms=%d\nreply:\n%s", tenant_id, (time.time() - t0) * 1000, reply)
    return reply, hits


def resolve_tenant(value):
    if value in tc.all_tenant_ids():                       # tenant id
        return value
    t = tc.tenant_for_number(value)                        # dialed number
    if t:
        return t
    matches = [t for t in tc.all_tenant_ids()              # business name
               if tc.load_tenant(t)["business_name"].lower() == value.strip().lower()]
    return matches[0] if len(matches) == 1 else None


if __name__ == "__main__":
    logging.basicConfig(filename="voicedesk.log", level=logging.INFO,
                        format="%(asctime)s %(levelname)s %(message)s")
    logging.getLogger("httpx").setLevel(logging.WARNING)
    
    if len(sys.argv) != 2:
        sys.exit('usage: python agent.py <tenant id | phone number | business name>')
    TENANT_ID = resolve_tenant(sys.argv[1])
    
    if not TENANT_ID:
        sys.exit(f"no tenant matches {sys.argv[1]!r}")
    cfg = tc.load_tenant(TENANT_ID)
    log.info("resolved %r -> %s", sys.argv[1], TENANT_ID)
    print(cfg["greeting"].format(business_name=cfg["business_name"]))
    
    result= rag.ingest_tenant(TENANT_ID, only_verified=False)  # skips unchanged files
    print(result)
    log.info("ingest tenant=%s %s", TENANT_ID, result)
    if "error" in result:
        sys.exit(1)
    print("\nType exit/quit to exit.\n")
    while True:
        q = input("You: ").strip()
        if q.lower() in ("exit", "quit"):
            break
        if not q:
            continue
        reply, hits = answer(TENANT_ID, q)
        print(f"Response: {reply}")
        print("Sources:", ", ".join(sorted({h["source"] for h in hits})) or "none", "\n")