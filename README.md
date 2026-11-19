# VoiceDesk

An AI phone receptionist for small businesses, built from scratch on hosted Gemini APIs (no voice platform).
It answers questions from a business's own manual (RAG), books appointments, passes messages to the owner
and keeps a log and summary of every call. Each business is a separate **tenant** with its own phone number,
settings, knowledge base and bookings.

> **Status: working terminal prototype.** The knowledge pipeline, the multi-tenant setup, the text agent with
> tools, call logs and file-based speech (speech-to-text and text-to-speech) work. Live microphone streaming,
> telephony and a portal are not built. Each section is marked `[built]`, `[partial]` or `[planned]`.
> Update the markers as the project changes, and don't describe a planned part as working.

## Status

| Piece | State | Notes |
|---|---|---|
| Tenants: create, look up by number or name (`tenant_config.py`) | [built] | Config per tenant, E.164 numbers, no default tenant |
| Document flow: add, review, verify, ingest, remove (`tenant_docs.py`) | [built] | CLI, resumable transcription |
| PDF to page files (`manual_qa.py`) | [built] | Gemini vision in 4-page slices, cached |
| Clean, chunk, embed, search (`rag_pipeline.py`) | [built] | One Chroma collection per tenant |
| Text agent with tools (`agent.py`, `session.py`) | [built] | Gemini chat with function calling |
| Appointments and notes (`bookings.py`) | [built] | Booking tools are exposed only when a tenant has a `scheduling` block |
| Call log and summary (`calllog.py`) | [built] | One log per call, summary written at hangup |
| Call report (`report.py`) | [built] | Reads the call summaries |
| Speech in/out (`stt.py`, `tts.py`) | [partial] | Works on `.wav` files, one request at a time, not streaming |
| Voice path with tools and call log | [planned] | The `.wav` path in `agent.py` currently skips `Session` |
| Retrieval tuning and evaluation set | [planned] | `min_score` is still a default, not measured |
| Review gate (`verified: true`) | [partial] | Implemented, but `agent.py` ingests with `only_verified=False` |
| Owner alerts and call transfer | [planned] | Config fields exist, no sender yet |
| Live microphone, streaming, barge-in | [planned] | |
| Telephony (softphone, then a real number) | [planned] | |
| Portal and API | [planned] | |

## How it works

```
OFFLINE: build one tenant's knowledge
 PDF -> tenant_docs.py add -> kb/<tenant>/raw/*.md -> check against the PDF -> verify -> ingest
        (manual_qa.py:        (verified: false)                                          (clean, chunk,
         4-page slices,                                                                   embed, store in
         cache, retry)                                                                    tenant_<id> collection)

ONLINE: one conversation
 caller (typed text or .wav file)
   -> stt.py (if audio) -> tenant resolved by the server (id / dialed number / business name)
   -> Session (one per call): retrieve chunks for the question -> Gemini chat + tools -> reply
         tools:  save_note                              (always)
                 check_availability, book_appointment   (only if the tenant has a scheduling block)
   -> tts.py (if audio) -> reply.wav
 on exit -> calls/<tenant>/ log + summary -> report.py
```

## Project structure

```
VoiceDesk/
  agent.py              entry point: resolves the tenant, indexes new pages, chat loop, .wav input
  session.py            one Session per call: prompt, tools, retrieval per turn, call log, summary at close
  bookings.py           availability, booking and notes storage used by the tools
  calllog.py            per-call log and summary
  report.py             builds a report from the call summaries
  rag_pipeline.py       chunking, embedding, ChromaDB ingest and search (one collection per tenant)
  manual_qa.py          PDF to page files with Gemini vision (resumable, cached)
  tenant_config.py      create tenants, load config, dialed number / name to tenant
  tenant_docs.py        per-tenant document commands: add, status, verify, ingest, remove
  stt.py                speech-to-text for a .wav file
  tts.py                text-to-speech to a .wav file
  requirements.txt
  tenants/<id>/         config.json per tenant               (data, not committed)
  manual_sources/<id>/  source PDFs                          (data, not committed)
  kb/<id>/raw/          transcribed pages, one .md per page  (data, not committed)
  vector_store/         ChromaDB data and ingest manifests   (data, not committed)
  calls/<id>/           call logs and summaries              (data, not committed)
```

