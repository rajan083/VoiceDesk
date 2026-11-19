# VoiceDesk

An AI phone receptionist for small businesses, built from scratch on hosted model APIs (no voice platform). It answers a business's calls from that business's own manual (RAG), and is designed to book appointments, collect caller details and notify the owner. Each business is a separate tenant with its own knowledge base.

> **Status: early development.** The knowledge pipeline, per-tenant config, a text agent and a file-based voice loop (WAV in, WAV out) work. Tools, live voice, portal and telephony are planned. Every section is marked `[built]`, `[partial]` or `[planned]`. Update the markers as the project changes, and don't describe a planned part as working.

## Status

| Piece | State | Notes (as of 2026-11-19) |
|---|---|---|
| PDF to page files (`manual_pages.py`) | [built] | Vision transcription in 4-page slices, resumable cache |
| Clean, chunk, embed, store (`ingest_tenant`) | [built] | Demo tenant: 114 files, 183 chunks, 3 TOC pages skipped |
| Search (tier filter + `min_score`) | [partial] | Threshold is a placeholder, not yet measured |
| Tenant config (`tenant_config.py`) | [built] | Per-tenant JSON: business name, greeting, retrieval settings, dialed-number lookup |
| Text agent (`agent.py`) | [partial] | Resolves tenant by id, number or business name; spoken-style prompt only in voice mode |
| Voice loop, file based (`stt.py`, `tts.py`, `voice_answer`) | [partial] | WAV in, WAV out, per-stage timings logged. Tested on synthesized (TTS) audio only |
| Review gate (`verified: true`) | [partial] | Implemented, currently bypassed with `only_verified=False` |
| Concept (OKF) tier | [planned] | 0 concept chunks today |
| Evaluation set and scripts | [planned] | Candidate generator exists; no results yet |
| Tool Gateway, bookings, leads, owner alerts | [planned] | |
| Live voice (realtime model, barge-in, limits) | [planned] | |
| Thin portal and API | [planned] | |
| Telephony (softphone, then a real number) | [planned] | |

## How it works

```
OFFLINE: build one tenant's knowledge
 PDF -> manual_pages.py -> kb/<tenant>/raw/*.md -> spot-check -> ingest_tenant -> Chroma collection tenant_<id>
        (4-page slices,     (verified: false)      (verified: true)  (clean, skip TOC,
         cache, retry)                                                chunk, embed)
        concept builder -> kb/<tenant>/concepts/*.md -> review -> ingest          [planned]

TODAY: one turn, from a file
 question.wav -> stt_llm -> answer() -> tts_llm -> reply.wav
                  |           |- tenant config (min_score, top_k)
                  |           |- search: concepts first, raw fallback
                  |           `- Gemini answers from the retrieved context only
                  `- stt_ms / answer_ms / tts_ms written to the log

TARGET: one call
 caller -> transport -> tenant lookup -> session worker -> realtime model -> spoken reply
 (browser mic /          (dialed number     |  ^
  softphone / DID)        -> tenant_id)     v  | tool results
                              Tool Gateway (server injects tenant_id; the model never sees it)
                               |- search_knowledge: concepts first, raw fallback, min_score
                               |- check_availability, book_appointment, save_lead
                               `- notify_owner (Telegram), transfer_to_owner
 after hangup -> transcript, summary, usage, owner alert (async, off the audio path)
