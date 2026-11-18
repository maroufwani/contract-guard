# AGENTS.md

Rules for the AI coding assistant working on this repo. Please read this fully before doing anything.

## What this project is

Contract Guard is a registry for OpenAPI specs. Teams upload each version of their API spec, and the app works out what changed since the last published version, decides which changes can break consumers, shows which consumer services are affected, and blocks the publish if a consumer would break. The full plan is in docs/PLAN.md and my decisions are in DECISIONS.md. If something in my prompt goes against those files, ask me before doing it.

## Stack

- Python 3.12 (inside Docker). My laptop has Python 3.14, so don't use anything that only works on one of them.
- FastAPI, SQLAlchemy 2.x with SQLite, Jinja2 templates + HTMX for the UI (no npm, no React, no frontend build step)
- PyYAML, openapi-spec-validator
- pytest for all tests
- docker compose at the repo root runs the app on port 8000 and the kit's webhook receiver

Don't add any other dependency without asking me first.

## Folder layout

```
app/core/      pure functions: normalize, diff, classify, impact, changelog
app/api/       FastAPI JSON routes
app/web/       HTML pages (Jinja2 + HTMX)
app/db/        SQLAlchemy models and session
scripts/       seed.py imports the kit specs and consumers
tests/unit/    tests for app/core
tests/integration/   tests through the FastAPI TestClient with a temp database
tests/acceptance/    tests that run the real kit files in publish order
docs/          plan, notes, AI transcripts
kit/           the starter kit, READ ONLY
```

## Hard rules

1. Never create, edit, move or delete anything inside kit/. Evaluators test against the original kit. If you need a changed copy for a test, put it under tests/fixtures/.
2. Never run git commit, git push or any git command that changes history. I do all commits myself.
3. Code in app/core must not import FastAPI, SQLAlchemy or anything from app/db or app/api. It takes plain dicts and returns plain dicts/dataclasses so I can unit test it.
4. Every feature comes with tests in the same step. Run pytest before telling me you are done and show me the result. Don't say tests pass if you didn't run them.
5. Only do what the current prompt asks. Don't start the next feature on your own, and don't rewrite files that the prompt didn't mention.
6. If something is unclear or the kit does something unexpected, stop and ask me instead of guessing. If you have to assume something, tell me so I can put it in DECISIONS.md.
7. No secrets, no hardcoded absolute paths from my machine. Config comes from environment variables with sensible defaults (for example WEBHOOK_URL defaults to http://webhook-receiver:9000).

## Domain rules (important, these are easy to get wrong)

- Always diff the normalized spec, never the raw YAML. Normalizing means resolving all internal $refs and turning schemas into field paths. Renaming or moving a component (for example Premium to PremiumDetails) must produce zero changes.
- Field paths use the kit notation: dots for nested fields and [] for list items, like premium.breakdown[].amount. If a response is an array at the top level, paths start with [], like [].legacyCode.
- An endpoint is identified by method + path template, with path parameters compared by position, not by name. So GET /quotes/{id} and GET /quotes/{quoteId} are the same endpoint.
- Direction matters. Request changes are matched against what a consumer "sends", response changes against what it "reads". In a request, making things stricter is breaking (new required field, enum value removed, optional to required). In a response, taking things away is breaking (field removed, required to optional, type changed).
- A change to a parent path affects all its children. Removing premium.breakdown affects a consumer that reads premium.breakdown[].amount.
- Operations or fields marked x-internal never block a publish. x-sunset is a deprecation date (exact rules are in DECISIONS.md).
- Consumer files can be out of date. A consumer reference that doesn't match the current published spec is a "stale reference" ERROR on that consumer, saying which published version it stopped matching. It must never crash anything, and it does not block new publishes.
- A version number is only taken once it is published. A new upload must have a higher version (semver compare) than the last published one of that service, otherwise it is rejected. Rejected or blocked numbers can be reused.
- Every change must have a severity (breaking, warning, safe) and a short reason in plain English.

## Style

- Simple readable code, type hints, small functions. No clever metaprogramming.
- Clear error messages for invalid specs (say what is wrong and where, for example the bad $ref).
- Comments only where the logic is not obvious.
