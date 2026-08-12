# Skills Agent Platform

A platform for defining reusable AI **skills** and running them safely.

A skill is a saved, versioned definition: instructions, an input and output
schema, worked examples, the tools it is allowed to use, and a step limit. When
a skill runs, an agent works through it using only the tools that skill was
granted — and any action that changes data stops and waits for a human to
approve it.

**Live application:** <https://skills-agent-platform.onrender.com>

> Hosted on a free tier. If the page takes a moment on first load, the container
> is waking up.

---

## What it does

- **Define skills** with instructions, JSON Schema for input and output,
  examples, a tool allowlist, and a maximum number of steps.
- **Validate before use.** A definition with errors cannot be published or run.
- **Draft and published versions.** Publishing freezes a version permanently;
  changes go into a new draft.
- **Run with sample input**, including drafts, so a skill can be tested before
  it is published.
- **See exactly what happened** — every model turn, tool call, argument, result,
  refusal and error, in order, with timings.
- **Approve or reject write actions.** The run pauses and nothing is written
  until a human decides.
- **Never execute an approved write twice**, even on a double click or a retry.
- **Compare any two versions** field by field, and **rerun** an old version.
- **Full history** of runs, errors, approvals and versions. Nothing is deleted.

---

## Architecture

One FastAPI process serves both the JSON API and the built React frontend, so
there is a single service to deploy and no CORS configuration.

```
Browser
  │
  │  same origin — no CORS
  ▼
FastAPI  ──────────────────────────────────────────────┐
  ├── /api/*          JSON API                         │
  ├── /*              serves the built React app       │
  │                                                    │
  ├── agent.py        the agent loop                   │
  │     ├── llm.py    Gemini, behind one function      │──▶ Google Gemini API
  │     ├── tools.py  the four permitted tools         │
  │     └── validation.py  definition + input checks   │
  ▼                                                    │
Postgres (Neon in production, SQLite locally) ◀────────┘
```

### The files that matter

| File | What lives there |
|---|---|
| [`app/agent.py`](app/agent.py) | The agent loop: permissions, approvals, retries, step limits |
| [`app/models.py`](app/models.py) | Eight tables; the data model is the design |
| [`app/tools.py`](app/tools.py) | The four tools and which of them writes data |
| [`app/validation.py`](app/validation.py) | Every rule about what makes a definition usable |
| [`app/llm.py`](app/llm.py) | The only file that knows about Gemini |
| [`app/routes_skills.py`](app/routes_skills.py) | Skills, versions, publishing, comparison |
| [`app/routes_executions.py`](app/routes_executions.py) | Running, approving, cancelling, history |

### How a run works

1. Check the run has not been cancelled and has steps remaining.
2. Ask the model what to do next, declaring **only** the tools this skill allows.
3. If it asked for a tool:
   - unknown, or not on the allowlist → **refuse**, tell the model why, continue
   - it writes data → **pause** and wait for a human
   - otherwise → run it, feed the result back, loop
4. If it gave a final answer → validate against the output schema and finish.

Every step is written to the database as it happens and committed immediately.
That single decision is what makes a run auditable, resumable after an approval,
and cancellable while it is still going.

The conversation sent to the model is **rebuilt from those database rows** on
every turn. No conversation state is held in memory, so resuming a run that
paused three days ago behaves exactly like continuing one that never stopped.

### The four tools

| Tool | Purpose | Writes data |
|---|---|---|
| `calculator` | Arithmetic | No |
| `document_search` | Keyword search over a document library | No |
| `record_lookup` | Fetch one business record by type and id | No |
| `create_task` | Create a task | **Yes — always needs approval** |

A tool not in this registry does not exist. If the model invents one, the
platform refuses the call and tells it so as a tool result, so it can recover
instead of the run dying.

### How duplicate writes are prevented

Three independent layers, so no single bug can cause a double write:

1. **An idempotency key** — a stable fingerprint of
   `(execution, step, tool, arguments)` — with a `UNIQUE` constraint.
