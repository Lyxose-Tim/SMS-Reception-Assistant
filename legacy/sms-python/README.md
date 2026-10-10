# Harbor & Home — SMS Reception Assistant

An existing Python + SQLite application for one business and one qualified,
client-owned Twilio SMS number. The default fictional portfolio demo remains
**DEMO / SIMULATED**. The service uses a durable inbox/outbox, approved rules and
an authenticated operator dashboard. **Real end-to-end testing remains unexecuted;
this is not production-ready.** No real messages, account setup, purchases,
public deployment or platform publication are authorized by local development.

## Current local acceptance

The latest independent Windows acceptance selected one final run: **382 collected,
380 executed, 377 complete passes, 3 partial cases and 2 whole-case restrictions;
0 failures/errors/uncaught thread exceptions**. Fresh formal-service browser checks
cover 41 core + 4 entry + 7 restore assertions, with 43 additional checks in this
acceptance round. Historical 315 and 283 results are preserved separately.
See [final acceptance](docs/FINAL_ACCEPTANCE_REPORT.md),
[current test index](docs/TEST_REPORT.md) and [platform conditions](docs/PLATFORM_SUPPORT.md).

The current-user **SMS Reception Assistant** Desktop shortcut has been physically
double-click tested. It launches a fresh synthetic local demo, reuses its owned
instance, and provides **Exit local demo**. It never enables real sending or opens
a customer database. See [Windows quick start](docs/WINDOWS_QUICK_START.md).
The formal service entry remains separate and authenticated.

## Current delivery scope

The current Upwork draft is **$350 / 10 days / 1 agreed-scope revision**. It
includes one business profile, at most **five approved FAQ entries** in the
supported hours/areas/services/pricing/requests topics, appointment requests
**pending owner confirmation**, human takeover and relevant manual replies,
opt-out/audit views, source code, setup instructions, offline acceptance evidence
and an explicitly authorized controlled real SMS send/receive test. The live
test is included in this scope; its concrete external actions still require
separate authorization and a ready account/number/route/participant/budget.
Unresolved prerequisites are reported as blocked, not as completed testing.

The client owns the Twilio account and number and pays third-party charges:
number rental, messaging segments/carrier fees, registration, hosting,
storage/backups and applicable taxes/platform fees. Delivery starts after the
approved business inputs and agreed prerequisites are supplied; registration
approval or carrier delivery is not guaranteed within that period. Ongoing
maintenance is separately agreed. There is no LLM, voice, CRM, calendar booking,
marketing, multi-tenant feature, unlimited usage or revenue guarantee in this offer.
See [scope review](docs/SERVICE_SCOPE_REVIEW.md) and
[current platform drafts](docs/PLATFORM_DRAFTS.md).

## Windows project environment and retained Linux path

The Windows project environment uses the existing Python 3.13 interpreter and the
declared Twilio 9.11.2 dependency. Windows Setup installs the 16 exact versions in
`requirements-windows.lock.txt`; Linux keeps `requirements-twilio.txt` unchanged.
The original Linux target remains Python 3.12;
new results for either platform require their own evidence. From
`D:\SMS Reception Assistant`, use PowerShell 7.5+ and
`scripts/windows.ps1 -Action Setup` for the isolated `.venv`. `start.bat` opens
the original demo with that interpreter, forcing real sending off. On Linux,
use `python3 app.py` or `sh start.sh`. Open
**http://127.0.0.1:8765/**; `--port 8766` selects another port.
The demo uses synthetic records in `runtime/demo.sqlite3`; never add real data
or credentials. Service mode has no simulator.

The Windows acceptance entry is `scripts/windows.ps1 -Action Start`: it runs the
project `.venv\Scripts\python.exe app.py` in service mode, on loopback with real
sending disabled, then waits in the foreground. An existing DB must carry the
synthetic acceptance marker. This is a real app entry, with native instance
ownership, not a fixture server. Its run records distinguish the Windows venv
redirector PID from the actual app PID. `-Action Stop -RunDirectory ...` requests
the app's normal close and waits for drain/lock release; it never kills by port or
process name. Setup/start/test/operations use new, isolated run directories.

Both paths retain one process, multiple bounded HTTP threads, one send worker and
local SQLite. Windows customer-data use remains **candidate/blocked on access-control
acceptance**; synthetic startup alone is insufficient. Linux deployment templates
remain an option with separate host acceptance. No WSL/Docker, background service,
execution-policy, PATH, ACL or system change is installed by these scripts.
Use [Windows guide](docs/WINDOWS_GUIDE.md) for exact safe commands and limitations.
See [startup](docs/START_ZH.md), [deployment preparation](docs/DEPLOYMENT_ZH.md)
and [operations/recovery](docs/OPERATIONS_ZH.md).

