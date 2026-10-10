# Controlled real SMS end-to-end plan — not executed

Prepared October 9, 2026. **Stage C has not been performed or authorized.** This is a reviewable plan for a later test with one client-owned business sender and one consenting, controlled test recipient. It does not permit account creation, account access, purchases, top-ups, public deployment, phone calls or real SMS now.

The current **$350 / 10 days / 1 agreed-scope revision** offer includes this
authorized controlled send/receive test for one business and one qualified,
client-owned Twilio SMS number, with at most five approved FAQ entries, pending
owner-confirmation requests, human takeover/replies, opt-out/audit views and source
code. The test is not an extra-price live-integration add-on. Account/registration,
participant, exact-text, segment/count, route/cost and action approvals remain
prerequisites. The client pays third-party fees. Missing prerequisites are recorded
as blocked; they do not permit a completed-test or production-ready claim.

Stages A/B cover implementation and local/mock integration checks. Stage C below establishes narrow real-world evidence for the approved account, route and participants only; even a passing C is not general legal compliance, production readiness or universal carrier delivery.

## Required test manifest

Fill this in before requesting activation. Store full phone/account values privately in secure configuration; published reports retain masks and evidence IDs only. Do not paste secrets into chat.

| Field | Required value — currently unknown |
|---|---|
| Run ID and dates | `[approved-run-id]`, exact start/end time and timezone |
| Account owner / person authorized to approve | `[name and authority]` |
| Account / Console flow | `[masked SID suffix]`, new/legacy, paid/trial and verified custom-content capability |
| Business sender | `[country code … last 2–4 digits]`; actual client-controlled SMS-capable sender, not a synthetic demo number |
| Sender type / registration | `[local/toll-free/other]`, required approval evidence and campaign/use case |
| Test recipient | `[country code … last 2–4 digits]`; one actual SMS device under participant control |
| Recipient control holder | `[participant name, verification method and time]` |
| Participant permission | `[evidence ID]`, approved test purpose/texts, consent source, time, STOP/START understanding, withdrawal contact |
| Sender/recipient countries and carrier | `[target route]`; do not infer these from the computer's timezone |
| Approved business data | `[configuration hash/version]`, fictional demo clearly identified to the participant or approved client facts |
| Purpose scope | Explicit conversation/manual test purposes; missed-call or marketing permission is not inferred |
| Public webhook access | `[HTTPS URL mask]`, owner, restricted gateway topology and exact callback endpoints |
| Application version and local tests | `[commit/package digest]`, executed test log and unresolved limitations |
| Spend currency and cap | `[account billing currency] [approved amount]` — no estimate until the route/account is known |
| Message/segment cap | Proposed maximum 12 participant-originated SMS / 12 inbound segments; 12 outbound messages / 48 outbound segments, including provider confirmations |
| Shutdown controller | `[operator]`, tested transport kill switch, queue pause and credential revocation path |
| Data retention / cleanup | `[retention/deletion date]`, access holder, named cleanup owner, temporary backup/provider-record policy, credential/exposure cleanup and redacted evidence location |

Account verification is necessary but does not substitute for participant permission. Check the actual account's custom-body restrictions and capability; an assigned trial number or available inbound webhook is insufficient proof that our replies can be sent. If the actual account cannot send these exact custom messages, stop and report the blocked capability. An upgrade, number purchase or registration is a separate decision.

## Approval boundaries before Stage C

Each row needs an explicit decision for the specified concrete action. Permission for code development does not approve these effects, and approving one row does not approve the others.

| Action | Concrete proposal to review | Current authorization |
|---|---|---|
| Read account capability/settings | Named account, exact read scope and access duration; use secure connector/configuration rather than shared secrets | Not granted |
| Receive/retain credentials | Least-privilege scope, secure storage, expiration/deletion and owner | Not granted |
| Public webhook exposure | Specific host, gateway route, exposure period, authentication/TLS and shutdown procedure; local admin/demo routes remain inaccessible | Not granted |
| Buy number / register / upgrade / top up | Item, recurring charges, amount/currency and payment owner | Not granted; never bundled with send approval |
| Enable real sending | Specific run manifest, single recipient allowlist, texts and segment/spend caps | Not granted |
| Each business-to-participant send | Corresponding step and finalized body/encoded segment count below | Not granted |
| Participant inbound texts | Participant agrees to the exact test sequence and their carrier charges | Not granted |
| Provider keyword acknowledgements | Actual sender's STOP/START/HELP response configuration and possible carrier confirmations | Not granted |
| Real voice/missed-call test | Separate recipient/destination, call duration/cost and consent scope | Not proposed for this SMS-only C run |
| Persistent deployment/access | Separate service, operator accounts, retention and billing terms after the test | Not granted |
| Publish media or platform listing | Exact sanitized copy/assets and destination | Not granted |

An approval can cover the enumerated SMS sequence as a batch only if it explicitly names every send, provider acknowledgement and caps. Any changed text, additional message/participant, restart after an uncertain outcome or extended exposure requires review of that changed action. Withdrawn consent overrides earlier send approval.

