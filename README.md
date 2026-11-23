# VoiceDesk

An AI phone receptionist for small businesses, built from scratch on hosted Gemini APIs (no voice platform).
It answers questions from a business's own manual (RAG), books appointments, takes messages and leads, asks the
owner to call a caller back, alerts the owner on Telegram, and keeps a log and summary of every call. Each
business is a separate **tenant** with its own phone number, settings, knowledge base and bookings.

> **Status: working terminal prototype.** The knowledge pipeline, the multi-tenant setup, the agent with its
> tools, speech in and out (on audio files), owner alerts and call logs are in place. A real customer message,
> a real call transfer, live microphone streaming, telephony, a web chat server and a portal are not built.
> Each section is marked `[built]`, `[partial]` or `[planned]`. Update the markers as the project changes, and
> don't describe a planned part as working.
> Checked against commit `0b3b20a` (2026-11-23). The repo has no automated tests.

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
| Address capture | [built] | Asked unless the tenant sets `scheduling.ask_address` to false |
| Callback number required before saving | [built] | `require_phone` (default on) makes the tools refuse with `phone_required` |
| Caller number from the command line | [built] | Second argument to `agent.py` simulates caller ID; real caller ID needs telephony |
| Lead capture (`save_lead`) | [built] | Saved as a note starting `LEAD:`, with an owner alert |
| Transfer to owner (`transfer_to_owner`) | [partial] | Sends the owner a "call this caller" alert. No call is transferred (see Known issues) |
| Owner alerts (`notify.py`) | [built] | Channels: `outbox` (a local file) and `telegram` |
| Owner onboarding by one-time Telegram link | [built] | Part of `tenant_config.py`, runs when a tenant is created |
| Model fallback | [built] | `FALLBACK_MODELS` for the chat and the call summary and speech to text|
| Call log and summary (`calllog.py`), report (`report.py`) | [built] | One log per call, summary at exit. `report.py` needs a `scheduling` block |
| Speech in/out (`stt.py`, `tts.py`) | [partial] | Works on `.wav` files, one request at a time, not streaming.
| Customer confirmation message | [partial] | Code exists, but it only writes to `outbox.jsonl`. Nothing reaches the customer |
| Review gate (`verified: true`) | [partial] | Implemented, but `agent.py` ingests with `only_verified=False` |
| Retrieval tuning and evaluation set | [planned] | `min_score` is a per-tenant setting, not yet measured |
| Automated tests, injection test suite | [planned] | |
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

ONLINE: one conversation (one run of agent.py)
 typed text, or a .wav file   +   optional caller number (simulated caller ID)
   -> stt.py (if audio) -> tenant resolved by the server (id / dialed number / business name)
   -> Session: retrieve chunks for the question -> Gemini chat + tools -> reply
         tools:  save_note, save_lead, transfer_to_owner      (always)
                 check_availability, book_appointment         (only if the tenant has a scheduling block)
   -> notify.py: Telegram or outbox alert to the owner (booking, note, lead, call-me request)
                 confirmation to the customer: written to outbox.jsonl only
   -> tts.py (if audio) -> reply.wav
 on exit -> calls/<tenant>/ log + summary -> report.py
