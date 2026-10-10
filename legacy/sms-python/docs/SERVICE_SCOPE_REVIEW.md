# Current service scope and evidence review

Reviewed October 9, 2026. The user's current Upwork draft defines the commercial
scope below. Earlier demo/package ideas do not override it. This review concerns
local files; an Upwork/Guru account or published listing has not been inspected.
No listing has been published by this implementation.

## Current fixed scope

**$350 / 10 days / 1 agreed-scope revision** for one business and one qualified,
client-owned Twilio SMS number. Include:

- Business configuration and at most **five approved FAQ entries** in the existing
  supported hours/areas/services/pricing/requests topics; unknown questions hand off.
- Appointment requests **pending owner confirmation**, with fresh details for each
  request; no calendar reservation or business-availability guarantee.
- Operator login, human takeover and relevant manual replies, withdrawal records,
  source/deadline and action audits, inbox/outbox delivery views.
- Source code, configuration example, dependency list, setup/operations instructions,
  offline regression evidence and a login-after-authentication dashboard walkthrough.
- An **included, explicitly authorized controlled real SMS send/receive test** when
  account/number/registration/participant/route/budget prerequisites are satisfied.
  The test is currently unexecuted. Missing prerequisites produce a documented
  blocked milestone, not a fabricated completed live installation.

Ten days and one revision are the current draft terms; do not replace them with
legacy package terms. Start after complete approved inputs and agreed readiness
prerequisites. External registration delays and delivery outcomes cannot be
promised; agree any resulting delivery-date change before making a new commitment.
Third-party number, messaging/carrier, registration, hosting, storage/backups and
applicable tax/platform charges are paid by the client. This is one-time scoped
work; ongoing maintenance requires a separate agreement.

LLM/real AI, voice/missed-call service, CRM, calendar booking, marketing, multi-tenant
features, public hosting as an already completed deliverable, unlimited usage and
lead/revenue guarantees are excluded. Demo-only simulations remain labeled as such.
No production-ready, guaranteed delivery or exactly-once carrier-delivery claim
is warranted, even after an approved controlled SMS test.

## Implementation and evidence boundaries

The existing Python + SQLite architecture remains. The optimization addresses
STOP/submission ordering, auditable human review of verified service inquiries,
the approved requests FAQ, fresh appointment state, an explicitly configured
inbound-service recipient policy, and proxy/auth input robustness. See
[IMPLEMENTATION_REVIEW.md](IMPLEMENTATION_REVIEW.md) for acceptance scenarios.

Service operation remains one process, one worker and local SQLite. The Windows
alignment adds project-local setup and a real foreground `app.py` service entry,
normal owned stop and native operations acceptance; see [WINDOWS_GUIDE.md](WINDOWS_GUIDE.md).
Windows customer-data operation remains candidate while private-storage/ACL
acceptance is blocked. Linux templates remain an option, not a new host-validation
claim. The preceding run stays in [TEST_REPORT.md](TEST_REPORT.md); new Windows
runtime/SDK/results must be recorded separately, including original skip IDs.
The original Linux report recorded 218 passing tests; the independent historical
Windows run recorded 218 discovered / 197 passed / 1 failed / 20 errored / 0 skipped.
These are different historical runs, not new execution results or carrier evidence.
Previously observed normal manual queueing, takeover, multi-turn appointments, FAQ
and log UI do not invalidate the independently reproduced defects. The changed
version still needs its own offline and authenticated UI acceptance evidence.

Before real testing, approve the exact masked sender/recipient, registration and
route, participant and purpose, finalized bodies and encoded segment counts,
message/currency/cost caps, time window and action/access scope in
[REAL_E2E_PLAN.md](REAL_E2E_PLAN.md). Real testing is included commercially;
this does not pre-authorize purchases, credentials, public exposure, sending,
persistent deployment or publication. Approved STOP takes precedence over earlier
send authorization; accepted provider messages cannot be recalled.

## Backup, cleanup and maintenance responsibility

The delivery includes existing backup/verification, offline restore/review,
retention and deletion commands, their documentation and a synthetic acceptance
exercise. It does not install a scheduler or encrypted backup service automatically.
The client or named maintenance operator owns encrypted storage/access controls,
ongoing backup and retention schedules, restore exercises, provider-data and old
backup cleanup, monitoring, costs and incident handling. Agree ongoing support
scope separately; there is no implied subscription or 24/7 support.

For a controlled real test, name the cleanup owner and deletion date before the
run; turn sending off, review pending/uncertain work, remove agreed temporary
records/backups, revoke temporary access/credentials and close temporary public
exposure. Online deletion does not remove old backups, provider records or disk
snapshots. Pseudonymous withdrawal/dedup/quota safety records remain by design,
with stable HMAC keys and separate documented responsibility. Do not advertise
complete erasure or use an old restore to reset opt-out/spending protection.
