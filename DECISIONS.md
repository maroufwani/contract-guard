# Decision log

Every key decision or assumption I made, with the time I made it. Newest at the bottom. "PO" means the answer came from the product owner, "mine" means it was my call.

## 18 Nov 2026

**[4:15] Picked Contract Guard as the problem (mine)**
It is mostly backend logic (diffing and rules), which I can test well in 6 hours, and the kit already gives me realistic test data.

**[4:30] Stack: Python, FastAPI, SQLite, Jinja2 + HTMX, pytest (mine)**
I'm most comfortable in Python, and FastAPI gives me the JSON API and the HTML pages in one app. SQLite needs no extra container. HTMX means no frontend build step, so I don't lose time on the UI.

**[4:30] Kit stays unchanged and goes in its own first commit (mine)**
The evaluators test against the original kit, so the first commit only has kit/ and I never touch it after that. If I need modified specs for tests, I copy them into tests/fixtures/.

**[5:00] Core logic is pure functions in app/core (mine)**
Normalize, diff, classify, impact and changelog take plain dicts and don't know about the database or the web. This makes them easy to unit test on the kit files.

**[5:10] Diff the normalized spec, not the raw file (mine, from the problem statement)**
All $refs get resolved and schemas become field paths in the kit notation before comparing. A renamed component should show zero changes.

**[5:10] Endpoints matched by method + path with parameters by position (mine)**
mobile-app and notifications-svc still call GET /quotes/{id} after quote v2 renamed it to {quoteId}. It's the same URL for the client, so it should match.

**[5:10] Scope: all 10 must-haves, then webhooks, then a CI check endpoint (mine)**
Left out: auth, AI migration guide, external $refs, OpenAPI 3.1 and Swagger 2, full oneOf/anyOf, a field-by-field consumer form. Reasons are in docs/PLAN.md.

**[5:15] Default assumptions until the PO answers (mine, will update)**
Breaking change with no affected consumer: warn. Override: owner only, with a reason, logged. Compare with: last published version. x-internal: never blocks. Removal after x-sunset: warning, before: breaking. New response enum value: warning. New status code: warning. Path parameter rename: not a change. Semver mismatch: warning. Rejected version number: can be reused. Stale consumer reference: warning. Notifications: provider team and affected consumer teams.

**[5:25] Removing a field after its x-sunset date is a warning even if a consumer still reads it (mine, until PO answers Q5)**
opencode pointed out my plan said policy v3 is blocked, but my own sunset rule says it should warn. I went with the date rule, because otherwise x-sunset does nothing. payments-svc still reads legacyCode, so it becomes a stale reference and gets notified. Policy v3 now publishes with warnings.

**[5:25] No migrations, tables created on startup (mine)**
SQLite plus create_all is enough for a 6 hour project. Alembic would only cost time.