## Before enabling the transport

1. Pass and record the complete offline suite on the approved Linux test path,
   including operations, deterministic STOP/send ordering, unknown-inquiry human
   review, requests FAQ/fresh booking state, both recipient policies, auth/proxy
   robustness and existing provider-failure/signature/idempotence gates. Complete
   the authenticated core UI walkthrough on the changed version. Distinguish Windows
   development results and Linux-only requirements; count only executed tests.
2. Verify account custom SMS capability, sender ownership/type/registration, route/country settings, verified recipient restrictions and participant evidence. Obtain a route-specific fee review. Do not use synthetic seeded numbers for real sending.
3. Keep simulator and local admin private. Expose only a separately reviewed HTTPS webhook gateway. Configure exact public URLs and official SDK signature validation; verify trusted resource ownership, direction, body and age.
4. Select `RECIPIENT_POLICY=test_allowlist`, set the one approved test recipient,
   conservative segment/message/spend caps and a narrow run window. Remove other
   recipients from this run. The formal inbound-service policy is not permission
   to add participants or send outside this manifest. Verify business sending
   enabled does not alone activate live transport.
5. Preview finalized automatic replies, operator text and provider confirmations. Keep each inbound text at one segment; the current application default is three outbound segments per message, stricter than this plan’s four-segment review ceiling; count Unicode and sender/STOP disclosures. Stop if any body exceeds these proposed limits.
6. Confirm STOP/START/HELP acknowledgement ownership. With signed `OptOutType`, record the provider response without another application response. For any sender lacking reliable keyword-state callbacks, resolve how local suppression receives withdrawal before testing.
7. Demonstrate the kill switch and ambiguous-result quarantine without real traffic. Do not enable SDK automatic SMS-create retries. Record the exact application/configuration version used.

## Proposed sequence with exact participant/operator texts

The date is a **synthetic appointment preference**, not a real booking. Update it only in the reviewed manifest if the run occurs after that date. Automatic replies must be previewed from the frozen business configuration and approved before the corresponding step. Expected meanings below are acceptance assertions, not fabricated provider transcripts.

| Step / direction | Exact input or action | Expected result and evidence | Maximum planned outbound messages |
|---|---|---|---:|
| 1a. Participant → business, first inquiry | `Are you insured?` | A new contact's unknown question becomes a saved handoff with no invented answer. Record verified inbound source and its original reply deadline. | 0 |
| 1b. Operator → participant | `Harbor & Home Cleaning: A team member will review your insurance question. We have not confirmed an insurance policy in this message. Reply STOP to opt out.` | Select step 1a's verified source and explicitly confirm it is a service inquiry. One human-only reply intent within the original deadline; no automatic or marketing permission. Record source/audit/SID/status and actual receipt. | 1 |
| 1c. Operator, then participant → business | Explicitly resume automation, then send `What are your opening hours?` | Resuming alone sends no SMS. The fresh hours inquiry produces one approved hours reply; record source/provider status and actual receipt. | 1 |
| 2. Participant → business | `I need standard cleaning in Oakwood on 2026-10-16 at 10 AM. Contact me on this number.` | One request row with service, area, desired time and controlled recipient contact. Reply explicitly says pending owner confirmation; no slot/quote is confirmed. | 1 |
| 3. Participant → business | `Do you guarantee removal of every stain?` | No invented guarantee. Unknown-policy escalation and human-handoff state are saved. Current service code saves the handoff without an automatic acknowledgement; no unknown-policy reply is expected. | 0 |
| 4. Operator → dashboard | Enable takeover explicitly. | Audit record shows automation paused. No SMS is generated by clicking takeover. | 0 |
| 5. Participant → business | `Can a person review my request?` | Inbound saved while takeover remains active; no automatic text or provider send call. | 0 |
| 6. Operator → participant | `Harbor & Home Cleaning: Your request is pending owner confirmation. No appointment or final quote is confirmed. Reply STOP to opt out.` | Select the relevant verified appointment inquiry; review/confirm it if no existing scoped basis applies. Explicitly approved manual send only; original deadline and all send gates remain effective. Automation remains paused. Collect SID/status and participant receipt. | 1 |
| 7. Operator → dashboard | Explicitly resume automation. | Audit records resumption. No SMS from the resume action. | 0 |
| 8. Participant → business | `HELP` | Provider-defined help response if supported/configured. Record exact source/body; HELP must not create/recover consent. No duplicate app reply for provider-managed HELP. | 1 |
| 9. Participant → business | `STOP` | Local withdrawal/ledger updated. Provider-owned confirmation, if any, is recorded separately. No application send-API call to this recipient to probe provider blocking. | 1 |
| 10. Participant → business | `What services do you offer?` | Inbound recorded; routine reply blocked. Show **zero send-API invocations** after STOP, not merely a failed send. This text does not restore permission. | 0 |
| 11. Operator → dashboard | Try the local manual-reply gate with `Harbor & Home Cleaning: This reply must remain blocked after STOP.` | Local intent/action reports suppression before transport; zero provider SMS-create calls. This is a gate check, not an approved external message. | 0 |
| 12. Participant → business | `START` | Record provider opt-in and recipient restoration evidence; restore only eligible prior scoped permissions. Preserve business/contact blocks, rates and takeover state. No duplicate app acknowledgement. Some toll-free routes may issue two provider/carrier confirmations. | 2 |
| 13. Participant → business | `What services do you offer?` | One approved services reply only if every gate remains satisfied. Save all evidence; if a gate blocks it, record that outcome without overriding the gate. | 1 |
| 14. Operator → system | Disable live transport, stop/pause pending sends and end the run. | Final pending/ambiguous queue review, segment/billing evidence; named owner completes agreed temporary-data/backup cleanup, credential revocation and temporary-exposure shutdown. | 0 |

