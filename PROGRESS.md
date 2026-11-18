# Contract Guard - progress against the problem statement

Last updated: 18 Nov 2026

## Snapshot

| Area | Status |
| --- | --- |
| Must-haves 1 to 10 | 10 / 10 implemented |
| Tests | 124 passing (unit, integration, acceptance) |
| Runs with one command | `docker compose up --build` (app on 8000, kit webhook receiver on 9900) |
| Optional "going further" items | 0 / 3 done (webhook notifications, CI check, AI migration guide) |

The product works end to end on the real kit files: upload a spec, validate it,
normalize it, diff it against the last published version, classify every change,
match it against registered consumers, block a publish when a consumer breaks,
and record the attempt.

## Must-have coverage

| # | Feature | Status | Where it lives | How it is proven |
| --- | --- | --- | --- | --- |
| 1 | Service and spec registry | Done | `app/api/services.py`, `app/web/routes.py`, `app/db/models.py`, `app/services/publish.py` | `tests/integration/test_upload.py`, `tests/unit/test_normalize.py`, `tests/acceptance/test_kit_publish.py` |
| 2 | Shared components / `$ref` | Done | `app/core/normalize.py` | Renaming/moving a component produces zero changes (quote 1.0.0 to 1.1.0); `tests/unit/test_normalize.py` |
| 3 | Comparing versions | Done | `app/core/diff.py` | `tests/unit/test_diff.py` covers endpoints, methods, params, nested fields, list items, types, required flags, enums and response codes |
| 4 | Classifying changes | Done | `app/core/classify.py`, `docs/RULES.md` | `tests/unit/test_classify.py` covers request vs response direction, `x-internal`, `x-sunset`, semver bump and unknown kinds |
| 5 | Consumers | Done | `app/core/consumers.py`, `app/api/consumers.py`, `app/web/routes.py` | `tests/integration/test_consumers.py` |
| 6 | Impact | Done | `app/core/impact.py` | `tests/unit/test_impact.py`, `app/api/services.py` (`POST /impact`) |
| 7 | Publishing safely | Done | `app/core/gate.py`, `app/services/publish.py` | Blocking and override flow in `tests/integration/test_publish.py` and `tests/acceptance/test_kit_publish.py` |
| 8 | Changelog and history | Done | `app/core/changelog.py`, `app/api/services.py` | `tests/unit/test_changelog.py`, `tests/integration/test_changelog_api.py` |
| 9 | UI | Done | `app/web/routes.py`, `app/web/templates/` | `tests/integration/test_pages.py`; pages for services, service detail, compare, attempt, consumers, consumer detail and metrics |
| 10 | Metrics and runnable | Done | `app/core/metrics.py`, `app/api/metrics.py`, `docker-compose.yml`, `README.md` | `tests/unit/test_metrics.py`, `tests/acceptance/test_kit_metrics.py` |

### 1. Service and spec registry

- Register a service with name, owner team and contact, through `POST /api/services`
  and the UI form.
- Upload YAML or JSON as a file or raw body through `POST /api/services/{name}/versions`
  and the service page.
- Invalid specs are rejected with a clear message and location (for example the bad
  `$ref` name, a missing `openapi` field, a consumer file uploaded to the spec box,
  or an empty upload).
- Version numbers are checked with semver against the last published version.
  Rejected and blocked numbers can be reused.

### 2. Shared components

- All internal `$ref`s are resolved before comparing, and schemas are flattened
  into kit field paths (`premium.breakdown[].amount`, top-level arrays as
  `[].legacyCode`).
- Endpoints are identified by method plus path template, with path parameters
  compared by position, so `GET /quotes/{id}` and `GET /quotes/{quoteId}` match.
- The quote 1.0.0 to 1.1.0 reorganise/rename produces zero changes in the
  acceptance test.

### 3. Comparing versions

The diff reports added, removed and changed:

- endpoints and methods, including deprecation
- path and query parameters, including required, type and enum changes
- request bodies and request fields, nested objects and list items
- response fields, nested objects and list items
- types, formats, required flags and enum values
- response status codes added or removed

### 4. Classifying changes

- Direction-aware rules: request changes that make the contract stricter are
  breaking; response changes that take something away are breaking.
- Every change gets a severity (`breaking`, `warning`, `safe`) and a plain English
  reason that names the endpoint, field and affected consumer where relevant.
- A parent path change covers all children (`premium.breakdown` removal hits a
  reader of `premium.breakdown[].amount`).
- `x-internal` never blocks a publish.
- `x-sunset` controls when a removal drops from breaking to warning.
- Unknown change kinds fall back to a warning instead of crashing.

### 5. Consumers

- Four kit consumers are seeded; more can be uploaded in the kit's YAML format
  through `POST /api/consumers` and the Consumers page.
- Each consumer records what it sends and reads per provider method and path.
- A reference that no longer matches the published spec becomes a stale reference
  error on that consumer, with the published version it stopped matching. It never
  crashes the app and does not block new publishes.

### 6. Impact

- `POST /api/services/{name}/impact` diffs a proposed spec against the latest
  published version and saves nothing.
- The result groups affected consumers with the exact changes that hit them.
- The service page shows the same impact before a publish, and the attempt page
  shows who a blocked version would have affected.

### 7. Publishing safely

- A publish is blocked when a breaking change matches what a consumer sends or
  reads.
