# CedarHQ Progress

## Completed

- Clean repository created at `/root/cedarhq`.
- Product requirements, architecture, route map, database entities, and status machines documented.
- Initial SQLite schema added.
- First vertical slice implemented with database-backed auth, onboarding, sandbox checkout, order timeline, ops review actions, document vault, compliance calendar, and audit logs.
- Founder overview upgraded to a database-backed command center with persistent workspace navigation, evidence-backed formation progress, next actions, company record, plan coverage, transparent billing, compliance deadlines, recent documents, and support status.
- Product workspaces implemented for a source-cited AI assistant, multi-account bookkeeping, tax preparation/status workflows, and Shopify/Amazon commerce analytics.
- Staff tax queue implemented with evidence-backed sandbox submission, acceptance, and rejection controls.
- Public Dokploy test deployment created at `https://cedarhq.144.76.96.3.sslip.io`.
- CedarHQ source pushed to `https://github.com/aamirmursleen/cedarhq`.
- Dokploy application `cedarhq-saas` is running from the official `python:3.12-slim` image with source bind-mounted from `/root/cedarhq` and persistent SQLite data mounted at `/data`.

## Simulated

- Google sign-in uses `SandboxGoogleAuthProvider`.
- Checkout uses `SandboxCheckoutProvider`.
- State filing/EIN actions use `SandboxFormationProvider`.
- Financial connections and transactions use the local `sandbox_ledger` adapter.
- Shopify/Amazon connections and daily metrics use deterministic sandbox commerce adapters.
- Tax submission/authority responses are simulated and generate explicit sandbox evidence.
- AI answers use a deterministic workspace-rule adapter until production AI and source-corpus credentials are available.
- Email verification/password reset are written to the in-app outbox and displayed locally in development.
- Generated documents and evidence are stored in SQLite text records for MVP; production should use encrypted object storage.

## Blocked Integrations

- Real Google OAuth credentials.
- Stripe account, products, prices, and webhook secret.
- Transactional email credentials.
- Legal filing, registered-agent, mailroom, banking, accounting, tax, Shopify, Amazon, and AI provider credentials.

## Validation

- Passed: `python3 -m py_compile app.py cedarhq/*.py`
- Passed: `python3 -m unittest discover -s tests` (7 tests)
- Passed: live HTTP smoke test for founder login, onboarding autosave, sandbox checkout, order timeline, staff login, and first ops transition.
- Passed after dashboard update: server startup and authenticated founder overview smoke check; company progress, record, service coverage, compliance, and document sections rendered successfully.
- Passed after product workspace update: authenticated runtime smoke for bookkeeping connection and CSV, tax questionnaire and full founder/staff status flow, Shopify/Amazon analytics and CSV, AI question with source citation, and sandbox tax evidence download.
- Passed: `VM115_BUILD_MODE=docker vm115-build .` built the Dockerfile on VM115 successfully.
- Not run after the latest product-workspace update: automated unit tests through VM115, because `vm115-build` routes build contexts and does not execute arbitrary Python test commands.
- Passed after Dokploy deployment: public HTTPS GET for home and signup, founder login, authenticated founder pages (`/app`, onboarding, documents, compliance, bookkeeping, taxes, analytics, assistant, billing, support), staff tax operations page, admin login and `/admin` redirect to operations.
- Blocked: visual browser verification because the `agent-browser` CLI/tool is not available in this environment.
- Blocked reference inspection: the supplied Moonpush PNG remains in `uploading` state without a completion signal, so its encrypted image payload is not downloadable yet.
- Completed reference inspection: the second Moonpush PNG decrypted successfully and confirmed the requested five-product navigation model (assistant, formation, bookkeeping, taxes, analytics).
- Not run locally: package builds, Docker builds, full Playwright/browser suites.
