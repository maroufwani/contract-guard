# Contract Guard - Design

Status: implemented, 18 Nov 2026
See also: `docs/PLAN.md` (plan and PO answers), `DECISIONS.md` (decision log),
`docs/RULES.md` (classification table), `progress.md` (feature status).

## 1. Problem and goals

Contract Guard is a registry for OpenAPI specifications. A provider team uploads
each version of its API before release. The system compares it with the last
published version, explains every contract change, finds which registered
consumers would break, and refuses to publish when a known consumer would break.

Design goals, in priority order:

1. **Compare the contract, not the file.** Resolve `$ref`s and flatten schemas
   to field paths before diffing. Moving or renaming a component must produce
   zero changes.
2. **Every change has a severity and a reason.** No silent changes. The rules
   depend on request vs response direction and on `x-internal` / `x-sunset`.
3. **Only block for real consumers.** A breaking change that nobody registered
   uses is a warning, not a block.
4. **Keep the decision auditable.** Every upload, block, rejection and override
   is stored with its reasons, changes and affected consumers.
5. **Pure core, thin edges.** Diffing, classification, impact and changelog are
   plain functions over dicts; they can be unit tested without a database or a
   web server.
6. **Runs with one command.** Docker Compose starts the app, the database, the
   seeded kit and the kit's webhook receiver.

## 2. Non-goals

- Login, roles and real authorisation.
- External `$ref`s, OpenAPI 3.1 and Swagger 2.
- Structural comparison of `oneOf` / `anyOf`; constraint comparison
  (`min`/`max`/`pattern`).
- Field-by-field consumer registration in the UI.
- A frontend build step or a JavaScript framework.
- Database migrations.

## 3. System overview

```
             browser (Jinja2 + HTMX)
                       |
                       | HTTP
                       v
+-------------------------------------------------------------+
| FastAPI app (app/main.py)                                   |
|                                                             |
|  app/web   HTML pages        app/api   JSON API             |
|        \                        /                           |
|         +---- app/services/publish.py ----+                 |
|         |        orchestration            |                 |
|         v                                 v                 |
|  app/core: normalize -> diff -> classify -> impact -> gate  |
|                   \-> changelog, metrics                    |
|                                                             |
|  app/db: SQLAlchemy models + SQLite                         |
+-------------------------------------------------------------+
          |                                   |
          v                                   v
   data/contract_guard.db              kit webhook receiver
   (SQLite, JSON snapshots)            (started by compose, not called yet)
```

The UI and the JSON API are two adapters over the same domain functions. No web
page makes an HTTP call back into the app's own API; both layers call the same
Python service and core functions with the request's database session.

Dependency rule:

```
app/core      imports nothing from FastAPI, SQLAlchemy, app/db or app/api
app/services  may import app/core and app/db
app/api       imports services and core
app/web       imports api helpers, services and core
scripts       imports services (seeding)
```

`app/core` receives plain dicts and returns plain dicts or dataclasses. `today`
is passed in explicitly, so `x-sunset` decisions are deterministic in tests.

## 4. Module responsibilities

| Module | Responsibility |
| --- | --- |
| `app/core/normalize.py` | Load YAML/JSON, validate, resolve `$ref`s, flatten to field paths |
| `app/core/versions.py` | Parse and compare `X.Y.Z` labels |
| `app/core/diff.py` | Produce `Change` records between two normalized specs |
| `app/core/classify.py` | Rule table: severity + rule id + plain English reason |
| `app/core/impact.py` | Match changes to consumer usages; find stale references |
| `app/core/gate.py` | Pure publish decision from classified changes and impact |
| `app/core/changelog.py` | Group classified changes by endpoint, JSON or Markdown |
| `app/core/metrics.py` | Aggregate attempts and stale references into dashboard numbers |
| `app/core/consumers.py` | Parse the kit-format consumer YAML |
| `app/core/errors.py` | `SpecError` for invalid specs |
| `app/services/publish.py` | Load/validate/gate/store an upload; override a block |
| `app/api/*.py` | JSON routes, request parsing, `{"error": ...}` responses |
| `app/web/routes.py` | HTML routes and HTMX partials |
| `app/db/models.py` | SQLAlchemy tables |
| `app/db/session.py` | Engine/session factory, SQLite parent directory, foreign keys |
| `scripts/seed.py` | Import the kit once into an empty database |

