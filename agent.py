import os
from dotenv import load_dotenv
from google import genai
from google.genai import types
import rag_pipeline as rag

load_dotenv()
client = genai.Client(api_key=os.environ["GEMINI_API_KEY"])
MODEL = "gemini-3.1-flash-lite"   # verify
TENANT_ID = "demo"                # your kb/<tenant_id> folder name

def llm(system, user):
    r = client.models.generate_content(
        model=MODEL, contents=user,
        config=types.GenerateContentConfig(system_instruction=system, temperature=0.2))
    return r.text

def answer(tenant_id, question):
    hits = (rag.search(tenant_id, question, top_k=5, tier="concept")
            or rag.search(tenant_id, question, top_k=5, tier="raw"))
    context = "\n\n".join(h["text"] for h in hits) or "(nothing relevant found)"
    system = "Answer using only the context. If it has no answer, say you don't know."
    user = f"<context>\n{context}\n</context>\n\nQuestion: {question}"
    return llm(system, user), hits

if __name__ == "__main__":
    print(rag.ingest_tenant(TENANT_ID, only_verified=False))  # skips unchanged files
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