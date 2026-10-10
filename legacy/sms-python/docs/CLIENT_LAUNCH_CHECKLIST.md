# Acceptance and readiness checklist for the current SMS scope

Current Upwork draft: **$350 / 10 days / 1 agreed-scope revision**, one business,
one qualified client-owned Twilio SMS number and at most five approved FAQ entries.
The controlled real send/receive test is included when ready and specifically
authorized. **Real E2E remains unexecuted; this checklist is not approval or
production-ready evidence.** No LLM, voice, CRM, calendar, marketing or multi-tenant
extension is in scope.

## Local implementation and acceptance

- [ ] Approve business hours/timezone, areas, services, pricing and requests FAQ;
  evaluate supported English phrasing and unknown-question handoff.
- [ ] Confirm appointment requests remain pending owner confirmation; a second
  request collects fresh details and no calendar/availability result is invented.
- [ ] Validate deterministic STOP-first and send-first thread ordering; no later
  business submission after committed withdrawal, and no carrier-recall promise.
- [ ] Validate unknown verified inquiry review, source audit and original deadline;
  review grants human-only service replies, never marketing or automatic scope.
- [ ] Validate test-allowlist policy and explicitly configured inbound-service policy,
  along with STOP/carrier/contact/queue/emergency/limits/restore gates.
- [ ] Validate trustworthy proxy login identity, spoofed forwarding rejection,
  malformed Cookies/password hashes and session/CSRF safeguards.
- [ ] Use the project-local Windows environment and canonical original test IDs;
  close each original SDK/platform skip with actual evidence or a specific retained
  limitation. Keep Linux assertions and label them unrun when applicable.
- [ ] Validate real Windows child-process exclusion, normal stop/drain, path
  identities, backup/restore failures, retention and permissions without mocked
  locks, system/ACL changes or killing unrelated processes. Customer-data ACL
  remains a separate blocking acceptance condition.
- [ ] Complete login and core Inbox/Requests/Consent/Activity/Outbox/profile UI
  through PowerShell Start → project venv → actual app.py, using a marked synthetic
  dry-run DB. A fixture cannot establish the first positive startup. Re-run and
  record every assertion/screenshot; old 41 assertions are historical.

## Number, participant and exact real-test authorization

- [ ] Verify client account/number ownership, SMS capability, custom-body support,
  target countries/route, geographic permissions and required registration.
- [ ] Verify participant phone control and narrow purpose permission; review
  STOP/START/HELP ownership and provider confirmations for the actual sender.
- [ ] Finalize sender/recipient masks, exact automatic/operator/participant texts,
  encoded segments, count caps, currency/cost cap, time window and shutdown owner.
- [ ] Approve least-privilege account read/configuration access, temporary credentials,
  HTTPS webhook exposure and real sends in the concrete manifest. Purchases,
  persistent deployment/access and publication require their own authorization.
- [ ] Keep the controlled test in `test_allowlist`; do not activate formal inbound
  service as a shortcut for sending outside the approved test participant.
- [ ] Keep the simulator/admin private; review signature preservation and trusted
  resource identity/age. Never weaken a gate to obtain a pass.
- [ ] Record API acceptance, sent, delivered and participant receipt separately;
  stop on ambiguous outcomes. Do not probe opted-out phones or blind retry.

## Operations and cleanup ownership

- [ ] Name client/operator responsible for encrypted backup/storage permissions,
  retention scheduling, restore exercises, monitoring, incident response and costs.
- [ ] Set separate old-backup, provider-record and snapshot cleanup policies; online
  deletion does not clean those copies. Document retained pseudonymous safety records.
- [ ] Agree controlled-test cleanup date/owner, stop sending, review uncertain and
  pending work, remove agreed temporary data/backups, revoke temporary access and
  credentials, and close temporary webhook exposure.
- [ ] Deliver source without DBs, backups or secrets. Keep stable HMAC key custody
  and restore review procedures clear. Ongoing support requires a separate agreement.
- [ ] Preserve historical Linux and independent Windows run provenance; log only
  new runs as current implementation evidence. Do not claim production readiness.
- [ ] Protect external baseline and preceding current/UI evidence; write new
  artifacts under unique Windows run directories, recording actual app/redirector
  PIDs, OS creation times, commands, UTC and independent local timezone.

See [REAL_E2E_PLAN.md](REAL_E2E_PLAN.md), [SERVICE_SCOPE_REVIEW.md](SERVICE_SCOPE_REVIEW.md)
and [OPERATIONS_ZH.md](OPERATIONS_ZH.md). Actual current provider and platform
requirements must be checked on the approved route before external action.