## 5. Normalized contract model

The normalized spec is the only thing stored and compared. It contains the
contract surface, never component names, descriptions, examples or file layout.

```python
{
  "version": "1.1.0",
  "endpoints": {
    "GET /quotes/{}": {
      "method": "GET",
      "path": "/quotes/{quoteId}",       # display spelling, not identity
      "internal": False,
      "deprecated": False,
      "params": {
        "path:0": {"required": True, "type": "string", "enum": None}
      },
      "request": None,                   # or {"required": bool, "fields": {...}}
      "responses": {
        "200": {"fields": {
          "quoteId": {
            "type": "string", "format": None, "required": True,
            "enum": None, "deprecated": False, "sunset": None,
            "internal": False
          },
          "premium.breakdown": {...},
          "premium.breakdown[].amount": {...}
        }}
      }
    }
  }
}
```

Key rules:

- **Endpoint identity** is `METHOD + " " + path template with "{}" placeholders`.
  `GET /quotes/{id}` and `GET /quotes/{quoteId}` are the same endpoint.
- **Path parameters** are keyed `path:0`, `path:1`, ... by position. The name is
  deliberately not stored, so renaming a path parameter is not a change.
- **Query/header params** are keyed `in:name`.
- **Field paths** use dots and `[]` for list items:
  `vehicle.make`, `premium.breakdown[].amount`. A top-level array starts with
  `[]`, for example `[].legacyCode`.
- **Objects** get their own entry when named, so a required-flag change on an
  object is visible. Anonymous array items do not get a second entry; the array
  entry already carries the required flag.
- **Arrays** store the array entry and then recurse into `items` with `[]`.
- **Enums** are sorted strings.
- **Required** means "required in its parent". Request schema roots and response
  schema roots start as required.
- **`$ref`** resolution is local-only. Recursive refs stop at type `recursive`
  using the set of refs on the current path. `allOf` parts are merged;
  `oneOf`/`anyOf` are recorded as an opaque field type rather than expanded.
- **Media type**: `application/json` is the contract; if a body/response has no
  JSON media type, the first media type is used.

## 6. Upload and publish pipeline

`publish_version` in `app/services/publish.py` is the only place a spec becomes
stored data.

```
POST spec (file or raw body)
  |
  +-- load_spec          YAML/JSON -> JSON-safe dict        -> reject 422
  +-- validate_spec      OpenAPI 3.0.x, local refs, shape   -> reject 422
  +-- version check      new > last published/overridden    -> reject 422
  +-- normalize_spec     resolve refs, flatten fields
  |
  +-- first published version? --------------------------> publish, stop
  |
  +-- diff_specs(last_published, new)
  +-- classify_all(changes, today)
  +-- check_version_bump(last, new, has_breaking)
  +-- compute_impact(classified, consumer usages, provider)
  +-- decide(classified, impact, version_warning)
  |
  +-- store SpecVersion + PublishAttempt
  +-- 201 published / 409 blocked / 422 rejected
```

Important invariants:

- **Rejected uploads never take a version number.** They create a
  `PublishAttempt(outcome="rejected")` with the error, but no `SpecVersion`.
  That is why claims-api `1.1.0` can be rejected and then reused.
- **Rejected specs never store a normalized spec.** Validation fails before
  normalization.
- **The comparison base is the newest `published` or `overridden` version.**
  Blocked and rejected versions are ignored. Quote `2.1.0` is therefore compared
  with `1.1.0` while `2.0.0` is blocked.
- **A version number must increase** by semver (missing parts count as zero)
  compared with the last published version, otherwise the upload is rejected.
  The number belongs to the service only once it is published.
- **A blocked spec does create a `SpecVersion(status="blocked")`**, so history and
  the UI can show it, but it is never a comparison base.
- **An override creates a new `PublishAttempt(outcome="overridden")`** and flips
  the blocked `SpecVersion` to `overridden`. The original blocked attempt is kept
  for audit. The override record copies the blocked changes, affected consumers
  and unused breaking changes, and adds the approver and reason.

## 7. Diff design