Planned inputs total **10 participant SMS**. Maximum expected outbound message objects are **9**, including the reviewed first-inquiry human reply, HELP/STOP and up to two START confirmations. Proposed hard review ceiling adds reserve only: **12 inbound segments + 48 outbound segments = 60 combined segments**. Reserve is not permission to send extra messages. Preview the finalized bodies and encoded segments before approval. Do not exceed the ceilings; stop if provider confirmations cannot be bounded, any automatic body is longer than allowed, or caps/fees cannot be measured reliably. Sender verification OTPs, number rental, registration, hosting and any setup traffic are outside this sequence and require separate cost/permission review.

No actual fee is inferred from “100 trial SMS,” package prices or raw message count. Proposed budget formula after selecting the route is: inbound segments × applicable inbound rate + outbound segments × applicable outbound/carrier rate + applicable fixed/setup charges, adjusted only for verified account entitlements. Record dated source rates and account currency before proposing a numeric cap; this plan currently leaves the amount unset.

## Duplicate, failure and delivery evidence

- Use mocks for error 21610, provider 4xx/5xx, timeouts, malformed callbacks and out-of-order statuses. A test-credential API response may supplement mock evidence if explicitly available/authorized, but produces no real SMS or delivery callback.
- Do not induce a live failure by messaging an arbitrary, unallocated or wrong phone number, or by probing an opted-out device. A real provider failure may be documented only if observed during this approved controlled sequence, or under a separately reviewed test using an actually controlled/authorized state. No failure outcome is guaranteed.
- If a real send produces an ambiguous timeout/5xx, quarantine it and stop. Reconcile account resources/callbacks with the specific intent; no blind retry. SMS-create POST is non-idempotent. Local duplicate-event protection is not an exactly-once delivery guarantee.
- Duplicate callback replay and invalid-signature checks remain local/mock unless a separately enumerated replay of this run's own webhook is approved. Replaying it must create zero additional sends.
- Evidence levels stay separate: **API accepted/queued** (provider created the resource), **sent** (carrier accepted), **delivered** (delivery report), **participant received exact text** (participant's explicit observation). None is an SMS read receipt; do not use `read` as an SMS success requirement.
- For absent/late delivery callbacks, report the last known status and participant observation independently. Do not manufacture `delivered` from elapsed time or receipt of the API SID.

## Result template — empty until a real run

For every step record: run/step ID; UTC/local time; masked from/to; consent evidence ID and current gates; message body hash/redacted body; inbound SID; local intent ID; outbound SID; SMS-create attempt count; signed callback verification outcome; each observed status/time; participant confirmation; segment count/fees when populated; operator/shutdown actions; deviations and cleanup.

Human review also records the selected verified inbound event, original inquiry
time/deadline and human-only basis; later confirmation cannot reset the deadline.
STOP/carrier and other send restrictions override earlier permission. Test cleanup
does not claim complete erasure: online deletion leaves old backups/provider records
and disk snapshots unless their separate policy is fulfilled, and pseudonymous
STOP/dedup/quota safety records remain. The client/named maintenance operator owns
ongoing encrypted backups, scheduled retention, restore exercises and support under
separately agreed terms. No scheduler, persistent deployment or maintenance service
is automatically installed by approval of this test.

The STOP boundary is successful atomic withdrawal commit/acceptance, not the raw
request's arrival. An in-flight send may finish first; waiting STOP handlers have
no guaranteed priority. A bounded wait timeout produces a retryable webhook failure
without a success acknowledgement; retain the retry/acceptance evidence. After the
withdrawal commits, no new business submission is allowed. Carrier-accepted SMS
cannot be recalled.

Current outcome: **not run — account, recipient, route, approval and budget unresolved**. A later report must name its exact account/route/date limits and distinguish failed, passed, blocked and unverified cases. Passing this controlled C run still leaves public-hosting security, client-country legal review, retention/privacy, long-term reliability and production operator access unverified. Voice and model integration are outside this service scope.