For a separately authorized Linux preparation, the declared dependency
is in `requirements-twilio.txt`. After private operator-password and privacy-key
configuration, run service mode with `ENABLE_REAL_SEND=false`. A new service DB
is empty and separate from the demo. `.env.example` lists safe defaults; the
application does not load `.env` automatically. Deployment templates in `deploy/`
remain uninstalled and unverified on a real host.

## Receiving, replying and stopping

- Signed Twilio intake durably deduplicates SMS and returns empty TwiML. A worker
  verifies the provider resource's account, number, direction, body and age.
  Only the outbox worker sends through Twilio REST.
- Approved FAQ answers and appointment collection stay within a current service
  inquiry. Unknown questions become a handoff. The operator selects a verified
  inbound source and explicitly confirms an unclassified service inquiry before
  replying. The source and deadline are audited; review grants human replies
  only and cannot extend the original window or create marketing permission.
- Appointment-rule questions use the approved requests FAQ. Each new appointment
  collects fresh details; completed requests do not silently supply old fields.
  Every appointment remains pending owner confirmation.
- Default recipient policy is `test_allowlist`. A controlled real test uses its
  approved recipients. The explicitly configured `inbound_service` policy permits
  relevant replies to verified fresh customer inquiries without requiring each
  customer to be a test recipient. It never authorizes arbitrary proactive sends.
  The dashboard shows the policy and cannot activate it.
- STOP intake, withdrawal, queue cancellation and final send submission share the
  local ordering boundary. Successful acceptance of a new STOP follows the atomic
  withdrawal transaction commit; after that commit, new business submissions are
  blocked. An in-flight send may complete before STOP commits. Waiting is bounded:
  a timeout returns a retryable failure, without acknowledging successful intake.
  Request arrival alone does not establish withdrawal, and waiting STOP handlers
  have no guaranteed priority. Provider-accepted SMS cannot be recalled.
  Single-instance native locking excludes another service process; several
  processes/workers sharing the DB are unsupported.
- Takeover pauses automation until explicitly resumed. Manual sends retain local
  and carrier opt-out, contact/business limits, source/deadline, pause/emergency,
  daily/rate/segment and restore-review gates. START/UNSTOP follows verified provider
  restoration rules; ordinary messages, HELP, YES and calls do not restore consent.
- Dry-run is default. A timeout/connection failure/5xx with unknown submission
  outcome is quarantined as `uncertain`; it is not blindly retried. Application
  quotas are not a provider billing hard cap. API acceptance, sent, delivered and
  participant receipt remain separate observations; delivery is not a read receipt.

## Regression evidence and next acceptance

[TEST_REPORT.md](docs/TEST_REPORT.md) preserves the preceding optimization run.
New Windows alignment runs, including actual service entry, native process/operations
and authenticated UI, receive a [separate report](docs/WINDOWS_TEST_REPORT.md) and
run-isolated evidence.
The original Linux report's **218 passed** is historical evidence. The independent
historical Windows run was **218 discovered: 197 passed, 1 failed, 20 errored,
0 skipped**; POSIX locks, permissions, symlinks and file operations account for
platform differences and must be distinguished from business regressions. Neither
history is a new optimization rerun. Use only newly recorded results to assess the
changed version; mocked SDK work is not real Twilio API or handset evidence.

Windows uses `scripts/windows.ps1 -Action Test`; the runner records canonical test
IDs, collection differences and each original skip's closure. Original SDK cases
must execute with the installed declared version; genuine Linux-only evidence
remains separately labeled. On an approved Linux host, the complete suite remains
`python -m unittest discover -s tests -v`. Neither platform inherits the other's
results. Login and core UI acceptance through the actual app entry are required
before real testing. See [implementation review](docs/IMPLEMENTATION_REVIEW.md).

The unexecuted [real E2E plan](docs/REAL_E2E_PLAN.md) requires concrete ownership,
registration, route, participant permission, exact texts, message/segment counts,
currency/cost cap, time window and action authorization. Account access, secret
configuration, purchases, public exposure, real sending, persistent deployment
and publication are separate approval boundaries. Passing a controlled run does
not establish production readiness.

## Data, backups and maintenance

SQLite snapshot/verification, offline guarded restore, restore review, retention
and contact deletion utilities are included. Backups are not encrypted by this
application. Retention settings do not schedule deletion. The client or named
maintenance operator owns ongoing encrypted backup/access controls, scheduling,
restore exercises, old backup/provider-data cleanup and incident handling. A
separate maintenance agreement defines any ongoing work; no subscription is implied.

A live test ends with sending disabled, queue/uncertain review, agreed deletion
of temporary data and backups, credential/access revocation, and exposure cleanup.
Online deletion does not delete old backups, provider records or disk snapshots.
Pseudonymous STOP/dedup/quota safety metadata is retained; HMAC keys must stay
stable. Document its separate retention responsibility rather than claiming all
traces were erased. Source deliveries exclude databases, backups and secrets.
Project source license: MIT; provider/platform terms apply independently.