`diff_specs(old, new)` returns a sorted list of `Change` dataclasses:

```python
Change(
    endpoint="GET /quotes/{}",  # identity
    path="/quotes/{quoteId}",   # display path
    location="response",        # endpoint | param | request | response
    status_code="200",          # response only
    field="premium.breakdown[].amount",
    kind="field_removed",
    old="number", new=None,
    internal=False,
    info={"old_deprecated": None, "old_sunset": "2026-06-01", "new_required": None},
)
```

The diff reports:

- endpoints added/removed and deprecated flag changes
- params added/removed and type, required, enum, format changes
- request body added/removed and request-body required changes
- request fields added/removed and type, format, required, enum, deprecated,
  sunset changes
- response status codes added/removed
- response fields added/removed and the same field-level changes

Deliberate behaviors:

- **Topmost removals only.** Removing `premium.breakdown` also removes every
  field under it; the diff keeps only the parent change and drops the child
  removals. The impact matcher treats a parent change as covering all children,
  so `premium.breakdown[].amount` still catches the mobile app.
- **No severity here.** `diff.py` never decides whether a change is dangerous.
  Severity is a separate pass, so rules can change without touching the diff.
- **A changed endpoint's display path comes from the new spec**, so a parameter
  rename is shown but not treated as a contract change.
- **Unknown/opaque schema types** still produce a field entry. The classifier
  has a fallback warning rule, so nothing is silently dropped.

## 8. Classification design

`classify(change, today)` looks up `(scope, kind)` in a rule table and returns
`Classification(severity, rule, reason)`.

- **Scope** is one of `endpoint`, `param`, `request-body`, `request-field`,
  `response`, `response-field`.
- **Direction decides the default**:
  - Request: stricter is breaking. New required field/param/body, optional to
    required, removed enum value, type change.
  - Response: taking something away is breaking. Field removed, required to
    optional, type change. Removing a 2xx status code is breaking.
  - Adding optional request fields and adding response fields is safe.
- **Warnings** are changes that deserve attention but do not by themselves
  break a caller: deprecations, removed request fields (a client may still send
  them), new response enum values, new status codes, format changes, semver
  mismatch.
- **`x-internal`**: when an operation or field is internal and the rule says
  breaking, severity drops to `warning` and the reason says only the owning team
  uses it. Internal changes never block.
- **`x-sunset`**: a response field removal is breaking, unless the old field's
  sunset date is `<= today`, in which case it is a warning. A missing sunset
  means breaking.
- **Semver**: `check_version_bump` adds a warning when there is at least one
  breaking change and the major number did not increase. It never blocks.
- **Fallback**: an unknown `(scope, kind)` becomes rule
  `unclassified-change` with warning severity, never an exception.
- Every reason is one sentence and names the endpoint, field and/or consumer
  where applicable.

The full table lives in `docs/RULES.md` and `app/core/classify.py`.

## 9. Impact design

`compute_impact(classified, usages, provider)` returns:

```python
{
  "affected_consumers": [
    {"consumer": "mobile-app", "team": "Mobile", "affected": [
      {"endpoint": ..., "field": ..., "via": "reads",
       "severity": "breaking", "rule": "...", "reason": "..."}
    ]}
  ],
  "unused_breaking": [ ...breaking changes that hit nobody... ]
}
```

Matching only considers `breaking` and `warning` changes. A usage matches when
`provider` equals the diffed service and the usage endpoint identity equals the
change endpoint identity.

What "hits a consumer" means by change type:

| Change | Affected consumers |
| --- | --- |
| endpoint removed / deprecated | anyone calling the endpoint |
| new required param, optional to required param | anyone calling the endpoint |
| other param change | consumers that send that param |
| new required request field/body, optional to required | anyone calling the endpoint |
| request body removed | anyone calling the endpoint |
| other request field change | consumers whose `sends` overlap the field |
| response field change | consumers whose `reads` overlap the field, for 2xx responses only |
| response code added/removed | anyone calling the endpoint |

Field overlap is bidirectional: exact match, a change on a parent covers a
consumer reading a child, and a change on a child is visible to a consumer that
declared the parent.

`unused_breaking` is every breaking change that did not hit any usage. It is
reported but does not block.

### Stale references

