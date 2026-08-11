# How coding agents were used

Most of the code in this repository was written by an AI coding agent, working
from my direction. This document is an honest account of what that involved:
what I delegated, what the agent got wrong, what I rejected, and how I checked
the result.

## Tools

| Tool | Used for |
|---|---|
| **Claude Code** (Opus) in VS Code | Nearly all implementation: backend, frontend, tests, documentation |
| Google AI Studio | Obtaining the Gemini API key |
| Google Gemini API | The model the built application itself calls at runtime |

## What I decided, and what I delegated

I made the product and architecture decisions and drove the debugging; the agent
did the typing and much of the diagnosis.

**Mine:**

- Choosing this problem over the alternative, based on the scoring weights and
  on the fact that its requirements are concrete and verifiable rather than
  subjective.
- The stack: Python backend for the parts I would have to defend, one service
  rather than two, plain CSS rather than a framework, and no authentication.
- Freezing scope on day one and requiring a deployment before features.
- Rejecting the agent's initial hosting recommendation (below).
- Rejecting AI co-author trailers on commits in favour of this document.

**Delegated:**

- Writing the data model, agent loop, tool registry, validation rules, HTTP
  routes, React screens and the test suite.
- Diagnosing three genuine integration bugs against the live Gemini API.
- Drafting these documents, which I reviewed and corrected.

## Representative prompts

Paraphrased, but these are the shapes of instruction that produced the work.

> Discuss which of these two problems I should choose given the time constraints,
> and only start coding after I confirm.

> Build the backend. Keep it simple and boring — no clever abstractions. I am
> shaky on this stack and have to explain every line in an interview, so comments
> should say *why*, not *what*.

> Docker is behind a paywall on Hugging Face.

> If I share my GitHub repo link here, will you show up as co-author? I do not
> want that.

> Write tests for the behaviour that must never break, using a fake model so they
> are deterministic and do not consume quota.

## Where the agent was wrong

These are the corrections that mattered.

**1. It recommended a host that is no longer free.**
The agent proposed Hugging Face Spaces and argued the case in detail, based on
Docker Spaces being free. I told it that Docker was paywalled. It checked the
current documentation, confirmed personal accounts now need PRO at $9/month,
and switched to Render — which turned out better anyway, since Render deploys
from a repository without requiring a public mirror. **Lesson: the agent stated
platform pricing confidently from stale knowledge. Anything about a third
party's current terms has to be verified, not assumed.**

**2. It called my API key the wrong format.**
It claimed my key looked like an OAuth token rather than an API key because it
started with `AQ.` instead of `AIza`, and predicted it would fail. It then
tested rather than acting on the guess — and the key authenticated fine. The
real problem was elsewhere: the default model name, `gemini-2.5-flash`, is no
longer offered to new accounts and returned 404. **Testing beat the confident
guess.**

**3. Its own test harness was wrong before the code was.**
The first smoke test failed with "no such table". The agent had used FastAPI's
`TestClient` without a context manager, so the startup hook that creates the
tables never ran. The application was fine; the test was broken. Worth
remembering when an agent reports a failure — the failure may be in its
scaffolding.

## Three real bugs that only live testing found

None of these were visible from reading the code. All three came from running
the agent against the real API and dumping raw responses.

**Signed reasoning must be replayed.** Gemini 3 attaches a `thoughtSignature` to
a tool call, and rejects the conversation if that signature does not come back
with the call it belongs to. Because this application rebuilds the conversation
from database rows on every turn, the signature was being dropped. The first fix
stored the signature in its own column — which then failed, because Gemini
sometimes omits it entirely. The correct fix was to stop reconstructing the
model's turn at all and store the provider's reply verbatim, replaying it
unchanged. That removed the whole class of bug.

**Parallel tool calls cause a silent empty reply.** The model occasionally
requests two tools in one turn. The API requires one response per call, and when
the counts do not match it returns HTTP 200 with an empty completion and no
error at all. Diagnosing this needed a dump of the raw request and response and
a comparison of two candidate fixes side by side. Each turn is now normalised to
a single call — the tested fix, chosen over the more elaborate one.

**The free quota is per model, per day.** A 429 turned out to be a *daily* cap of
20 requests for one model, not a per-minute limit. At 5–8 requests per run, that
is roughly three runs a day — unusable for anyone reviewing the application.
Since the quota is counted per model, the fix was a fallback chain that moves to
the next model when one is exhausted.

## What I rejected

- **AI co-author trailers on commits.** The agent's default was to add
  `Co-Authored-By: Claude` to every commit. I asked it to stop and disclose here
  instead, where the description is accurate and has context. Commit authorship
  is entirely mine.
- **Hugging Face Spaces**, as described above.
- **An API key pasted into the wrong file.** Twice I pasted my key into
  `.env.example`, which is tracked by git and was headed for a public repository.
  The agent caught it both times, reverted the file, confirmed against the git
  history that the key had never been committed, and added extra ignore patterns.
  I then rotated the key.

## How I verified the output

- **70 automated tests**, which I asked for specifically on the logic where a bug
  would be serious: tool refusals, the approval gate, duplicate-write prevention,
  step limits, and the calculator refusing anything that is not arithmetic.
  They use a scripted fake model, so they are deterministic and cost no quota.
- **An end-to-end run against the real API**, checked by hand: the agent used
  four read tools, calculated the refund correctly, paused before writing,
  created exactly one task on approval, and created no second task when approved
  again.
- **Raw API inspection.** For all three integration bugs I had the agent dump the
  actual request and response rather than trusting its explanation, and compare
  candidate fixes before choosing one.
- **Reading the code.** The agent was told throughout to avoid clever
  abstractions and to comment the reasoning behind decisions, because I have to
  be able to explain any part of this. Where it produced something convoluted —
  a response parser that had grown an inline assignment inside a loop — I had it
  rewritten plainly.

## Honest assessment

The agent was fastest at things with a clear specification: the data model, the
routes, the React screens, the tests. It was most useful at diagnosing the
Gemini integration problems, which involved reading raw API payloads and forming
hypotheses quickly.

It was least reliable on facts about the outside world — hosting prices, key
formats, model availability — where it stated stale information with confidence.
Every such claim in this project turned out to need checking, and two were
wrong. The working pattern that held up was: let it propose and implement, then
make it prove the result by running something.