2. **A check-and-set `executed` flag** on the approval, set in the same
   transaction, so two simultaneous approvals cannot both proceed.
3. **A `UNIQUE` constraint on the tasks table itself**, so even if all the
   application logic failed, the database would still refuse the second write.

---

## Running it locally

Requires Python 3.12+ and Node 20+.

```bash
# 1. Backend dependencies
python -m venv .venv
.venv\Scripts\activate          # Windows
# source .venv/bin/activate     # macOS / Linux
pip install -r requirements.txt

# 2. Configuration
cp .env.example .env
# then edit .env and add your GEMINI_API_KEY

# 3. Frontend
cd frontend
npm install
npm run build
cd ..
cp -r frontend/dist app/static   # Windows: xcopy /E /I frontend\dist app\static

# 4. Run
uvicorn app.main:app --reload
```

Open <http://localhost:8000>. The database is created and seeded on first start,
so there is a working demo skill immediately.

A free Gemini API key comes from <https://aistudio.google.com/apikey> — no card
required. Without one, everything except *running* a skill still works, and the
app says so clearly rather than failing halfway through.

### Frontend development

To work on the UI with hot reload, run the backend and frontend separately:

```bash
uvicorn app.main:app --reload      # terminal 1, port 8000
cd frontend && npm run dev         # terminal 2, port 5173
```

Vite proxies `/api` to port 8000, so the browser still sees one origin.

---

## Tests

```bash
pytest
```

70 tests, about five seconds, **no API key needed and no quota consumed** — the
language model is replaced with a scripted fake, so the tests are deterministic.

They concentrate on the things that must never break rather than chasing a
coverage number:

| File | Covers |
|---|---|
| [`tests/test_agent.py`](tests/test_agent.py) | Tool refusals, the approval pause, duplicate-write prevention, step limits, cancellation, tool failure, malformed output and its retry |
| [`tests/test_tools.py`](tests/test_tools.py) | Each tool, and that the calculator refuses anything that is not arithmetic |
| [`tests/test_validation.py`](tests/test_validation.py) | Every definition rule, including that a write tool cannot skip approval |
| [`tests/test_api.py`](tests/test_api.py) | Publishing gates, version immutability, comparison, input validation |

The agent was also verified end to end against the real Gemini API: a run that
used four read tools, calculated a refund, paused for approval, created exactly
one task on approval, and created **no** second task when approved again.

---

## Logs

Every log line is a single JSON object. That matters here because the
interesting events are about an AI workflow — *which tool did step 3 call, and
was it refused* — and those read far better as structured fields than as
sentences. There are two loggers: `app` for web events and `agent` for the AI
workflow.

A complete run, including a refused tool call and an approval, looks like this:

```json
{"time":"...","level":"INFO","logger":"agent","message":"tool_refused","execution_id":1,"tool":"document_search","reason":"not_allowed"}
{"time":"...","level":"INFO","logger":"agent","message":"tool_call","execution_id":1,"tool":"calculator","ok":true,"duration_ms":0}
{"time":"...","level":"INFO","logger":"agent","message":"approval_requested","execution_id":1,"tool":"create_task","step":3}
{"time":"...","level":"INFO","logger":"agent","message":"approval_granted","execution_id":1,"tool":"create_task","ok":true}
{"time":"...","level":"INFO","logger":"agent","message":"execution_finished","execution_id":1,"status":"completed","steps_used":4}
{"time":"...","level":"INFO","logger":"app","message":"http_request","method":"GET","path":"/api/skills","status_code":200,"duration_ms":15}
```

Other events worth knowing about: `llm_call_ok` (with `fell_back` when a backup
model was used), `llm_quota_exhausted`, `llm_model_unavailable`,
`trimmed_parallel_tool_calls`, `invalid_final_output`, `approval_rejected`,
`approval_replay_ignored` (a duplicate approval that was correctly ignored), and
`version_published`.

Logs go to stdout, which is where Docker and Render collect them. In production
they are under **Logs** in the Render dashboard.

