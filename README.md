# Contract Guard

Contract Guard is a registry for OpenAPI specs. A team uploads each version of
their API, the app compares it with the last published version, says which
changes are breaking, and shows which consumer services would be affected. If a
consumer would break, the publish is blocked until the owning team overrides it.

## Run it from scratch

You need Docker (with Docker Compose) and git. Nothing else.

```bash
git clone <repository-url>
cd contract-guard
docker compose up --build
```

Then open http://localhost:8000 for the app. The kit's webhook receiver starts
on http://localhost:9900 too, but the app does not send anything to it yet.

When the database is empty, the app seeds the kit on startup: three services
(quote-api, policy-api, claims-api), the four consumers, and every kit spec in
publish order. Uploads are written to the SQLite file under `data/`.

To start again with an empty database, run `docker compose down`, delete
`data/`, then run `docker compose up --build` again. The kit is seeded again.

## A short walkthrough

All of this works on the seeded data, nothing has to be uploaded.

1. Open http://localhost:8000. You see the three services, their owner teams,
   latest published version, and last publish attempt. quote-api's last attempt
   (`2.1.0`) is blocked, and `2.0.0` was blocked too.
2. Click `quote-api`, then `Details` on the `2.0.0` attempt. It was blocked
   because mobile-app reads `premium.breakdown[].amount`, which was removed. The
   page lists the affected consumer and the reasons.
3. On the same page, use "Compare two versions" from `1.1.0` to `2.0.0`. The
   changelog is grouped by endpoint with a plain English reason for each change.
4. Open `Consumers` in the nav, then `payments-svc`. It has a stale reference
   error for `legacyCode` because policy-api `2.0.0` removed it, and it names
   the published version it stopped matching.
5. Open `Metrics` to see attempts by outcome, overrides, breaking changes
   caught, consumers protected, and stale references.
6. To see an override, open `Override` on quote-api's blocked `2.0.0` attempt.
   Type `Quotes team` as the approver, give a reason and submit. The attempt
   becomes `overridden`, counts as published, and the metrics page changes. Any
   other team name is rejected.

The other kit outcomes are there too: claims-api `1.1.0` is rejected for a bad
`$ref`, then the fixed file reuses `1.1.0`. Whether policy-api `2.0.0` removals
are warnings or breaking depends on today's date against the `x-sunset` dates.

## Running the tests

Tests run inside the app container. Windows Application Control on my laptop
blocks the `rpds-py` DLL that openapi-spec-validator needs, so pytest cannot
import it on Windows. Docker also uses the same Python 3.12 as the app. Build
the image once (`docker compose up --build` or `docker compose build`) first.

```bash
docker compose run --rm -v .:/app app python -m pytest -q
```

You can run one folder at a time:

```bash
# core functions: normalize, diff, classify, impact, changelog, metrics
docker compose run --rm -v .:/app app python -m pytest -q tests/unit

# the FastAPI app through the TestClient with a temporary database
docker compose run --rm -v .:/app app python -m pytest -q tests/integration

# the real kit files, published in order
docker compose run --rm -v .:/app app python -m pytest -q tests/acceptance
```

## API endpoints

All JSON routes are under `/api`. Errors are returned as `{"error": "..."}`. The
upload routes accept either a multipart file field named `file` or a raw YAML/JSON
request body. Upload, impact, diff and changelog also take an optional
`today=YYYY-MM-DD` query parameter for the `x-sunset` checks; it defaults to the
current date.

| Method | Path | What it does |
| --- | --- | --- |
| GET | `/health` | Health check |
| POST | `/api/services` | Register a service (name, owner_team, contact) |
| GET | `/api/services` | List services with their latest published version |
| GET | `/api/services/{name}` | One service |
| POST | `/api/services/{name}/versions` | Upload a spec, validate it and run the publish gate |
| GET | `/api/services/{name}/versions` | List stored versions and their status |
| POST | `/api/services/{name}/impact` | Diff a proposed spec against the latest published one; saves nothing |
| GET | `/api/services/{name}/diff?from=&to=` | Classified diff between two stored versions |
| GET | `/api/services/{name}/changelog?from=&to=&format=json\|markdown` | Changelog between two stored versions, as JSON or Markdown |
| GET | `/api/services/{name}/attempts` | Publish history for a service |
| POST | `/api/services/{name}/attempts/{id}/override` | Override a blocked attempt (approved_by must be the owner team, reason required) |
| POST | `/api/consumers` | Create a consumer or replace its usages from a kit-format YAML |
| GET | `/api/consumers` | List consumers and their usages |
| GET | `/api/consumers/{name}` | One consumer, its usages and stale references |
| GET | `/api/metrics` | Metrics across all services and consumers |

## Project layout

```
app/core/       pure functions: normalize, diff, classify, impact, changelog, metrics
app/api/        FastAPI JSON routes and error handling
app/services/   publish gate, override, database-facing logic
app/web/        Jinja2 pages and HTMX templates
app/db/         SQLAlchemy models and session
scripts/        seed.py, which imports the kit
tests/          unit, integration and acceptance tests
kit/            the starter kit (read only)
data/           SQLite database, created on first run
docs/           plan, rules, self-review and AI transcripts
```

## What works and what doesn't

Works: all 10 must-haves.

- Service and spec registry with validation and clear errors for invalid specs
- `$ref` resolution and normalization to field paths, so moving or renaming a
  component produces zero changes
- The diff between two normalized specs, and the classifier with severities and
  plain English reasons
- Consumer registration and impact analysis, including parent paths and stale
  references
- The publish gate, blocking, overrides, changelog, history, the HTMX UI and
  the metrics page

Not done or not supported:

- Webhook notifications and the CI check endpoint were skipped for time. The
  receiver container starts on port 9900 but no notifications are sent.
- No login or auth. Overrides are protected only by checking that the typed
  approver name matches the service's owner team.
- `min`/`max`/`pattern` constraints and `oneOf`/`anyOf` are not compared.
- External `$ref`s and OpenAPI 3.1 are not supported.
- Seeding uses today's date for the `x-sunset` checks, so the policy-api
  `2.0.0` result depends on when the kit was seeded.
- No database migrations. Tables are created on startup, so a schema change
  means deleting `data/` and starting again.

## Docs

- [docs/PLAN.md](docs/PLAN.md) - the plan, the PO questions, the data model
- [DECISIONS.md](DECISIONS.md) - every decision and assumption, with times
- [docs/RULES.md](docs/RULES.md) - the classification rule table
- [docs/SELF_REVIEW.md](docs/SELF_REVIEW.md) - what I would change or improve
- [docs/transcriptions/](docs/transcriptions/) - AI session transcripts