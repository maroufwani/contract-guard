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

**[5:40] Host Python is 3.15, Docker is 3.12 (mine)**
opencode found my laptop actually has Python 3.15.1. Tests run locally on 3.15 and the app runs on 3.12 in Docker. SQLAlchemy had to be 2.0.58 because 2.0.40 crashes on 3.15.


**[5:45] PO replied to questions 1 to 11 (PO)**
Confirmed: compare with the last published version. Left to me: block/warn workflow, overrides, x-internal and x-sunset meaning, sunset rule, enum and status code rules, path parameter rename. So all my defaults from 5:15 stay for those. Full answers are in docs/PLAN.md.

**[5:45] Version numbers only move forward on a successful publish (PO + mine)**
The PO said the version is bumped only on a successful publish with 0 errors for dependents. I read that as: a number belongs to the service only once it is published, so rejected and blocked numbers can be reused (claims 1.1.0 case), and a new upload must have a higher semver than the last published one, otherwise it is rejected. Breaking change with only a minor bump stays a warning. All the kit versions go up, so none of them get rejected because of this.

**[5:45] Stale consumer references are errors, not warnings (PO + mine)**
The PO said a reference that no longer matches is "an error to the previous deployed version". So I show it as an error on the consumer and name the published version it stopped matching (for example payments-svc reading legacyCode after policy 2.0.0). It does not block new publishes, because the damage is already live and the provider can't undo it by being blocked. Changed from my 5:15 default (warning).

**[5:45] Question 12 was never sent, notifications are my call (mine)**
I forgot to include it in the email. Going with webhooks to the provider team and the affected consumer teams, and a CI check endpoint after that if there is time.

**[6:00] Tests run inside Docker, not on Windows (mine)**
Windows Application Control on my laptop blocks the rpds-py DLL that openapi-spec-validator needs, for every version. So I run pytest in the app container. That is also Python 3.12, the same as what evaluators run.

**[6:00] Path parameters have no name in the normalized spec (AI caught my mistake)**
My Step 3 prompt put the parameter name inside path:0, which would make {id} and {quoteId} differ. opencode pointed it out and dropped the name. The display path still shows the spelling.

**[6:00] Not comparing min/max/pattern constraints (mine)**
Quote v4 has minimum/maximum on ncbPercent. The must-haves don't list constraints and time is short, so the diff ignores them. Listed as a limitation in the README.

**[6:05] My expected diff for claims was incomplete (mine, AI was right)**
The Claim schema is used in both POST /claims 201 and GET /claims/{claimId} 200, so amountApproved and assessorNotes change in both places. opencode stopped and showed me the real output instead of changing my expected values. I fixed the tests.

**[6:05] Array items don't get their own object entry (AI suggestion, accepted)**
The array entry (like premium.breakdown) already has the required flag, so a separate entry for its items would just duplicate it.

**[6:15] x-internal keeps the rule id but drops to warning (AI suggestion, accepted)**
So the changelog still shows what kind of change it was, and the reason says it is internal.

**[6:15] Unknown change kinds are a warning, never a crash (AI suggestion, accepted)**
If the diff ever produces something the rule table doesn't know, it should show up, not take down the publish.

**[6:20] These changes hit every caller of an endpoint, not only matching fields (mine + AI)**
New required request field or param, request body becoming required, optional to required, endpoint removed, endpoint deprecated, response code added or removed. partner-portal is hit by requestedBy in policy v4 even though it never sends it, which is exactly why.

**[6:20] Stale references for unknown providers have since_version null (AI suggestion, accepted)**
If the provider has nothing published yet, there's no version to blame.

**[6:30] Overridden versions count as published everywhere (AI caught a gap)**
The stale reference check from Step 6 only looked at status "published", so an overridden version was ignored. opencode fixed it in Step 7.

**[6:30] An override record keeps the changes and impact it released (AI suggestion, accepted)**
So the history shows exactly which breaking changes went out on purpose and who approved them.

**[6:30] Kit is seeded on startup when the database is empty (mine)**
Someone running docker compose up sees real data straight away. Consumers go in before specs, otherwise nothing would be blocked.

**[6:35] Changelog order: endpoints by worst severity, then breaking, warning, safe inside each (AI suggestion, accepted)**

**[6:40] Consumer upload in the UI is YAML file only (AI choice, accepted)**
Consumers come as kit-format YAML files anyway. The API still takes a raw body.