## Demo walkthrough

The seeded skill is **Refund Eligibility Assessor**, which ships with two
published versions so version comparison and rerun work immediately.

- **v1** — read-only. It can look things up but has no way to escalate.
- **v2** — adds `create_task`, so it can escalate, which requires approval.

Try this input on **v2**:

```json
{
  "order_id": "ORD-1003",
  "customer_message": "The desk arrived damaged. I want a full refund please."
}
```

The agent looks up the order (18000 INR, delivered 12 days ago) and the customer
(standard tier), finds the refund and escalation policies, calculates
`18000 * 0.9 = 16200`, and — because that exceeds the 10000 escalation
threshold — asks to create a task. **The run pauses there.** Approve it and it
finishes; reject it and the run continues without the task.

Other data to try: `ORD-1002` (gold tier, within policy), `ORD-1004` (delivered
61 days ago, outside the refund window), `ORD-9999` (does not exist).

---

## Deployment

Deployed as a single Docker container on Render's free tier, with Postgres
hosted on Neon. [`render.yaml`](render.yaml) describes the service, so the
deployment lives in the repository rather than only in a dashboard.

Two secrets are set in the Render dashboard and never appear in the repository:

| Variable | What it is |
|---|---|
| `GEMINI_API_KEY` | Google AI Studio key |
| `DATABASE_URL` | Neon Postgres connection string |

See [`.env.example`](.env.example) for every configuration name.

**Free-tier cold starts.** Render suspends a free service after 15 minutes
without traffic, and waking it takes about a minute. A scheduled ping every 10
minutes keeps it warm. `GET /api/health` is a cheap endpoint suited to this.

---

## Scope

### Built

Everything described under [What it does](#what-it-does), plus structured JSON
logging for both application and agent events, a health endpoint, and loading,
empty, validation, success and failure states on every screen.

### Deliberately left out

These were cut to keep the core workflow solid, not overlooked:

- **Authentication and multi-user support.** The platform is single-tenant. Adding
  users would mean an ownership column on every table and permission checks on
  every route — a lot of surface area that would not have made the agentic
  workflow any better.
- **Real external integrations.** All four tools operate on local data. The point
  of the tool layer is the permission and approval model around it, which is
  identical whether `create_task` writes to a local table or to Jira.
- **Database migrations.** Tables are created from the models on startup. With a
  schema this new and a build window this short, Alembic would have been
  ceremony. A schema change today means recreating the database.
- **Token streaming.** Runs are synchronous and the UI shows a clear working
  state. Streaming would improve the feel but not the correctness.
- **Skill sharing or a marketplace**, and **per-user rate limiting**.

---

## Known limitations

**Runs are synchronous.** A `POST` to run a skill stays open until the run
finishes or pauses. This keeps the system easy to reason about — no queue, no
workers, no polling — but a long run holds a connection, and cancellation is
detected at the start of the next step rather than instantly. Because every step
is committed as it happens, cancelling a *paused* run is immediate.

**The free Gemini tier is small.** Google caps free requests **per day, per
model** — 20 for some models. One run costs 5–8 requests. The app therefore
tries a chain of models and moves to the next when one is exhausted, which
multiplies the daily budget. When every model is exhausted the app says so
plainly, and everything except running a skill keeps working. The quota resets
at midnight Pacific time.

**One tool call per turn.** Gemini sometimes requests several tools at once. The
API then requires one response per call and returns an *empty completion* if the
counts do not match — a silent, hard-to-diagnose failure. Each turn is therefore
normalised to its first tool call; the model simply asks again for anything else
it needs. This costs an extra round trip in rare cases and is logged when it
happens.

**Document search is a keyword match**, not semantic search. Fine for a bounded
demo corpus; a real deployment would use full-text search or embeddings.

**Model output is not deterministic.** Rerunning a version reproduces the exact
*definition*, not the exact answer. That is precisely why both runs are kept in
history.

**No migrations**, as described above.
