# Self-review

## What works

All 10 must-haves work on the kit. Specs are validated and normalized, so the quote v1 to v2 reorganisation shows zero changes. The diff covers endpoints, params, request and response fields (nested and lists), types, required flags, enums and status codes. Every change gets a severity and a plain English reason, and the rules depend on direction (request vs response), x-internal and x-sunset. Impact matches changes against what each consumer sends and reads, and the gate blocks only when a breaking change hits a real consumer. Seeding the kit gives the results I worked out by hand before writing any code: 7 published, 4 blocked, 1 rejected, and payments-svc flagged for legacyCode. There are 118 tests (unit, integration and acceptance) and they all pass in Docker.

## What doesn't work or is missing

I skipped the optional parts: webhook notifications and the CI check endpoint. The receiver still starts with docker compose, but the app doesn't send anything to it. There is no login, so an override just checks that "approved by" matches the owner team name. min/max/pattern constraints and oneOf/anyOf are not compared. Seeding uses today's date, so before 1 Nov 2026 policy 2.0.0 would be blocked instead of published (the sunset hadn't passed yet). Tests can't run directly on my Windows laptop because Application Control blocks a compiled library, so they run in Docker.

## What the AI got wrong

It said the required region param was added in quote v4 when it is actually in v3. Its first try at the skeleton wrote 11 files in one go, hit the output limit and saved nothing. It counted notifications-svc as "protected" when it only got a warning. The stale reference check from one step ignored overridden versions until a later step noticed. It also went ahead and built the version check a step early, without being asked.

It also caught my mistakes: my prompt put the path parameter name inside the normalized spec, which would have made {id} and {quoteId} differ, and my expected diff for claims was missing a second endpoint that uses the same schema. I only trusted its output after reading the tests myself, and I told it never to change my expected values without asking.

## What I'd do next

Webhook notifications to the affected teams, a CI check endpoint, comparing constraints and oneOf/anyOf, real login for overrides, and letting consumers register usage from the UI instead of uploading YAML.