Consumer files can be older than the current spec. `find_stale_references`
checks each usage against the provider's published versions in publish order:

- An endpoint is valid if it exists in the latest published spec.
- A sent reference is valid if it is a path parameter of the usage itself, a
  parameter of the endpoint, or a request field.
- A read reference is valid if it appears in any 2xx response field.
- The first version where a reference stops matching, after having matched in
  an earlier version, is reported as `since_version`. If it never matched, the
  first published version is reported. If the provider has nothing published,
  `since_version` is `None`.
- Unknown providers, endpoints and fields are returned as stale errors, never
  raised.

Stale references are errors on the consumer but never block a new publish:
the damage is already live, and blocking would freeze the provider.

## 10. Publish gate and overrides

`decide(classified, impact, version_warning)` is pure:

- Blocking = at least one affected item has `severity == "breaking"`.
- Warnings and unused breaking changes are included in `reasons`.
- A semver warning is included in `reasons`.
- No breaking or warning changes produces the message
  "No breaking or warning changes were found."
- Outcome is `blocked` or `published`.

Override rules (`override_attempt`):

- Only a `blocked` attempt can be overridden.
- `approved_by` must match the service's `owner_team`, case-insensitively.
- A non-empty written reason is required.
- The blocked version must still be higher than the current last published
  version.
- Overriding does not mutate the blocked attempt: it adds a second attempt with
  outcome `overridden`, copies the original changes/impact, and records
  approver and reason. The `SpecVersion` status becomes `overridden`.
- Overridden versions count as published for later comparisons, stale
  reference checks and metrics.

## 11. Persistence design

SQLite through SQLAlchemy 2.x. Tables are created on startup with
`Base.metadata.create_all`; there are no migrations.

```
Service 1---* SpecVersion
Service 1---* PublishAttempt
Consumer 1---* ConsumerUsage
```

| Table | Important columns |
| --- | --- |
| `services` | name (unique), owner_team, contact |
| `spec_versions` | service_id, version_label, raw_spec, normalized_spec (JSON), status, created_at |
| `publish_attempts` | service_id, version_label, outcome, compared_against, changes (JSON), affected_consumers (JSON), unused_breaking (JSON), reasons (JSON), approved_by, override_reason, error, created_at |
| `consumers` | name (unique), team |
| `consumer_usages` | consumer_id, provider, method, path, sends (JSON), reads (JSON) |

Design choices:

- **Snapshots instead of recomputation.** `SpecVersion.normalized_spec` is stored,
  so a changelog or diff between old versions does not depend on re-parsing the
  original YAML or on current code having the same rules.
- **Attempts are immutable history.** A blocked attempt is never rewritten; an
  override is a new row. The blocked row is what "caught" the change in metrics.
- **JSON columns for shape-shifting payloads.** Changes and impact are nested
  and only read as a whole by the UI/metrics; separate tables would add joins
  without a query need.
- **SQLite foreign keys are enabled per connection** with a `PRAGMA`.
- **`version_label` is not unique per service.** A blocked or rejected number can
  be reused; the newest `SpecVersion` for a label is used when reading.
- **No secrets in the repository.** Configuration comes from `DATABASE_URL`,
  `WEBHOOK_URL` and `SEED_KIT`; defaults are relative paths and the compose
  service name.

## 12. HTTP API design

All JSON routes are under `/api`. Every API error is
`{"error": "<message>"}` with a meaningful status code.

| Method/path | Purpose | Notable codes |
| --- | --- | --- |
| `GET /health` | health check | 200 |
| `POST /api/services` | register service | 201, 409 duplicate, 422 missing fields |
| `GET /api/services` | list services + latest published | 200 |
| `GET /api/services/{name}` | one service | 200, 404 |
| `POST /api/services/{name}/versions` | upload and publish a spec | 201 published, 409 blocked, 422 rejected |
| `GET /api/services/{name}/versions` | stored versions + status | 200 |
| `POST /api/services/{name}/impact` | proposed spec impact, saves nothing | 200, 422 invalid |
| `GET /api/services/{name}/diff?from=&to=` | classified diff between two stored versions | 200, 404 |
| `GET /api/services/{name}/changelog?from=&to=&format=json\|markdown` | changelog | 200, 422 bad format |
| `GET /api/services/{name}/attempts` | publish history | 200 |
| `POST /api/services/{name}/attempts/{id}/override` | override a block | 201, 403 wrong team, 409 not blocked, 422 no reason |
| `POST /api/consumers` | create/replace a consumer from YAML | 201, 422 invalid |
| `GET /api/consumers` | list consumers + usages | 200 |
| `GET /api/consumers/{name}` | consumer + stale references | 200, 404 |
| `GET /api/metrics` | aggregate protection metrics | 200 |

