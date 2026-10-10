# Actual running screenshots

This file describes historical captures from the original delivery. They are not
new optimization acceptance or real SMS evidence. Any fresh authenticated UI
walkthrough and new captures are identified separately in `TEST_REPORT.md`.

These five captures were taken from the working local demo through a private
internal browser preview on October 9, 2026. They contain synthetic data, with
showcase privacy enabled. No image-generated or fabricated application screen
was used. The persistent Demo / Simulated badge is visible.

| File | Demonstrated behavior |
|---|---|
| `01-faq.jpg` | Approved opening-hours answer in the conversation |
| `02-pending-request.jpg` | Four collected fields; pending owner confirmation |
| `03-consent-gate.jpg` | No verified purpose consent; no missed-call follow-up |
| `04-opt-out-history.jpg` | Consent evidence and STOP status change |
| `05-human-handoff.jpg` | Automation paused, Your team reply, explicit resume |

Original captures in `artifacts/screenshots/` are JPEG, **1348 × 926** pixels.
Export copies in `artifacts/platform-images/` are JPEG, **1348 × 1011**, exactly
4:3. Neutral padding preserves the full screen: no labels or evidence were
removed. Each is under 10 MB. These are review assets; account upload and
platform acceptance were not tested. Guru numeric media requirements remain
unverified. Consult the dated official references before publishing.

The FAQ source is `data/business.json`; a missing fact causes clarification or
handoff. The first capture does not itself show the configuration editor.
The recordings script is in `docs/DEMO_SCRIPT.md`; **no video file was recorded**.

## Service-upgrade captures

Two additional actual browser captures were taken during the original service-upgrade delivery: `06-updated-pending-request.jpg` shows that version's running synthetic demo; `07-service-operator-login.jpg` shows its empty dry-run service behind the login gate. Both are 1348 × 926 JPEGs with no customer credentials/data. Authenticated service screens were not captured in that historical browser run; no real SMS or carrier outcome is shown. Existing five platform exports remain original demo media.
