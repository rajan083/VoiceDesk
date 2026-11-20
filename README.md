# VoiceDesk

An AI phone receptionist for small businesses, built from scratch on hosted Gemini APIs (no voice platform).
It answers questions from a business's own manual (RAG), books appointments, passes messages to the owner,
alerts the owner about bookings and messages, and keeps a log and summary of every call. Each business is a
separate **tenant** with its own phone number, settings, knowledge base and bookings.

> **Status: working terminal prototype.** The knowledge pipeline, the multi-tenant setup, the agent with tools,
> speech in and out (on audio files), owner alerts and call logs work. Customer confirmations, live microphone
> streaming, real telephony, a web chat server and a portal are not built. Each section is marked `[built]`,
> `[partial]` or `[planned]`. Update the markers as the project changes, and don't describe a planned part as
> working.

## Status

| Piece | State | Notes |
|---|---|---|
| Tenants: create, look up by id, number or name (`tenant_config.py`) | [built] | Config per tenant, E.164 numbers, no default tenant |
| Document flow: add, review, verify, ingest, remove (`tenant_docs.py`) | [built] | CLI, resumable transcription |
| PDF to page files (`manual_qa.py`) | [built] | Gemini vision in 4-page slices, cached |
| Clean, chunk, embed, search (`rag_pipeline.py`) | [built] | One Chroma collection per tenant |
| Agent with tools (`agent.py`, `session.py`) | [built] | Gemini chat with function calling |
| Typed and spoken turns share one `Session` | [built] | Same tools, conversation and call log for both |
| Appointments and notes (`bookings.py`) | [built] | Booking tools only when a tenant has a `scheduling` block |
| Address capture | [built] | The agent asks for it unless the tenant sets `ask_address` to false |
| Caller ID number reaches the session | [partial] | `agent.py` reads the number argument but doesn't pass it to `Session` yet |
| Owner alerts for bookings and notes (`notify.py`) | [built] | Channels: outbox file, Telegram. Email and WhatsApp written, untested |
| Owner onboarding by one-time Telegram link | [built] | Part of `tenant_config.py`, runs when a tenant is created |
| Call log and summary (`calllog.py`), report (`report.py`) | [built] | One log per call, summary at hangup |
| Speech in/out (`stt.py`, `tts.py`) | [partial] | Works on `.wav` files, one request at a time, not streaming |
| Review gate (`verified: true`) | [partial] | Implemented, but `agent.py` ingests with `only_verified=False` |
| Customer confirmation messages (SMS or WhatsApp) | [planned] | No message is sent to the customer yet |
| Retrieval tuning and evaluation set | [planned] | `min_score` is a per-tenant setting, not yet measured |
| Live microphone, streaming, barge-in | [planned] | |
| Web chat server and telephony | [planned] | |
| Portal and API | [planned] | |

## How it works

```
OFFLINE: build one tenant's knowledge
 PDF -> tenant_docs.py add -> kb/<tenant>/raw/*.md -> check against the PDF -> verify -> ingest
        (manual_qa.py:        (verified: false)                                          (clean, chunk,
         4-page slices,                                                                   embed, store in
         cache, retry)                                                                    tenant_<id> collection)

ONLINE: one conversation
 caller (typed text or .wav file) + caller number (caller ID)
   -> stt.py (if audio) -> tenant resolved by the server (id / dialed number / business name)
   -> Session (one per call): retrieve chunks for the question -> Gemini chat + tools -> reply
         tools:  save_note                              (always)
                 check_availability, book_appointment   (only if the tenant has a scheduling block)
   -> notify.py: owner alert for a booking or a note (customer confirmation: planned)
   -> tts.py (if audio) -> reply.wav
 on exit -> calls/<tenant>/ log + summary -> report.py
```

## Project structure

```
VoiceDesk/
  agent.py              entry point: resolves the tenant, indexes new pages, chat loop, .wav input
  session.py            one Session per call: prompt, tools, retrieval per turn, call log, summary at close
  bookings.py           availability, booking and notes storage used by the tools
  notify.py             owner alerts for bookings and notes (outbox, Telegram, email, WhatsApp)
  calllog.py            per-call log and summary
  report.py             builds a report from the call summaries
  rag_pipeline.py       chunking, embedding, ChromaDB ingest and search (one collection per tenant)
  manual_qa.py          PDF to page files with Gemini vision (resumable, cached)
  tenant_config.py      create tenants, load config, connect an owner's Telegram, number / name lookup
  tenant_docs.py        per-tenant document commands: add, status, verify, ingest, remove
  stt.py                speech-to-text for a .wav file
  tts.py                text-to-speech to a .wav file
  requirements.txt
  tenants/<id>/         config.json per tenant               (data, not committed)
  manual_sources/<id>/  source PDFs                          (data, not committed)
  kb/<id>/raw/          transcribed pages, one .md per page  (data, not committed)
  vector_store/         ChromaDB data and ingest manifests   (data, not committed)
  calls/<id>/           call logs and summaries              (data, not committed)
  outbox.jsonl          messages "sent" through the outbox channel (created where you run; see Known issues)
  voicedesk.log         application log (not committed)
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
TELEGRAM_BOT_TOKEN=your-bot-token          # owner alerts, see "Notifications"
# Optional channels:
# SMTP_USER=you@gmail.com
# SMTP_APP_PASSWORD=...
# WA_TOKEN=...
# WA_PHONE_NUMBER_ID=...
# WA_GRAPH_VERSION=v26.0                   # optional
```