Upload, impact, diff and changelog accept an optional `today=YYYY-MM-DD` query
parameter so sunset behavior can be reproduced; it defaults to the current date.

Upload and impact accept either multipart `file` or a raw YAML/JSON request
body. Consumer upload accepts a multipart `file` or raw body.

The impact endpoint is read-only: it validates and normalizes in memory, diffs
against the latest published version, and saves nothing. This keeps
"what would happen?" separate from "make it happen" and is the basis for a future
CI check.

## 13. Web UI design

Server-rendered Jinja2 with HTMX. No npm, no bundler, no client state.

| Page/route | Contents |
| --- | --- |
| `GET /` | services, owners, latest published, last attempt, create-service form |
| `GET /services/{name}` | versions, attempt history, upload form, proposed-impact form |
| `GET /services/{name}/compare?from=&to=` | changelog grouped by endpoint |
| `GET /services/{name}/attempts/{id}` | changes, affected consumers, reasons, override form |
| `GET /consumers` | consumers with endpoint and stale counts, upload form |
| `GET /consumers/{name}` | usages, stale references |
| `GET /metrics` | dashboard numbers |

HTMX partials:

- `partials/upload_result.html` - outcome, counts, compared version, errors
- `partials/impact_result.html` - proposed version, summary, affected consumers,
  unused breaking changes, errors

Forms post to the HTML routes, which call the same `publish_version`,
`compute_impact` and `override_attempt` functions as the JSON API. Errors are
rendered inline in the page, not as raw JSON. Successful service creation and
override redirect (303) back to the service page.

Templates use `base.html` for the shared layout and a small CSS block; no
JavaScript is required beyond HTMX's form swapping.

## 14. Metrics design

`compute_metrics(attempts, stale_count)` returns:

- `total_attempts`
- `outcomes` (`published`, `blocked`, `rejected`, `overridden`)
- `breaking_caught` - breaking changes on blocked attempts that hit at least one
  consumer
- `consumers_protected` - distinct consumers with a breaking hit on a blocked
  attempt
- `overrides`, `overridden_breaking` - how many breaking changes went out
  through an override
- `breaking_with_no_consumer` - breaking changes that shipped without a known
  consumer, so the rules or consumer files may need attention
- `stale_references`
- `block_rate` - blocked / total attempts

Counting rules that matter:

- A blocked attempt still counts as "caught" after it is overridden, because the
  blocked row is preserved. The override row only adds the override cost.
- The same breaking change caught in two attempts counts twice, because it was
  caught twice (for example quote `2.0.0` and `2.1.0` both compared with
  `1.1.0`).
- `breaking_with_no_consumer` is counted only for versions that actually
  shipped (`published` or `overridden`).

## 15. Seeding, runtime and configuration

`docker compose up --build`:

- `app` builds from `Dockerfile`, listens on `8000`, mounts `./data`.
- `webhook-receiver` builds from `kit/webhook-receiver`, listens on `9000` inside
  compose, exposed on `${WEBHOOK_PORT:-9900}`.
- `SEED_KIT=1` makes startup seed the kit when the `services` table is empty.
- `WEBHOOK_URL` defaults to `http://webhook-receiver:9000`; the app does not send
  notifications yet.

Seeding order matters:

1. create the three services with their owner teams
2. load all consumer YAML files, so impact has consumers before any spec
3. publish each service's specs in filename order

Seeding is skipped if any service exists, so restarts do not duplicate data. To
reseed, delete `data/` and restart.

## 16. Testing design

