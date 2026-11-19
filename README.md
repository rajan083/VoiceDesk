# VoiceDesk

A multi-tenant voice assistant backend for small businesses. Each business (tenant) has its own
phone number, settings and knowledge base. A caller's question is answered from that tenant's
documents only, using retrieval-augmented generation (RAG).

Status: terminal prototype. The phone/telephony layer is not built yet.

## How it works

1. A manual (PDF) is added to a tenant. Gemini transcribes it page by page into Markdown files.
2. You check the transcribed pages against the PDF and mark them verified.
3. The verified pages are split into chunks, embedded and stored in a per-tenant vector collection.
4. When a question comes in, the agent finds the closest chunks and asks Gemini to answer using
   only that context. If the context has no answer, it says it doesn't know.

## Project layout

| Path | Purpose |
|---|---|
| `agent.py` | Question-answering CLI. Resolves the tenant, indexes new pages, answers questions, writes `agent.log`. |
| `rag_pipeline.py` | Chunking, embedding (sentence-transformers), vector store (ChromaDB) and search. One collection per tenant. |
| `manual_qa.py` | PDF to Markdown transcription with Gemini. Resumable, cached in `cache/`. |
| `tenant_config.py` | Creates tenants and resolves tenant ID, phone number or business name to a tenant. |
| `tenant_docs.py` | Per-tenant document commands: add, status, verify, ingest, remove. |
| `tenants/<id>/config.json` | Tenant settings (greeting, phone numbers, retrieval settings, owner contacts). |
| `kb/<id>/raw/` | Transcribed pages, one Markdown file per page. |
| `manual_sources/<id>/` | The original PDFs. |
| `vector_store/` | Persistent ChromaDB data and per-tenant ingest manifests. |

## Setup

```powershell
python -m venv .venv
.venv\Scripts\Activate.ps1
pip install -r requirements.txt
```

Create a `.env` file in the project folder:

```
GEMINI_API_KEY=your-key-here
```

`.env`, `agent.log`, `vector_store/`, `cache/`, `kb/` and `manual_sources/` should be listed in
`.gitignore`. Never commit the API key.

## Usage

Anywhere a command takes `<tenant>`, you can give the tenant ID, an assigned phone number, or the
exact business name. Quote values that contain spaces or a `+`.

### 1. Create a tenant

```powershell
python tenant_config.py new "PlumbingAgency" --number +14155550100
python tenant_config.py list
python tenant_config.py check
```

Tenant IDs are generated (`t_` plus 12 hex characters). Pass `--id demo` only for a fixed name.

### 2. Add and review a document

```powershell
python tenant_docs.py add <tenant> guide.pdf
python tenant_docs.py status <tenant>
python tenant_docs.py verify <tenant> <doc> --pages 1-20,25    # or --all
python tenant_docs.py ingest <tenant>
```

`<doc>` is the name shown by `status` (the PDF name in lowercase with underscores). Only verified
pages are indexed. `ingest --include-unverified` is for development only.

To remove a document, run `python tenant_docs.py remove <tenant> <doc> --yes`, then `ingest` again.

### 3. Ask questions

```powershell
python agent.py "PlumbingAgency"
```

The agent plays the tenant's greeting, indexes any new pages, and then answers questions until you
type `exit` or `quit`. Each question, the sources and scores that were retrieved, token usage and the
reply are written to `agent.log`.

## Tenant configuration

`tenants/<id>/config.json` is merged over these defaults:

```json
{
  "language": "en",
  "greeting": "Thank you for calling {business_name}. How can I help?",
  "phone_numbers": [],
  "owner": {"notify_telegram_chat_id": null, "transfer_number": null},
  "retrieval": {"top_k": 5, "min_score": 0.20}
}
```

Phone numbers must be in +E.164 form. A number can belong to only one tenant. Tune `top_k` and
`min_score` per tenant against its own test questions.

## Design rules

- The server decides the tenant from the dialed number. The tenant is never taken from the caller's
  speech or from the model, and an unknown number never falls back to a default tenant.
- Each tenant has a separate vector collection, so one tenant's search never touches another's data.
- Unreviewed transcriptions are not served in production. Check numbers such as prices and hours
  against the PDF before you verify a page.

## Models

- Transcription and answers: Gemini (`gemini-3.1-flash-lite`, set in `manual_qa.py` and `agent.py`).
- Embeddings: `all-MiniLM-L6-v2` (sentence-transformers), loaded once per process.

## Roadmap

- [ ] Test set per tenant, to measure retrieval and tune `min_score`
- [ ] Voice-friendly answers (short, no lists) and "take a message" or transfer when the answer is unknown
- [ ] Short conversation memory within a call
- [ ] FastAPI server and telephony (dialed number resolved with `tenant_for_number`)
- [ ] Tests that one tenant can never retrieve another tenant's chunks