`.env`, the log files, `outbox.jsonl` and the data folders must stay out of git. Never commit an API key or bot
token. If one leaks, revoke it and create a new one. The first run downloads the embedding model.

## Usage

Anywhere a command takes `<tenant>`, you can give the tenant ID, an assigned phone number or the exact
business name. Quote values that contain spaces or a `+`.

**1. Create a tenant**

```powershell
python tenant_config.py new "Plumbing Agency" --number +14155550100
python tenant_config.py list
python tenant_config.py check
```

IDs are generated (`t_` plus 12 hex characters). Use `--id demo` only for a fixed name. After the tenant is
created, the command prints a one-time Telegram link and waits up to 10 minutes for the owner to tap it, which
saves the owner's chat ID in the tenant's config. Press Ctrl+C to skip, or use `--no-telegram`. If
`TELEGRAM_BOT_TOKEN` is not set, this step is skipped.
Connect an owner later with `python tenant_config.py link <tenant>`.

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
python agent.py "Plumbing Agency" +919999900000
```

The optional second argument is meant to simulate caller ID. Today it is checked but not passed to the session
(see Known issues), so the agent always asks for a number. The agent plays the tenant's greeting, indexes any
new pages, then answers until you type `exit` or `quit`. On exit it prints the call summary.

**4. Try the voice path**

```powershell
python tts.py                                    # type a question, creates speech.wav
python agent.py "Plumbing Agency" +919999900000
You: speech.wav                                  # type the file name without quotes
```

The agent prints what it heard and its reply, and plays `reply.wav`. The spoken turn goes through the same
`Session` as typed text, so booking, notes and the call log work the same way. Timings for each step go to
`voicedesk.log`.

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

A tenant that takes bookings and sends notifications also has:

```json
{
  "scheduling": {
    "timezone": "Asia/Kolkata",
    "slot_minutes": 30,
    "min_notice_minutes": 60,
    "hours": {"mon": ["09:00", "18:00"], "sat": ["10:00", "14:00"]},
    "ask_address": true
  },
  "notify": {
    "owner_channel": "telegram",
    "owner_to": "123456789",
    "customer_channel": "outbox",
    "confirm_customer": true
  }
}
```

- Phone numbers must be in +E.164 form, and one number can belong to only one tenant.
- A `scheduling` block switches on `check_availability` and `book_appointment`. A day missing from `hours` is closed.
  Set `"ask_address": false` for a business that doesn't need an address (a clinic, for example).
- `notify.owner_to` is set automatically when the owner taps the Telegram link, and `owner_channel` says how the
  alert is sent (`outbox`, `telegram`, `email` or `whatsapp`). With no `owner_to`, no alert is sent.
- `customer_channel` and `confirm_customer` are saved by the link command but nothing reads them yet.
  `owner.notify_telegram_chat_id` and `owner.transfer_number` are reserved and unused too.
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
  Search tries `concept` first and falls back to `raw`, drops results below `min_score`, and logs each hit with
  its source and score so `min_score` can be tuned.

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
- **Booking flow:** ask for the caller's name, what they need help with, the address (unless the tenant turns it
  off) and the preferred day, check availability, offer two or three times, read back the date, time and name, wait
  for a yes, then book and read out the confirmation code. The prompt forbids inventing times, codes or availability.
- **Caller number:** when the session is given a caller number, the agent never asks for it. Otherwise it asks
  for one, and the owner's alert marks that number "not verified" (a caller-ID number is marked "caller ID").
- **Notes:** `save_note` passes a message or callback request to the owner, and the agent only says it was passed
  on after the tool returns ok.
- **Confirmations:** the agent says a confirmation was sent only if the booking result reports it. No customer
  message is sent yet, so the result never reports it and the agent never claims one.
- **Spoken replies:** on a spoken turn, replies are one or two short plain sentences with no lists or markdown.
- **Tenant and caller phone are set by the server.** The model never sees or sets `tenant_id` or the caller ID
  number, and an unknown dialed number never falls back to a default tenant.

## Notifications

`notify.py` builds every message in code from the tool result. The model never chooses the recipient or the text,
so a caller can't talk the agent into messaging a stranger. A failed send is logged and never breaks the call.

| Channel | State | Notes |
|---|---|---|
| `outbox` | [built] | Writes each message to `outbox.jsonl`. Nothing is delivered, which makes it free and good for demos |
| `telegram` | [built] | Free. The recipient must start the bot once. Used for owner alerts |
| `email` | [partial] | Gmail SMTP with an app password. Written, untested |
| `whatsapp` | [partial] | Meta Cloud API. Written, untested. Free-form text works only inside the 24-hour window, and production needs approved templates |

- **Booking:** the owner gets the name, time, service, address, phone (marked "caller ID" or "not verified") and code.
- **Customer confirmation** [planned]: `booking_alerts` only messages the owner. The customer branch, and the
  `customer` result that `session.py` looks for, don't exist yet.
- **Notes:** the owner gets the caller's message.
- **Connecting an owner:** when a tenant is created, a one-time Telegram link is printed. When the owner taps it,
  the bot receives the code with their chat ID, and the ID is saved in `notify.owner_to`. The code is random and
  works once, so send the link privately. If the wrong person used it, run `link` again to replace the owner.

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

- `agent.py` ingests with `only_verified=False`, so unreviewed transcriptions are served. A vision model can
  misread numbers and units, so check prices, hours and warnings against the PDF before you verify a page.
- `agent.py` builds `Session(TENANT_ID, client, MODEL)` without `caller_phone`, so the caller number argument is
  ignored. Until it is passed, the agent always asks for a number, and every booking uses the number the caller
  typed, which the owner's alert marks as unverified.
- No confirmation is sent to the customer. `notify.py` only messages the owner, so `confirmation_sent` is always false.
- `.gitignore` does not list `outbox.jsonl`, which holds names, phone numbers and addresses. Add it before the next commit.
- `voice_answer()` uses the global `session` created in the script's main block, and its first parameter, named
  `tenant_id`, actually receives the session. It works when `agent.py` is run directly and breaks if imported.
  `answer()` in `agent.py` is no longer called, so `llm()` is unused too.
- Notifications are sent inside the tool call, which adds a fraction of a second to that turn, and there is no
  retry or queue.
- Only the number from caller ID is trusted. A typed-in number is shown to the owner as unverified, and web chat
  has no caller ID at all.
- The address is saved inside the booking's notes, and the `notes` field the model can pass is not included in the
  owner's alert.
- A tenant has one owner chat. The Telegram link is first-tap-wins, and the command waits by polling the bot, so
  don't run two programs that read the same bot at once.
- The booking and note alerts contain callers' names, numbers and addresses. Keep `calls/`, `outbox.jsonl` and the
  logs out of git and decide how long to keep them.
- `retrieval.min_score` is a per-tenant setting that has not been measured.
- The in-memory search cache is cleared only by the process that ran the ingest, so a separate long-running
  process can serve stale results until it restarts.
- `tts.py` raises a `NameError` if the response has no usage data, because `usage` is only set inside an `if`.
- The embedding model is English-focused. Test a multilingual model before serving other languages.
- Near-duplicate chunks from repeated manual sections can crowd out other results.

## Roadmap

- [x] Send the voice path through `Session` (tools, call log, summary)
- [x] Owner alerts for bookings and notes (outbox, Telegram)
- [x] Address capture
- [ ] Pass the caller number from `agent.py` into `Session` so caller ID works in the terminal
- [ ] Customer confirmation message for bookings, using the saved `customer_channel`
- [ ] Evaluation set per tenant, then choose `min_score` from the results
- [ ] Review pages with numbers and warnings, then switch to `only_verified=True`
- [ ] Injection test suite: caller attacks and poisoned documents, run after every prompt change
- [ ] FastAPI server: web chat endpoint, session store with idle timeout, site key per tenant
- [ ] Real customer delivery (SMS or approved WhatsApp templates) behind the same `send()` function
- [ ] Live microphone and streaming speech, then barge-in and call limits
- [ ] Portal: profile, document upload with status, test call, call list, Telegram link for owners
- [ ] Telephony: local softphone first, then one real number

## Telephony and privacy notes

- A phone call reaches your software only if a carrier delivers it. Each tenant needs a number on a carrier
  you control, and a business can forward its existing number to it.
- Develop with a browser or laptop microphone first, and buy a real number only for the final demo.
- Tell callers they are speaking to an AI, and tell them if the call is recorded or logged. Call logs hold what
  callers say, so keep `calls/` out of git and decide how long to keep it. Sending caller audio to a model
  provider raises consent and data-protection questions (for example India's DPDP Act); check them before
  real use.