- A breaking change with no registered consumer is a warning, not a block.
- The owning team can override a block with a written reason; the approver must
  match the service's owner team. Overrides are recorded and count as published
  for later comparisons.
- Blocked versions are never used as the comparison base: quote 2.1.0 is compared
  with 1.1.0 while 2.0.0 stays blocked, and after an override of 2.0.0 the next
  upload compares with 2.0.0.

### 8. Changelog and history

- Changelog between any two stored versions as JSON or Markdown, grouped by
  endpoint and ordered by worst severity.
- Every publish attempt is stored with its outcome (`published`, `blocked`,
  `rejected`, `overridden`), the version it was compared against, its changes,
  affected consumers, override reason and approver.
- The UI shows the attempt list per service and a detail page per attempt.

### 9. UI

Plain Jinja2 and HTMX, no npm or frontend build step:

- services list with owner, latest published version and last attempt
- service page with versions, attempts, upload and proposed-impact forms
- compare page for two versions
- attempt detail with affected consumers and the override form
- consumers list and consumer detail with stale references
- metrics page

### 10. Metrics and runnable

- The metrics API and page show total attempts, outcomes, overrides, breaking
  changes caught, consumers protected, breaking changes released through an
  override, breaking changes with no consumer, stale references and block rate.
- `docker compose up --build` starts the app and the kit's webhook receiver; the
  database is seeded from the kit on first start when empty.
- `README.md` explains running from scratch, the walkthrough, the API and how to
  run the tests.

## Kit acceptance result

Seeding the real kit in publish order (acceptance test, fixed date 2026-11-18):

| Service | Version | Outcome | Why |
| --- | --- | --- | --- |
| quote-api | 1.0.0 | published | initial |
| quote-api | 1.1.0 | published | components reorganised, no contract change |
| quote-api | 2.0.0 | blocked | removes `premium.breakdown[].amount`, read by mobile-app |
| quote-api | 2.1.0 | blocked | still carries the removal, compared with 1.1.0 |
| policy-api | 1.0.0 | published | initial |
| policy-api | 1.1.0 | published | deprecations and sunset dates only |
| policy-api | 2.0.0 | published | removals are warnings under the sunset rules |
| policy-api | 2.1.0 | blocked | new required request field and removed request enum value, hits partner-portal |
| claims-api | 1.0.0 | published | initial |
| claims-api | 1.1.0 | rejected | bad `$ref` (`ClaimDocumnt`) |
| claims-api | 1.1.0 | published | fixed file reuses the number |
| claims-api | 2.0.0 | blocked | `amountApproved` becomes optional in a response, read by mobile-app |

Totals: 7 published, 4 blocked, 1 rejected. `payments-svc` is flagged with a stale
reference for `legacyCode`, which stopped matching at policy-api 2.0.0. Metrics:
5 breaking changes caught, 2 consumers protected, 1 stale reference.

If policy-api is seeded before 1 Nov 2026, the `x-sunset` dates have not passed,
so policy-api 2.0.0 would be blocked instead of published. This is expected from
the decision log, not a bug.

## Tests

Ran on 18 Nov 2026 in this working copy:

```text
.venv\Scripts\python.exe -m pytest -q
124 passed, 54 warnings in 13.69s
```

The suites are split so each layer can be checked on its own:

```text
tests/unit/           normalize, diff, classify, impact, changelog, metrics
tests/integration/    FastAPI TestClient, temp database, upload, publish, pages
tests/acceptance/     the real kit files in publish order
```

`README.md` documents the Docker command for running the same tests against Python
3.12:

```bash
docker compose run --rm -v .:/app app python -m pytest -q
```

`docker compose config --quiet` also passes. I did not rerun the full
`docker compose up --build` in this session; the compose file and Dockerfile are
in place and the README walkthrough is based on the seeded data.

## Going further (optional)

Not done yet:

1. **Webhook notifications.** `WEBHOOK_URL` defaults to
   `http://webhook-receiver:9000` and the receiver container starts with compose,
   but the app does not send anything yet. Planned: notify the provider team and
   affected consumer teams on publish, block, reject and override.
2. **CI check endpoint.** A single endpoint that a pipeline can call with a spec
   and a base version, returning a non-zero/block decision without saving
   anything. `POST /impact` is the closest existing building block.
3. **AI migration guide per affected team.** Not started; this was explicitly left
   out of the 6-hour scope in `docs/PLAN.md` in favour of correctness.

The next step for these is to ask the product owner which one matters most.

## Known gaps and limitations

- No login or auth. An override only checks that the typed approver name matches
  the service's owner team.
- `min`/`max`/`pattern` constraints, `oneOf`/`anyOf`, external `$ref`s,
  OpenAPI 3.1 and Swagger 2 are not compared or supported.
- No database migrations; tables are created on startup, so a schema change means
  recreating `data/`.
- The seeded result for policy-api 2.0.0 depends on today's date against the
  `x-sunset` dates.
- The 118-test figure in `docs/SELF_REVIEW.md` is stale; the current suite is 124
  tests.

## Useful documents

- `README.md` - run, walkthrough, API endpoints, test commands
- `docs/PLAN.md` - the plan, PO questions, data model, test plan
- `DECISIONS.md` - every decision and assumption with times
- `docs/RULES.md` - the classification rule table
- `docs/SELF_REVIEW.md` - what works, what does not, what to do next
