import os, sys, logging, time
from dotenv import load_dotenv
from google import genai
from google.genai import types
import rag_pipeline as rag
import tenant_config as tc
from tts import tts_llm
from stt import stt_llm
from session import Session

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


def answer(tenant_id, question, voice=False):
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
    if voice:
        system += " Reply in one or two short plain sentences, with no lists or markdown."
    user = f"<context>\n{context}\n</context>\n\nQuestion: {question}"
    t0 = time.time()
    reply = llm(system, user)
    log.info("tenant=%s llm_ms=%d\nreply:\n%s", tenant_id, (time.time() - t0) * 1000, reply)
    return reply, hits


def voice_answer(session, audio_path, out_path="reply.wav"):
    t0 = time.time(); question = stt_llm(audio_path)
    t1 = time.time(); reply, hits = session.turn(question, voice=True)
    t2 = time.time(); tts_llm(reply, filename=out_path)
    t3 = time.time()
    log.info("tenant=%s stt_ms=%d turn_ms=%d tts_ms=%d heard=%r",
             session.tenant_id, (t1-t0)*1000, (t2-t1)*1000, (t3-t2)*1000, question)
    return question, reply, hits


if __name__ == "__main__":
    logging.basicConfig(filename="voicedesk.log", level=logging.INFO,
                        format="%(asctime)s %(levelname)s %(message)s")
    logging.getLogger("httpx").setLevel(logging.WARNING)
    
    
    if len(sys.argv) not in (2, 3):
        sys.exit('usage: python agent.py <tenant id | phone number | business name> [caller number]')
    TENANT_ID = tc.resolve_tenant(sys.argv[1])
    if not TENANT_ID:
        sys.exit(f"no tenant matches {sys.argv[1]!r}")
    try:
        caller = tc.normalize_number(sys.argv[2]) if len(sys.argv) == 3 else None
    except ValueError as e:
        sys.exit(str(e))
        
    cfg = tc.load_tenant(TENANT_ID)
    log.info("resolved %r -> %s", sys.argv[1], TENANT_ID)
    print(cfg["greeting"].format(business_name=cfg["business_name"]))
    
    result= rag.ingest_tenant(TENANT_ID, only_verified=False)  # skips unchanged files
    print(result)
    log.info("ingest tenant=%s %s", TENANT_ID, result)
    if "error" in result:
        sys.exit(1)
    print("\nType exit/quit to exit.\n")
    session= Session(TENANT_ID, client, MODEL, caller_phone=caller)
    while True:
        q = input("You: ").strip()
        if q.lower() in ("exit", "quit"):
            try:
                print(session.close() or "")
            except Exception:
                log.exception("summary failed")
            break
        if not q:
            continue
        if q.lower().endswith(".wav"):
            try:
                heard, reply, hits = voice_answer(session, q)
            except Exception:
                log.exception("voice turn failed")
                print("Response: Sorry, I'm having trouble right now. Please try again in a moment.\n")
                continue
            print(f"Heard: {heard}\nResponse: {reply}")
            print("Sources:", ", ".join(sorted({h["source"] for h in hits})) or "none", "\n")
            os.startfile("reply.wav")
            continue
        try:
            reply, hits = session.turn(q)
        except Exception:
            log.exception("turn failed on every model")
            print("Response: Sorry, I'm having trouble right now. Please try again in a moment.\n")
            continue
        print(f"Response: {reply}")
        log.info("Sources: %s", ", ".join(sorted({h["source"] for h in hits})) or "none")