The data folders are in the repo only as empty placeholders (`.gitkeep`).

## Setup (Windows PowerShell)

```powershell
python -m venv .venv
.\.venv\Scripts\Activate.ps1
pip install -r requirements.txt
```

Create a `.env` file:

```
GEMINI_API_KEY=your-key-here
```

`.env`, the log files and the data folders must stay out of git. Never commit the API key.
The first run downloads the embedding model.

## Usage

Anywhere a command takes `<tenant>`, you can give the tenant ID, an assigned phone number or the exact
business name. Quote values that contain spaces or a `+`.

**1. Create a tenant**

```powershell
python tenant_config.py new "Plumbing Agency" --number +14155550100
python tenant_config.py list
python tenant_config.py check
```

IDs are generated (`t_` plus 12 hex characters). Use `--id demo` only for a fixed name.

**2. Add and review a document**

```powershell
python tenant_docs.py add <tenant> guide.pdf
python tenant_docs.py status <tenant>
python tenant_docs.py verify <tenant> <doc> --pages 1-20,25      # or --all
python tenant_docs.py ingest <tenant>
```

`<doc>` is the name shown by `status` (the PDF name in lowercase with underscores). Only verified pages are
indexed. `ingest --include-unverified` is for development only. To remove a document, run
`python tenant_docs.py remove <tenant> <doc> --yes` and then `ingest` again.

**3. Talk to the agent**

```powershell
python agent.py "Plumbing Agency"
```

The agent plays the tenant's greeting, indexes any new pages, then answers until you type `exit` or `quit`.
On exit it prints the call summary.

**4. Try the voice path**

```powershell
python tts.py                       # type a question, creates speech.wav
python agent.py "Plumbing Agency"
You: speech.wav                     # type the file name without quotes
```

The agent prints what it heard and its reply, and plays `reply.wav`. Timings for each step (speech-to-text,
answer, text-to-speech) go to `voicedesk.log`.

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