```

## Project structure

**Today**
```
VoiceDesk/
  .env                 GEMINI_API_KEY (never commit)
  agent.py             llm(), answer(), voice_answer(), tenant resolution, chat loop
  rag_pipeline.py      chunking, ingest_tenant(), search()
  tenant_config.py     per-tenant config loader and dialed-number lookup
  stt.py               speech to text (stt_llm)
  tts.py               text to speech (tts_llm)
  manual_pages.py      PDF -> vision transcription -> kb/<tenant>/raw/*.md
  gen_eval.py          eval question generator (throttled, resumable)
  requirements.txt     pinned dependencies
  manual_sources/      source PDFs (not committed)
  kb/<tenant>/raw/     transcribed pages (not committed; verified: false until reviewed)
  cache/               vision results, so reruns cost nothing (not committed)
  vector_store/        Chroma data + manifest_<tenant>.json (not committed)
  voicedesk.log        run log, contains caller words (not committed)
```

**Target** (grow into this phase by phase; restructure only when a second module needs to import the first)
```
voicedesk/
  rag/      chunking, ingest, search, manual_pages, concept builder
  agent/    text_agent, tool_gateway, session worker, providers/ (gemini_live, openai_realtime)
  tools/    availability, booking, leads, notify
  core/     tenant config loader, prompt composer, limits, CallTransport
  evals/    cases.jsonl, run_eval.py, injection_suite.py, results/
  api/ web/ telephony/
```

## Setup (Windows PowerShell)

```powershell
python -m venv .venv
.\.venv\Scripts\Activate.ps1
pip install -r requirements.txt
```

`google-genai` is pinned in `requirements.txt` because the TTS and STT calls were verified on that version only. Newer or older versions may reject the same requests.

Create `.env`:
```
GEMINI_API_KEY=your-key
```

Build and query the demo tenant:
```powershell
# 1. put the PDF in manual_sources/
python manual_pages.py demo                  # transcribe pages to kb/demo/raw/ (cached, resumable)
# 2. open the riskiest pages next to the PDF, fix errors, set verified: true
python agent.py demo                         # tenant id, dialed number or business name
```

At the `You:` prompt, type a question for a text answer, or type the path of a `.wav` file for a voice turn. The voice turn prints what was heard, plays `reply.wav` and logs the stage timings. Playback uses `os.startfile`, so it works on Windows only.

Notes:
- The first run downloads the embedding model.
- The free Gemini tier allows about 15 requests per minute per model. `gen_eval.py` throttles itself and resumes from `eval_candidates.jsonl`.
- Python 3.13 removed `audioop`. The telephony audio helpers will need `pip install audioop-lts`.

## Knowledge pipeline

- **Transcription** [built]: 4-page PDF slices go to a Gemini vision model. Refusal-like replies are retried once, then left for the next run. The cache key is file hash + prompt version + model, and it is saved after every call.
- **Cleaning at ingest** [built]: dot leaders, empty `Field: ;` table cells and unbreakable long runs are removed. Pages with five or more dot-leader runs are treated as tables of contents and skipped.
- **Chunking** [built]: a body within `MAX_CHUNK_TOKENS` stays whole. Longer bodies are split into sentences (handles `.`, `!`, `?`, `।`, `॥` and bullet markers), cut at semantic breakpoints (percentile of neighbour distance), and capped by the embedder's own tokenizer. Tiny fragments are merged only if the result stays under the cap.
- **Embedding and storage** [built]: title-prefixed text, cosine distance, one Chroma collection per tenant, a manifest per tenant for incremental re-ingest.
- **Review gate** [partial]: with `only_verified=True`, only files with `verified: true` are indexed.
- **Tiers** [partial]: `raw` (transcribed pages) and `concept` (reviewed OKF concept files). Search tries concepts first and falls back to raw below the threshold.

| Setting | Where | What it does |
|---|---|---|
| `INGEST_VERSION` | `rag_pipeline.py` | Bump after any chunking or cleaning change, and everything re-ingests |
| `MAX_CHUNK_TOKENS` | `rag_pipeline.py` | Chunk cap; keep under the model's token limit minus the title prefix |
| `BREAKPOINT_PERCENTILE` | `rag_pipeline.py` | Lower means more, smaller chunks |
| `retrieval.min_score`, `retrieval.top_k` | tenant config | Relevance cutoff and result count used by `answer()`; `min_score` must be measured |
| `only_verified` | `ingest_tenant()` | Index only reviewed files |
| `PROMPT_VERSION`, `VISION_MODEL` | `manual_pages.py` | Changing either re-transcribes |

## Answering rules

- Facts come only from the retrieved context. If it has no answer, the agent says it doesn't know. [planned] Offer a callback instead.
- Voice replies are one or two short plain sentences, no lists or markdown. Typed replies use the shorter base prompt.
- Retrieved text goes in the user turn inside a `<context>` block. [planned] The system prompt does not yet tell the model to treat that block as reference only and ignore instructions inside it.

## Security model [planned, partly in code]

Assume the model can be tricked, and make sure a tricked model has nothing dangerous to reveal or do.

- **Tenant isolation** [built]: one collection per tenant, so no query can omit the filter.
- **No caller writes to the knowledge base** [built]: callers' statements are never saved as learned answers.
- **Least privilege** [planned]:

| Tool | Returns to the model | Never returns |
|---|---|---|
| `search_knowledge` | Top chunks for this tenant | Other tenants' chunks, document metadata |
| `check_availability` | Free slots | Who booked the busy ones |
| `book_appointment` | Confirmation code | Other bookings |
| `transfer_to_owner` | "Transferring" | The owner's number (read from server config) |
| `notify_owner` | "Sent" | Contact details |

- `tenant_id` is supplied by the server, never by the model.
- Treat every document as public: anything retrievable can be read aloud to any caller.
- An injection test suite (caller attacks and poisoned documents) runs after every prompt change. [planned]

## Evaluation [planned]

- **Retrieval:** hit rate at k=3 on 30 or more spoken-style questions, a few of them about tables and mid-procedure steps, plus about 10 unanswerable ones. Choose `min_score` from the gap between answerable and unanswerable top scores.
- **Hand-written and generated questions are reported separately.** Generated questions reuse the chunk's wording and inflate the hit rate.
- **Voice:** compare typed, recorded-voice and phone-quality (8 kHz mu-law) versions of the same questions: heard text, top score, right page in top 3. Also time to first audio, interruption handling, cost per call. The file loop already logs `stt_ms`, `answer_ms` and `tts_ms`; `tts_ms` is full synthesis time, not time to first audio.
- **Safety:** injection suite, with a leaked/complied flag per case.
- **Known test cases from the demo manual:** duplicated sections (16.x and 21.x, and 12 and 14), and a contradiction in the source (the Equipment option descriptions are reversed). The right behavior for a contradiction is "I'm not sure".

## Roadmap

| # | Build | Done when |
|---|---|---|
| 1b | Eval set, `min_score` from evidence, spot-check risky pages, `only_verified=True` | A stated hit@3 and threshold |
| 1c | Embedding bake-off at a fixed chunk cap | A table of hit rates |
| 2 | Prompt composer, Tool Gateway, bookings, leads, Telegram notify, injection suite | Injection suite runs with 0 leaks |
| 3 | Voice gate (record 30 questions, spike, pass criteria), then provider adapter, `CallTransport`, barge-in, limits | Pass criteria met; latency measured |
| 4 | Thin portal on FastAPI: profile, document upload with status, test call, call list | A tenant onboards without editing files |
| 5 | Local Asterisk + softphone, extension-to-tenant lookup, then one real number | One recorded forwarded call |
| 6 | Hardening and write-up: optional Postgres/pgvector with row-level security, spend caps, results report | Report with retrieval, latency, cost and injection numbers |

## Known issues

- `min_score` is a guess. Measure it before trusting "I don't know" behavior.
- The voice loop has been tested on synthesized audio only. Real voices, 8 kHz phone audio and Hindi or Punjabi are untested, and a general model used as speech to text can answer a question instead of transcribing it, so check the `Heard:` line.
- `only_verified=False` serves unreviewed transcriptions. A vision model can misread numbers and units, so spot-check pages with measurements and warnings first.
- `voicedesk.log` stores questions, transcripts and replies in plaintext. It is git-ignored; decide a retention rule before any real caller.
- A pasted `.wav` path with surrounding quotes is treated as a text question, and a failed voice turn can end the chat loop.
- The in-memory search cache is cleared only by the process that ran the ingest. A separate server process can serve stale results until restarted. Either stamp cache keys with the manifest's modified time or remove the cache.
- `manual_pages.py` reads PDFs from `manual_sources/` directly, not from a per-tenant subfolder, so every PDF there lands under the tenant name you pass.
- The embedding model is English-focused. Test a multilingual model before serving Hindi or Punjabi callers.
- Near-duplicate chunks from repeated manual sections can crowd out other results in the top 3.

## Telephony notes [planned]

- A phone call reaches your software only if a carrier delivers it. Each tenant needs a number (DID) on a carrier you control, and a business can forward its existing number to it.
- Develop with a browser microphone and a local softphone first. They exercise the same agent logic. Buy one real number only for the final demo, and start the KYC paperwork early.
- Calls arrive as 8 kHz mu-law audio, while models expect 16 or 24 kHz PCM, so the transport needs resampling in both directions and a way to flush audio when the caller interrupts.
- Disclose the AI and any recording at the start of every call. Sending caller audio to a third-party model provider raises consent and data-protection questions (India's DPDP Act); check them before real use.