```

## Project structure

```
VoiceDesk/
  agent.py              entry point: resolves the tenant, indexes pages, chat loop, .wav input
  session.py            one Session per call: prompt, tools, retrieval, model fallback, call log, summary
  bookings.py           availability, bookings and notes in SQLite (voicedesk.db)
  notify.py             alerts for bookings, notes, leads and call-me requests (outbox, Telegram)
  calllog.py            per-call JSONL log and the call summary
  report.py             bookings, notes and call summaries for one tenant
  rag_pipeline.py       chunking, embedding, ChromaDB ingest and search (one collection per tenant)
  manual_qa.py          PDF to page files with Gemini vision (resumable, cached)
  tenant_config.py      create tenants, load config, connect an owner's Telegram, number / name lookup
  tenant_docs.py        per-tenant document commands: add, status, verify, ingest, remove
  stt.py                speech-to-text for a .wav file
  tts.py                text-to-speech to a .wav file
  requirements.txt      full pip freeze, with exact versions
  tenants/<id>/         config.json per tenant               (data, not committed)
  manual_sources/<id>/  source PDFs                          (data, not committed)
  kb/<id>/raw/          transcribed pages, one .md per page  (data, not committed)
  vector_store/         ChromaDB data and ingest manifests   (data, not committed)
  calls/<id>/           call logs and summaries              (data, not committed)
  models/               local copy of the embedding model    (not committed)
  voicedesk.db          bookings and notes, created in the folder you run from (not committed)
  voicedesk.log         application log (not committed)
  outbox.jsonl          messages "sent" through the outbox channel (created where you run; see Known issues)
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
FALLBACK_MODELS=model-b,model-c            # optional: models to try, in order, if the main one is overloaded
```

`.env`, the log files, `outbox.jsonl` and the data folders must stay out of git. Never commit an API key or bot
token. If one leaks, revoke it and create a new one. The first run downloads the embedding model and saves a
copy in `models/`. Run the commands from the project folder, because `voicedesk.db`, `calls/` and `outbox.jsonl`
are created relative to where you run them.

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
`TELEGRAM_BOT_TOKEN` is not set, this step is skipped. Connect an owner later with
`python tenant_config.py link <tenant>`.

The command has no options for hours or alerts. To take bookings, add the `scheduling` block described under
"Tenant configuration" to `tenants/<id>/config.json` by hand.

**2. Add and review a document**

```powershell
python tenant_docs.py add <tenant> guide.pdf
python tenant_docs.py status <tenant>
python tenant_docs.py verify <tenant> <doc> --pages 1-20,25      # or --all
python tenant_docs.py ingest <tenant>
```

`<doc>` is the name shown by `status` (the PDF name in lowercase with underscores). Only verified pages are
indexed by `ingest`. `ingest --include-unverified` is for development only. To remove a document, run
`python tenant_docs.py remove <tenant> <doc> --yes` and then `ingest` again. Use `tenant_docs.py add` and not
`python manual_qa.py`, which reads PDFs from a flat `manual_sources/` folder (see Known issues).

**3. Talk to the agent**

```powershell
python agent.py "Plumbing Agency"
python agent.py "Plumbing Agency" +919999900000      # optional caller number, in +E.164 form
```

The agent prints the tenant's greeting, indexes any new pages (`agent.py` indexes unverified pages too), then
answers until you type `exit` or `quit`. On exit it prints the call summary. A run of `agent.py` is one call: one
`Session`, one call log. With a caller number, the agent never asks for a number and the owner's alert marks it
"caller ID". Without one, the agent asks for a number before it books or saves anything.

**4. Try the voice path**

```powershell
python tts.py                                    # type a question, creates speech.wav
python agent.py "Plumbing Agency" +919999900000
You: speech.wav                                  # type the file name without quotes
```

The agent prints what it heard and its reply, and plays `reply.wav` (Windows only, through `os.startfile`). The
spoken turn goes through the same `Session` as typed text, so booking, notes and the call log work the same way.
Timings for each step go to `voicedesk.log`.

**5. See what was saved**

```powershell
python report.py <tenant>                  # upcoming bookings, notes and leads, recent call summaries
python report.py <tenant> --call <id>      # one call's transcript, with its tool calls
```

`report.py` only works for a tenant that has a `scheduling` block. To summarize an old call by hand, run
`python calllog.py calls/<tenant>/<id>.jsonl`.

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

A tenant that takes bookings and sends alerts also has:

```json
{
  "require_phone": true,
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
- `require_phone` is read with a default of `true`. When it is on, `book_appointment`, `save_note`, `save_lead`
  and a call-me request refuse to run without a number, and `notify.py` refuses to send a booking alert without
  one. A number given by the caller must have 7 to 15 digits.
- A `scheduling` block switches on `check_availability` and `book_appointment`. A day missing from `hours` is
  closed. Set `"ask_address": false` for a business that doesn't need an address (a clinic, for example).
- `notify.owner_to` is set automatically when the owner taps the Telegram link, along with `owner_channel`
  (`telegram`), `customer_channel` (`outbox`) and `confirm_customer` (`true`). `owner_channel` and
  `customer_channel` can be `outbox` or `telegram`. With no `owner_to`, no owner alert is sent.
- `owner.transfer_number` is validated as a phone number and read by `transfer_to_owner`, but no call can be
  transferred yet. `owner.notify_telegram_chat_id` is not used anywhere.
- Tune `top_k` and `min_score` per tenant against its own test questions.

## Knowledge pipeline

- **Transcription** [built]: 4-page PDF slices go to a Gemini vision model. Refusal-like replies are retried
  once, then left for the next run. Results are cached by file hash, prompt version and model.
- **Cleaning** [built]: dot leaders, empty table cells and very long unbroken runs are removed, and pages that
  look like a table of contents are skipped.
- **Chunking** [built]: a page of up to 180 words stays whole (the check counts words, see Known issues). Longer
  pages are split into sentences, cut at semantic breakpoints and capped by the embedding model's own tokenizer.
  Tiny fragments are merged back.
- **Storage** [built]: title-prefixed text, cosine distance, one Chroma collection per tenant, and a manifest per
  tenant so unchanged files are skipped on the next ingest.
- **Search** [built]: chunks are tiered as `raw` (transcribed pages) or `concept` (reviewed summary files).
  Search tries `concept` first and falls back to `raw`, drops results below `min_score`, and `session.py` logs
  each hit with its source and score so `min_score` can be tuned.

| Setting | Where | What it does |
|---|---|---|
| `INGEST_VERSION` | `rag_pipeline.py` | Bump after any chunking or cleaning change, and everything re-ingests |
| `MAX_CHUNK_TOKENS` | `rag_pipeline.py` | Chunk cap; keep it under the embedder's limit minus the title prefix |
| `BREAKPOINT_PERCENTILE` | `rag_pipeline.py` | Lower means more, smaller chunks |
| `retrieval.top_k`, `retrieval.min_score` | tenant `config.json` | How many chunks, and the relevance cutoff |
| `only_verified` | `ingest_tenant()` | Index only reviewed files |
| `PROMPT_VERSION`, `VISION_MODEL` | `manual_qa.py` | Changing either re-transcribes |
| `FALLBACK_MODELS` | `.env` | Models tried after the main one fails (chat, call summary and speech to text) |

## Agent behavior

- **Facts come only from the retrieved context.** The context sits in the user turn inside a labeled block and is
  treated as reference text. The prompt tells the model to ignore any instructions that appear inside it, and
  any caller request to change the rules.
- **Unknown answers:** if the context lacks the answer, the agent says it doesn't know and offers to take a message.
- **Booking flow:** ask for the caller's name, what they need help with (saved as the service), the address
  (unless the tenant turns it off) and the preferred day, check availability, offer two or three times, read back
  the date, time and name, wait for a yes, then book and read out the confirmation code. The prompt forbids
  inventing times, codes or availability. The server checks the slot again, and a unique index in SQLite stops two
  bookings for the same slot.
- **Messages and leads:** `save_note` passes a message to the owner. `save_lead` is for a caller who wants a quote,
  a callback or a visit but isn't booking: it saves their name, need, address and best time. The agent says a
  message was passed on only after the tool returns ok. Notes and leads share a limit of 10 per call.
- **Speaking to the owner:** `transfer_to_owner` is for a caller who asks for a person or has an urgent matter.
  It reports `transferring` (only possible once a call can be connected), `callback_requested` (the owner got an
  alert) or `not_available`. It can succeed once per call.
- **Caller number:** when the session is given a caller number, the agent never asks for it. Otherwise it asks
  for one. The owner's alert marks a caller-ID number "caller ID" and a spoken one "given by caller, not verified".
- **Confirmations:** the agent says a confirmation was sent only if the booking result has `confirmation_sent`
  true. Today that is true whenever the message was written to `outbox.jsonl` (see Known issues, item 2).
- **Spoken replies:** on a spoken turn, replies are one or two short plain sentences with no lists or markdown.
- **Tenant and caller phone are set by the server.** The model never sees or sets `tenant_id` or the caller ID
  number, and an unknown dialed number never falls back to a default tenant.
- **Model fallback:** on an overload, a rate limit, a server error or an unknown model (HTTP 404, 429, 500, 502,
  503, 504), the session switches to the next model in `FALLBACK_MODELS` for the rest of the call and replays the
  conversation so far. If every model fails, `agent.py` prints a short apology and the call continues.

## Notifications

`notify.py` builds every message in code from the tool result. The model never chooses the recipient or the text,
so a caller can't talk the agent into messaging a stranger. A failed send is logged and never breaks the call.

| Channel | State | Notes |
|---|---|---|
| `outbox` | [built] | Writes each message to `outbox.jsonl`. Nothing is delivered, which makes it free and good for demos |
| `telegram` | [built] | Free. The recipient must start the bot once. Used for owner alerts |

Email and WhatsApp were removed in commit `e175127`.

- **Booking:** the owner gets the name, time, service, address, phone and code.
- **Note, lead and call-me request:** the owner gets the caller's message, the lead's details, or the reason the
  caller wants to speak to them, each with the caller's phone and how it was obtained.
- **Customer confirmation** [partial]: sent only when the session has a caller ID number and the tenant's
  `notify.confirm_customer` is true. It goes through `customer_channel`, which is `outbox` by default, so it is
  written to `outbox.jsonl` and nothing reaches the customer.
- **Connecting an owner:** when a tenant is created, a one-time Telegram link is printed. When the owner taps it,
  the bot receives the code with their chat ID, and the ID is saved in `notify.owner_to`. The code is random and
  works once, so send the link privately. If the wrong person used it, run `link` again to replace the owner.

## Models

| Use | Model | File |
|---|---|---|
| Answers, chat and call summary | `gemini-3.1-flash-lite`, then `FALLBACK_MODELS` | `agent.py` (`MODEL`), `session.py` |
| Page transcription | `gemini-3.1-flash-lite`, no fallback | `manual_qa.py` |
| Speech to text | `gemini-3.1-flash-lite` built from `FALLBACK_MODELS`; the first entry is wrong (Known issues, item 1) | `stt.py` |
| Text to speech | `gemini-3.8-flash-lite-tts`, voice Kore, 24 kHz mono WAV | `tts.py` |
| Embeddings | `all-MiniLM-L6-v2` (sentence-transformers), loaded once per process from `models/` | `rag_pipeline.py` |

Model names change often, so check them against your account's current model list.

## Known issues

1. **`confirmation_sent` can be true when nothing was delivered.** A confirmation written to `outbox.jsonl`
   counts as sent, so the agent can tell a caller that a confirmation message was sent.
2. **`report.py` fails with a `KeyError` for a tenant without a `scheduling` block.**
3. **No call is transferred.** `agent.py` never passes the `transfer` argument to `Session`, so
   `transferring` can't happen. With owner alerts configured the result is `callback_requested`, and for a tenant
   without them it is `not_available`.
4. **`chunk_body` is defined twice in `rag_pipeline.py`.** The second definition wins and compares the number of
   words, not tokens, against `MAX_CHUNK_TOKENS`, so a page of up to 180 words stays whole even if it is longer
   than the embedding model's limit. `toc_files` in the ingest result is always empty.
5. `agent.py` ingests with `only_verified=False`, so unreviewed transcriptions are served. A vision model can
   misread numbers and units, so check prices, hours and warnings against the PDF before you verify a page.
6. `.gitignore` does not list `outbox.jsonl`, which holds names, phone numbers and addresses. Add it before the
   next commit.
7. A lead is stored as a note starting `LEAD:`, and the address is saved inside the booking's notes. The `notes`
   field the model can pass is not included in the owner's alert.
8. Alerts are sent inside the tool call, which adds a fraction of a second to that turn, and there is no retry or
   queue. A typed-in number is shown to the owner as unverified, and web chat has no caller ID at all.
9. A tenant has one owner chat. The Telegram link is first-tap-wins, and the command waits by polling the bot,
    so don't run two programs that read the same bot at once.
10. Call logs, `voicedesk.db`, alerts and `voicedesk.log` contain callers' names, numbers, addresses and
    speech. Keep them out of git and decide how long to keep them.
11. `python manual_qa.py <tenant>` reads every PDF in `manual_sources/` directly, not from the tenant's
    subfolder. Use `tenant_docs.py add`.
12. `agent.py` still contains `answer()` and `llm()`, which nothing calls. `notify.py` still imports `smtplib`
    and `EmailMessage`. Playback uses `os.startfile`, so it works on Windows only.
13. `tts.py` raises a `NameError` if the response has no usage data, because `usage` is only set inside an `if`.
14. `retrieval.min_score` is a per-tenant setting that has not been measured. The in-memory search cache is
    cleared only by the process that ran the ingest, so a separate long-running process can serve stale results
    until it restarts.
15. The model fallback and the Telegram flow have not been exercised by any automated test. The embedding model
    is English-focused, so test a multilingual model before serving other languages. Near-duplicate chunks from
    repeated manual sections can crowd out other results.

## Roadmap

- [x] Send the voice path through `Session` (tools, call log, summary)
- [x] Owner alerts for bookings, notes, leads and call-me requests (outbox, Telegram)
- [x] Address capture, lead capture, required callback number
- [x] Caller number argument passed from `agent.py` into `Session`
- [x] Model fallback for the chat and the call summary
- [ ] Customer confirmation by SMS or an approved WhatsApp template, behind the same `send()` function
- [ ] Real transfer to the owner's phone, once telephony exists
- [ ] Evaluation set per tenant, then choose `min_score` from the results
- [ ] Review pages with numbers and warnings, then switch to `only_verified=True`
- [ ] Automated tests, and an injection test suite: caller attacks and poisoned documents
- [ ] FastAPI server: web chat endpoint, session store with idle timeout, site key per tenant
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