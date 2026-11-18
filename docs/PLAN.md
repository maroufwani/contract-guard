# Contract Guard - Plan (Checkpoint 1)

Written by: Marouf Mushtaq
Started: 18 Nov 2026, 4:00PM. Last updated: 5:20PM

## The problem in my own words

Teams at a company like Acko talk to each other through HTTP APIs. When the Quotes team changes their API, they usually don't know exactly which other teams use which fields. So a change that looks small, like renaming a field in the response, can break the mobile app in production and nobody finds out until users complain.

Contract Guard is a registry where every team uploads each new version of their OpenAPI spec before releasing it. It compares the new version with the previous one, works out every change, decides which changes can break callers, checks which consumer services actually use those parts, and stops the publish if someone would break. The hard part is that the comparison has to be about what the API really accepts and returns, not how the YAML file is written. Teams move things into components and rename them all the time, and that alone should never be reported as a change.

## Questions I asked the PO

I sent questions 1 to 11 by email at 5:11 PM and got the reply at 5:41 PM. The PO answered some of them directly and left the rest to me ("direction of your product is upon you"), so for those I went with my default. I forgot to send question 12, so that one is also my call.

1. If a breaking change doesn't hit any registered consumer, should it block or only warn? PO: my call. I'm going with warn, because consumer files can be incomplete but blocking everything is annoying. This also fits the PO's answer to Q9, which talks about "0 errors in dependency", so what matters is whether a dependent breaks.
2. Can a block be overridden, by whom, and is it recorded? PO: my call. The provider owner can override with a written reason, and it goes into the history.
3. Do we compare against the last published version or the last attempted one? PO: last published version. A blocked version never existed for consumers.
4. What do x-internal and x-sunset mean? PO: "something you have to figure out". My reading: x-internal means only the owning team calls it, so changes there never block. x-sunset is the date after which a deprecated field can be removed.
5. Is removing a field allowed after its sunset date, before it, or both? PO: my call. After sunset it is only a warning, before sunset it is breaking.
6. Is a new enum value in a response breaking? PO: my call. Warning, since a strict client might crash on an unknown value.
7. Is a new response status code breaking? PO: my call. Warning.
8. Is renaming a path parameter (/quotes/{id} to /quotes/{quoteId}) a change? PO: my call. No, the URL that clients call is the same.
9. Should we enforce semver (breaking change must bump major)? PO: the version number only moves forward on a successful publish with 0 errors for dependents. So my reading is that a version number belongs to a service only once it is published, and a new upload must have a higher version than the last published one, otherwise it is rejected. A breaking change released as only a minor bump is still just a warning.
10. Can a rejected version number be reused? PO: covered by the answer to Q9. Yes, because a rejected or blocked version never took the number.
11. How do we treat consumer references that no longer match the spec? PO: it is an error against the previous deployed version. So I show it as an error (not a warning) on the consumer, and say which published version it stopped matching. It never crashes anything, and it does not block new publishes, because that damage is already live and blocking would freeze the provider team for something they can't undo.
12. Which going-further item matters most, and who should be notified? Not asked, so my call. Webhook notifications to the provider team and the affected consumer teams.

## What I found in the kit

I went through all 12 spec versions and the 4 consumer files by hand, and these became my acceptance tests. Quote v2 only reorganises components and renames {id} to {quoteId}, so it should publish clean. Quote v3 removes premium.breakdown[].amount (mobile-app reads it) and should be blocked. Quote v4 only adds optional fields. Policy v2 deprecates legacyCode and holder.phone with sunset dates, so only warnings. Policy v3 removes both. legacyCode was past its sunset date so that is only a warning, and holder.phone is removed before its sunset, which is breaking, but nobody reads it, so it also only warns. So v3 publishes with warnings, and payments-svc then shows an error because it still reads legacyCode. Policy v4 adds a required request field and removes VEHICLE from a request enum, which breaks partner-portal, and it is only a minor version bump. Claims v2 has a typo in a $ref (ClaimDocumnt) so it must be rejected as invalid. Claims v3 fixes it and reuses the number 1.1.0. Claims v4 makes amountApproved optional in the response, which breaks mobile-app.

Some consumer files are stale. mobile-app and notifications-svc still call GET /quotes/{id}, so I have to match path parameters by position and not by name. One open question for me: if quote v3 is blocked and the team then uploads v4, v4 is compared with v2, so it still carries the removal from v3 and should also be blocked. I think that is the correct behaviour.

## Similar products I looked at

oasdiff is an open source OpenAPI diff tool that has named rules with severity levels, and I'm borrowing that idea for my classifier. openapi-diff gives a readable changelog grouped by endpoint, which is how I want my changelog to look. Optic runs a check in CI and fails the build on breaking changes. Pact is consumer-driven contract testing and has "can-i-deploy", which is close to my publish gate. Apollo GraphOS checks schema changes against real client usage, which is the same idea as my consumer impact but for GraphQL. Buf does the same for protobuf wire compatibility. The main thing none of the simple diff tools do is combine the diff with who actually uses each field, and that is the part I want to get right.