- Phone numbers must be in +E.164 form, and one number can belong to only one tenant.
- Add a `scheduling` block (it includes the tenant's `timezone`; see `bookings.py` for the other fields) to
  switch on `check_availability` and `book_appointment` for that tenant.
- Tune `top_k` and `min_score` per tenant against its own test questions.

## Knowledge pipeline

- **Transcription** [built]: 4-page PDF slices go to a Gemini vision model. Refusal-like replies are retried
  once, then left for the next run. Results are cached by file hash, prompt version and model.
- **Cleaning** [built]: dot leaders, empty table cells and very long unbroken runs are removed, and pages that
  look like a table of contents are skipped.
- **Chunking** [built]: a short page stays whole. Longer pages are split into sentences, cut at semantic
  breakpoints and capped by the embedding model's own tokenizer. Tiny fragments are merged back.
- **Storage** [built]: title-prefixed text, cosine distance, one Chroma collection per tenant, and a manifest per
  tenant so unchanged files are skipped on the next ingest.
- **Search** [built]: chunks are tiered as `raw` (transcribed pages) or `concept` (reviewed summary files).
  Search tries `concept` first and falls back to `raw`, and drops results below `min_score`.

| Setting | Where | What it does |
|---|---|---|
| `INGEST_VERSION` | `rag_pipeline.py` | Bump after any chunking or cleaning change, and everything re-ingests |
| `MAX_CHUNK_TOKENS` | `rag_pipeline.py` | Chunk cap; keep it under the embedder's limit minus the title prefix |
| `BREAKPOINT_PERCENTILE` | `rag_pipeline.py` | Lower means more, smaller chunks |
| `retrieval.top_k`, `retrieval.min_score` | tenant `config.json` | How many chunks, and the relevance cutoff |
| `only_verified` | `ingest_tenant()` | Index only reviewed files |
| `PROMPT_VERSION`, `VISION_MODEL` | `manual_qa.py` | Changing either re-transcribes |

## Agent behavior

- **Facts come only from the retrieved context.** The context sits in the user turn inside a labeled block and is
  treated as reference text. The prompt tells the model to ignore any instructions that appear inside it, and
  any caller request to change the rules.
- **Unknown answers:** if the context lacks the answer, the agent says it doesn't know and offers to take a message.
- **Booking flow:** ask for the caller's name and preferred day, check availability, offer two or three times,
  read back the date, time and name, wait for a yes, then book and read out the confirmation code. The prompt
  forbids inventing times, codes or availability.
- **Notes:** `save_note` passes a message or callback request to the owner, and the agent only says it was passed
  on after the tool returns ok.
- **Spoken replies:** in voice mode, replies are one or two short plain sentences with no lists or markdown.
- **Tenant and caller phone are set by the server.** The model never sees or sets `tenant_id` or the caller's
  number, and an unknown dialed number never falls back to a default tenant.

## Models

| Use | Model | File |
|---|---|---|
| Answers, chat and call summary | `gemini-3.1-flash-lite` | `agent.py` |
| Page transcription | `gemini-3.1-flash-lite` | `manual_qa.py` |
| Speech to text | `gemini-3.1-flash-lite` (v1beta) | `stt.py` |
| Text to speech | `gemini-3.8-flash-lite-tts`, voice Kore, 24 kHz mono WAV | `tts.py` |
| Embeddings | `all-MiniLM-L6-v2` (sentence-transformers), loaded once per process | `rag_pipeline.py` |

Model names change often, so check them against your account's current model list.

## Known issues

- The `.wav` path in `agent.py` calls `answer()` directly, so it has no tools, no booking, no notes and no call
  log. Only typed input goes through `Session`.
- `agent.py` builds `Session` without a caller phone number or voice mode, so bookings made in the terminal
  have no phone number.
- `agent.py` ingests with `only_verified=False`, so unreviewed transcriptions are served. A vision model can
  misread numbers and units, so check prices, hours and warnings against the PDF before you verify a page.
- `retrieval.min_score` (0.20) is a default, not a measured value.
- The in-memory search cache is cleared only by the process that ran the ingest, so a separate long-running
  process can serve stale results until it restarts.
- `tts.py` raises a `NameError` if the response has no usage data, because `usage` is only set inside an `if`.
- The embedding model is English-focused. Test a multilingual model before serving other languages.
- Near-duplicate chunks from repeated manual sections can crowd out other results.

## Roadmap

- [ ] Evaluation set per tenant, then choose `min_score` from the results
- [ ] Review pages with numbers and warnings, then switch to `only_verified=True`
- [ ] Send the voice path through `Session` (tools, call log, summary)
- [ ] Owner alerts (Telegram) and call transfer from the `owner` config
- [ ] Injection test suite: caller attacks and poisoned documents, run after every prompt change
- [ ] Live microphone and streaming speech, then barge-in and call limits
- [ ] FastAPI portal: profile, document upload with status, test call, call list
- [ ] Telephony: local softphone first, then one real number

## Telephony and privacy notes

- A phone call reaches your software only if a carrier delivers it. Each tenant needs a number on a carrier
  you control, and a business can forward its existing number to it.
- Develop with a browser or laptop microphone first, and buy a real number only for the final demo.
- Tell callers they are speaking to an AI, and tell them if the call is recorded or logged. Call logs hold what
  callers say, so keep `calls/` out of git and decide how long to keep it. Sending caller audio to a model
  provider raises consent and data-protection questions (for example India's DPDP Act); check them before
  real use.