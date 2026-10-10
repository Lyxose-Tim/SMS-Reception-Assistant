# Historical source snapshot — original Linux evidence

Copied before the optimization implementation. Its original “this turn” and
test counts refer to that historical run, not the current Windows/UI checks.
See [current report](TEST_REPORT.md) for new execution evidence.

# Verification report — October 9, 2026 (service upgrade)

**A: local checks passed. B: mocked provider checks passed. C: real end-to-end
validation pending / 真实联调待完成. Not production-ready.** No real account,
sender, participant, registration, API send, delivery, public deployment or
platform publication was used. An API-capable implementation is not a proven
live integration.

## Executed environment and runs

| Component | Actually observed |
|---|---|
| OS | Linux 6.18.44 x86_64; glibc 2.39 |
| Python / SQLite | CPython 3.12.14 / SQLite 3.53.1 |
| Official Twilio SDK | 9.11.2 in an isolated QA virtual environment |
| Node | 24.19.0, JavaScript syntax verification only |
| Browser | Cloud Chrome, private internal preview; exact browser version not surfaced |
| New native captures | Actual JPEG viewport, 1348 × 926 |
| Service deployment platforms | Linux local code tested; systemd/Nginx installation and Windows/macOS service behavior not tested |

The original **86-test suite was rerun and passed before changes**. It is a
baseline, not live evidence. After the upgrade and final fixes, the delivered
source was tested again:

```sh
python -m unittest discover -s tests -v
```

SDK-enabled final root run: **218 passed, zero failed, zero skipped, 17.695s**.
Exact log: `artifacts/test-run.txt`. An independent run also passed 218.
Default Python without optional SDK/requests: **218 discovered; 172 passed,
46 explicitly skipped**, independently executed after the final backend fixes.
A skipped official-SDK test is not a pass. `node --check web/app.js` passed
after the final login-message edit.

| Test module | Passed in full SDK run |
|---|---:|
| Original domain `test_engine` | 49 |
| Original local HTTP `test_http` | 18 |
| Original demo Twilio adapter `test_twilio_adapter` | 19 |
| Durable sending `test_outbox` | 48 |
| Official SDK/REST adapter `test_provider` | 32 |
| Protected service HTTP `test_service_http` | 14 |
| Durable inbound pipeline `test_service_pipeline` | 17 |
| Operations/recovery `test_operations` | 21 |
| **Total** | **218** |

The assembled ZIP was also extracted into a fresh temporary directory and smoke-tested: default loopback demo returned HTTP 200, kept real outbound disabled and created seven synthetic contacts; service mode with a disposable generated operator hash returned HTTP 401 before login, created zero contacts and schema version 2. Both processes stopped cleanly with no provider configuration. ZIP CRC and exclusion checks passed. This is local package verification, not deployment.

## Layer A — locally executed

Real local HTTP servers and disposable SQLite files were used. These tests
do not use a real Twilio account or external phone.

| Requirement | Observed local evidence |
|---|---|
| Existing architecture/data | Original 86 regressions remain green; a backed-up existing demo DB was opened with the additive upgrade: integrity ok, 8 contacts and 1 pending request preserved. Existing data is not shipped in the ZIP. |
| FAQ scope | Approved entries limited to five supported topics; unapproved/missing/unknown facts hand off; malicious instructions cannot modify consent or confirm a booking. |
| Appointment requests | Single/multiple turns, incomplete/expired fields and legitimate contact emails; completed rows remain pending owner confirmation. Email words person/service do not renew permission or trigger takeover. |
| Auth/CSRF/input isolation | Unauthorized state denied; password session/logout/TTL, CSRF and origin/Host protections, bounded/strict inputs; simulator/reset/voice not exposed by service mode. |
| Webhook intake | Valid synthetic SDK signatures accepted, invalid/missing/changed/all-extra-param/raw-query cases rejected; empty TwiML only; no synchronous send/resource GET in initial intake. |
| Durable processing | Duplicate and concurrent intake, trusted resource age/identity/direction/body checks, read-only lookup failures, restart recovery and persisted verification errors. |
| Single-event failure | ValueError/TypeError/KeyError become saved no-send handoffs; another customer's valid event still processes; no exception content leaked or global pause for these domain errors. |
| Takeover | Automatic business output paused; explicit resume needed; manual message idempotence and all permission/sending gates remain. STOP continues to apply during takeover. |
| Withdrawal | STOP before normal processing cancels queued business output; zero provider send calls for suppressed cases. START/UNSTOP requires fresh verified allowed profile; YES, HELP, INFO, ordinary replies/calls do not restore. No app duplicates platform acknowledgements. |
| Sending gates | Recipient allowlist, sender, dry-run, pause, emergency file, service window, carrier/local block, minute/day/message/segment caps, oversize automatic-output handoff. |
| Idempotence/status order | One durable intent per event; duplicate/out-of-order callbacks, callback before REST response, wrong nonce/SID/recipient and terminal-status regression checks. No exactly-once carrier claim. |
| Ambiguous API outcomes | Timeout/connection/5xx freeze as uncertain; no blind second SMS POST. Definite 429 rejection gets at most three attempts and retains actual error 20429. Known-SID reconcile checks exact resource, no-SID stays frozen. |
| Restart recovery | Queued tasks survive and recheck gates; interrupted submitting becomes uncertain. Exclusive Linux DB lock prevents a second service process. |
| Retention/deletion | Old content/unfinished booking cleanup, stable hashed withdrawal and replay suppression; daily/minute quota metadata survives deletion and restart. |
| Backup/restore | SQLite backup includes committed WAL, integrity/FK checks, offline locks, rollback snapshot, restore pause + independent review gate, current safety union. Latest STOP timestamp survives an old backup; a previously unseen intermediate START remains blocked. |
| Communication boundary | No external SMS; dry-run leaves service intents held, not fabricated delivered. Default demo remains simulated and refuses live activation. Mock error/unavailable/deadline regressions remain demo-only. |