## What I will build and what I will leave out

I will build all 10 must-haves first: service and spec registry with validation, $ref resolution, the diff, the classifier with plain English reasons, consumer registration, impact, the publish gate, changelog and history, a simple UI, and a metrics page. After that, webhook notifications through the kit's receiver, and then a CI check endpoint if I still have time.

I'm leaving out login and auth, the AI-written migration guide, external $refs, OpenAPI 3.1 and Swagger 2, full oneOf/anyOf support, and a field-by-field form for consumers (consumers are uploaded as YAML in the kit format instead). These are either not needed for the demo or too big for 6 hours. The diff and classifier are the core of the product, so I would rather have those correct and well tested than have more screens.

## Design sketch

```
 upload spec (API / UI)
        |
   validate  --invalid-->  rejected, saved in history
        |
   normalize (resolve $refs, flatten into field paths)
        |
   diff against last published version
        |
   classify each change (breaking / warning / safe + reason)
        |
   impact (match changes against consumer sends/reads)
        |
   publish gate --blocked--> saved in history, notify teams
        |
   published, saved in history, notify teams
```

The core functions (normalize, diff, classify, impact, changelog) will be plain Python functions with no database or web code inside them, so I can unit test them directly on the kit files. The API and the UI both call the same service layer on top of them.

Stack: Python, FastAPI, SQLite with SQLAlchemy, Jinja2 templates with HTMX for the UI, pytest for tests. It runs with one command, docker compose up, which starts the app on port 8000 and the kit's webhook receiver.

## How I will decide if a change is breaking

The rule I'm following is that it depends on direction. In a request, the consumer is the one sending data, so making things stricter breaks them: a new required field, an optional field becoming required, an enum value removed, a type changed. In a response, the consumer is the one reading, so taking things away breaks them: a field removed, a required field becoming optional (they might get null), a type changed. Adding optional request fields or new response fields is safe. Removed endpoints and methods are always breaking. Anything marked x-internal never blocks. Every change gets a short reason in plain words, for example "mobile-app reads premium.breakdown[].amount from POST /quotes and it was removed".

A change is matched to a consumer if the consumer sends (for request changes) or reads (for response changes) that path or anything under it. Endpoints are matched by method and path with parameters compared by position.

## Data model

Service: id, name, owner team, contact.
SpecVersion: id, service, version label, raw spec text, normalized spec (JSON), status (published, blocked, rejected, overridden), created at.
PublishAttempt: id, service, version label, outcome, compared against which version, list of changes with their severity, list of affected consumers, override reason, time.
Consumer: id, name, team.
ConsumerUsage: consumer, provider, method, path, sends list, reads list.

## Main screens

1. Services list, with owner and latest published version.
2. Service page: all versions and all publish attempts with their outcome, plus the upload form.
3. Compare page: pick two versions, see the changelog grouped by endpoint, coloured by breaking, warning or safe.
4. Impact page for a proposed version: which consumers are affected and by which changes, and an override button.
5. Consumers page: list and upload a consumer YAML.
6. Metrics page.

## Test plan

Unit tests for the core functions: $ref resolution (including the rename in quote v2), each diff type (fields, nested objects, lists, types, required, enums, status codes, parameters), each classification rule in both request and response direction, and path matching by position.

Integration tests through the FastAPI test client with a temporary database: register a service, upload valid and invalid specs, register consumers, publish, get blocked, override, read history.

Acceptance tests that load the real kit files in publish order and check the expected outcome of every version, as listed in "What I found in the kit". If these pass, I know the product works on the evaluators' data. All tests run with pytest, and I will put the exact command in the README.

## Metrics

On the metrics page I will show: number of publish attempts, how many were published, blocked, rejected and overridden, number of breaking changes caught before release, number of consumers protected (distinct consumers on blocked attempts), number of breaking changes released anyway through an override, and number of stale consumer references. The most important one for me is breaking changes caught, because that is the whole point of the tool. Overrides are also important to watch, because if people override all the time then the rules are wrong.

## Rough time plan

Hour 0 to 0:45: this plan, PO questions, repo setup. Hour 0:45 to 1:30: skeleton, docker compose, upload and validation. Hour 1:30 to 3:00: normalize, diff and classifier with unit tests (the most important part). Hour 3:00 to 4:00: consumers, impact, publish gate, history. Hour 4:00 to 5:00: changelog, UI, metrics. Hour 5:00 to 5:30: webhooks. Last 30 minutes: README, self-review, and a clean run from a fresh clone.

## Risks

The biggest risk is the normalization going wrong (for example circular $refs or a renamed component showing up as a removed field), because everything else depends on it. That's why I'm doing it first with tests on the quote v1 to v2 case. The second risk is running out of time on the UI, so I'm keeping it plain HTML with HTMX and no frontend build step.