| Layer | What it covers |
| --- | --- |
| `tests/unit` | normalize (refs, renames, lists, recursion, allOf), diff kinds, classifier rules in both directions, impact matching, changelog, metrics |
| `tests/integration` | FastAPI TestClient with a temporary SQLite database: health, upload, publish, block, override, consumers, changelog API, pages |
| `tests/acceptance` | the real kit files in publish order, expected outcomes and metrics |

The acceptance test fixes `today = 2026-11-18`, so `x-sunset` behavior is stable.
It asserts quote-api `2.1.0` is compared with `1.1.0`, policy-api `2.0.0`
publishes with warnings, claims-api `1.1.0` is rejected then reused, and
payments-svc gets a stale `legacyCode` reference at policy-api `2.0.0`.

Current suite: 124 tests, all passing.

## 17. Key trade-offs and alternatives rejected

| Decision | Alternative | Why this choice |
| --- | --- | --- |
| Normalize first, diff field paths | Diff raw YAML or component trees | Renaming/moving components must be invisible; the problem statement requires it |
| Path params by position, name not stored | Compare param names | `GET /quotes/{id}` and `{quoteId}` are the same URL for clients |
| Pure `app/core` over dicts | Put logic in routes/models | Fast, deterministic unit tests on the kit; no DB needed |
| Store normalized snapshots | Recompute on every read | Changelog between old versions stays stable and cheap |
| Compare with last published/overridden | Compare with last attempt | PO decision: blocked versions never existed for consumers |
| Block only when a real consumer breaks | Block every breaking change | Avoids blocking providers for unused fields; consumer files can be incomplete |
| Keep blocked attempt and add override row | Mutate blocked attempt to published | Preserves exactly what was blocked, by whom it was released, and why |
| Stale references are errors but never block | Block on stale references | The break is already live; blocking cannot undo it and would freeze the provider |
| Build the diff/classifier in-house | Add `oasdiff` or another dependency | The stack is fixed, and consumer-aware classification is the product, not the diff |
| Jinja2 + HTMX | React/SPA | No build step, less time spent on UI plumbing |

## 18. Known limitations and future design

### Limitations

- No authentication or roles. The override check compares the typed approver
  name with the owner team; it is a workflow guard, not a security boundary.
- `min`/`max`/`pattern` constraints are not compared.
- `oneOf`/`anyOf` are opaque fields; `allOf` is merged.
- External `$ref`s, OpenAPI 3.1 and Swagger 2 are rejected.
- `cookie` parameters are ignored; only path, query and header parameters are
  contract surface.
- Only one media type per body/response is considered.
- No pagination on services, versions, attempts or consumers.
- No database migrations.
- The seeded policy-api `2.0.0` result depends on the seed date relative to the
  kit's `x-sunset` dates.

### Future work, in likely order

1. **Webhook notifications.** Send publish/block/reject/override events to
   `WEBHOOK_URL` (provider team and affected consumer teams). Needs an event
   payload shape, retries and an outbox so a failed notification cannot fail a
   publish.
2. **CI check endpoint.** `POST /api/services/{name}/check` reusing the impact
   path, returning a stable block decision and machine-readable reasons without
   saving an attempt. This is the `POST /impact` design plus a conventional
   exit/result shape.
3. **Auth for overrides.** Real identity and a role that can approve, with the
   owner team as a scope rather than a typed string.
4. **Constraint and composition comparison.** Add `minimum`, `maximum`,
   `pattern`, `oneOf`/`anyOf` to the normalized model and rule table.
5. **Consumer UI form.** Register sends/reads field by field, in addition to
   YAML upload.
6. **Migrations and pagination** once the data model stops changing.

## 19. Design checklist

- Can a component be renamed with zero changes? Yes: refs are resolved and field
  paths are the identity; acceptance test covers quote 1.0.0 to 1.1.0.
- Can a path parameter be renamed with zero changes? Yes: path params are keyed
  by position.
- Is every change explained? Yes: every `Change` gets a severity, rule id and
  reason, including unknown kinds.
- Does a block require a real consumer? Yes: `decide` blocks only on affected
  breaking changes; unused breaking changes are reported as warnings.
- Is the history complete? Yes: rejected, published, blocked and overridden
  attempts are all stored; overrides are additive.
- Can the core be tested without infrastructure? Yes: `app/core` has no FastAPI,
  SQLAlchemy or DB imports.