## Layer B — provider adapter, mocked only

The **actual official RequestValidator and SDK exception/client configuration**
were exercised locally with synthetic tokens, signatures and SIDs. SDK client
message-create/fetch/account/number methods were mocked. Tests inspect explicit
From/To/body/status callback, timeout/no-auto-retry configuration, sanitized
4xx/21610/20429 and ambiguous 5xx behavior, inbound resource proof and outgoing
status retrieval. They also check a genuine documented status-callback subset
that omits To: the nonce/account/from/SID correlation remains required.

No Twilio test-credential API request was made. No real account resource was
read. No provider/carrier callback was received. Mock accepted/sent/delivered
values are fixtures, not delivery evidence. Current official test-credential
documentation itself states no real numbers or status callbacks are involved;
new Console cannot create legacy test credentials.

A/B overlap in the 218-test suite; their counts are not added again.

## Browser evidence and limitations

This turn operated the running default demo in the private preview: Business
hours produced the configured simulated answer; the appointment view visibly
showed pending owner confirmation. A new actual capture is
`06-updated-pending-request.jpg`. Five earlier captures are retained as
**original synthetic demo evidence**, not a fresh live acceptance run.

A separate empty, dry-run service was launched in the internal preview with
a disposable generated operator hash and no provider credentials. The real
browser displayed the login gate, hid records and simulator, and after a UI
fix showed a clean human-readable sign-in page rather than the normal 401
machine code. Capture: `07-service-operator-login.jpg`. No credential or
customer data appears in this capture. The temporary QA launcher was removed
and the normal preview script restored before packaging; preview stopped.

**Authenticated service dashboard click-through was not completed in the
browser.** Authentication and protected writes were exercised over real local
HTTP in the automated tests; this is not a substitute for visual review of
every authenticated screen. The cloud browser's credential handoff was not
invoked for this disposable local test. Recheck those controls on the authorized
client host before live activation. There is no fabricated signed-in screenshot.

## Layer C — not run

| Required evidence | Actual state |
|---|---|
| Account/number ownership, SMS capability, target region, trial/custom-body capability and registration | Unknown / not inspected |
| Secure credentials and approved external HTTPS deployment | Not configured or deployed |
| Test phone control and participant consent | Not supplied/verified |
| Concrete send manifest, currency and spending approval | Not completed |
| Real inbound message and verified provider resource | No evidence |
| Real SMS API acceptance / MessageSid | No evidence |
| Real sent / delivered callbacks | No evidence |
| Participant actual phone receipt | No evidence |
| Real STOP/START/HELP acknowledgements and carrier restoration | No evidence |
| Real failure outcome | No evidence; mocks only, no opted-out probe |

Fill `REAL_E2E_PLAN.md` only after readiness and explicit test approval.
API accepted, sent, delivered and participant receipt must be recorded
separately. A delivery receipt is not an SMS read receipt. Redact identifiers,
messages and screenshots. The empty `artifacts/real-e2e-status.json` explicitly
records the unexecuted state.

## Remaining limits and sales scope

One business/number; five supported approved FAQ topics; simple English rules,
unknowns hand off. No real LLM, marketing, voice/missed-call followup in service,
CRM, calendar reservation, guaranteed delivery, unlimited usage or multi-user
roles. Default reply window is conservative application policy, not a legal
opinion. Current application counts cannot cap provider confirmations, other
apps, rental/registration/taxes or final carrier billing.

Small STOP-versus-submission and carrier non-idempotent POST races remain;
already accepted SMS cannot be recalled. Outages/delays may need human review;
no-SID ambiguous submissions do not offer a resend shortcut. Service uses one
Linux process and local SQLite; no scale/load/penetration or long-term uptime
claim. HMAC/dedup metadata has no automatic expiry and remains pseudonymous;
backup encryption and scheduling are customer operations work. Signed webhooks
provide integrity, not a universal replay timestamp.

Deployment templates were source-reviewed, **not installed, nginx-tested or
exercised through a real TLS/proxy**. Credentials, privacy treatment, retention
schedule, monitoring/alerting, country/provider registration and live fees
require separate acceptance. Current trial restrictions can prevent custom
business SMS; no assumption of free custom messages is made.

The prior platform drafts' broad FAQ quantities, future auth wording and
unqualified duplicate-text claims need revision. See
`SERVICE_SCOPE_REVIEW.md`. Only local engineering/customization plus a
conditional separately approved integration milestone is currently defensible.
Do not publish the service or claim production readiness before C and the
additional deployment/operational reviews. No video was recorded; original
English scripts and shot lists are retained as simulated-demo materials.
