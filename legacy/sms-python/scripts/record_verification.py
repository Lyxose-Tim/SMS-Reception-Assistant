"""Summarize current evidence, keeping original and platform runs distinct."""
import json
import re
import sqlite3
from contextlib import closing
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
ARTIFACTS = ROOT / "artifacts"
UI = ROOT / "output" / "playwright"
result = json.loads((ARTIFACTS / "offline-regression-current.json").read_text(encoding="utf-8"))
phases = []
for index in range(1, 5):
    match = re.search(r"### Result\s*\n(\{[^\n]+\})", (UI / f"ui-phase{index}.txt").read_text(encoding="utf-8"))
    if not match:
        raise RuntimeError("UI phase has no successful result")
    phases.append(json.loads(match.group(1)))
summary_path = UI / "ui-acceptance-summary.json"
summary = json.loads(summary_path.read_text(encoding="utf-8")) if summary_path.exists() else {}
summary.update({"phases": phases, "assertions_passed": sum(phase["passed"] for phase in phases),
    "synthetic_only": True, "real_e2e_executed": False, "browser_closed": True,
    "fixture_server_stopped": True})
info = json.loads((UI / "server-info.json").read_text(encoding="utf-8"))
if Path(info["database"]).exists():
    with closing(sqlite3.connect(Path(info["database"]).as_uri() + "?mode=ro", uri=True)) as database:
        database.row_factory = sqlite3.Row
        summary["appointments"] = [dict(row) for row in database.execute("SELECT service,area,desired_time,contact,status FROM appointments ORDER BY id")]
        summary["stop_queue"] = [dict(row) for row in database.execute("SELECT origin,status,error_code,permission_basis FROM outbox WHERE phone='+12025550812'")]
        summary["provider_submissions"] = database.execute("SELECT count(*) FROM outbox_attempts").fetchone()[0]
elif not all(field in summary for field in ("appointments", "stop_queue", "provider_submissions")):
    raise RuntimeError("UI fixture was purged before its evidence was exported")
(UI / "ui-acceptance-summary.json").write_text(json.dumps(summary, ensure_ascii=False, indent=2), encoding="utf-8")
sdk_skips = sum('requirements-twilio' in item['reason'] or 'SDK' in item['reason'] for item in result['skipped'])
linux_skips = sum('Linux' in item['reason'] for item in result['skipped'])
browser = next(phase["metadata"] for phase in phases if "metadata" in phase)
report = f"""# Current implementation verification — 2026-10-09 (client date)

This report belongs to the approved optimization implementation. It records new
Windows offline and authenticated browser checks; it does not claim complete
Linux/SDK acceptance, real SMS delivery or production readiness.

## Evidence provenance

| Run | Result | Attribution |
|---|---|---|
| Historical independent Windows run | 218: 197 passed, 1 failure, 20 errors, 0 skipped | User-provided earlier independent run; not rerun statistics |
| Original Linux report | 218 passed in its recorded Linux environment | [Preserved original report](TEST_REPORT_ORIGINAL_LINUX.md); not this implementation run |
| Current Windows offline run | {result['discovered']}: **{result['passed']} passed, {result['failures']} failures, {result['errors']} errors, {len(result['skipped'])} skipped** | [Current log](../artifacts/offline-regression-current.txt), [structured results](../artifacts/offline-regression-current.json) |
| Current authenticated UI | **{summary['assertions_passed']} assertions passed**, 4 phases | [UI summary](../output/playwright/ui-acceptance-summary.json), phase logs and screenshots |
| Complete supported Linux + SDK regression | Pending | No approved existing Linux environment was provided |
| Real E2E | Not executed | Separate sender/registration/participant/content/segments/cost/permission approval required |

Current runtime: {result['platform']}; Python {result['python'].splitlines()[0]};
SQLite {result['sqlite']}. Twilio SDK is absent in the available Python; the declared
version remains 9.11.2. Skip reasons identify **{sdk_skips} SDK-dependent** and
**{linux_skips} Linux-only** cases. They are pending checks, not business passes.
Exact execution timestamps are in the structured evidence.

The initial sandbox default temporary directory could not be opened by SQLite;
the final runner sets temporary files inside this workspace for its own process.
That environment error is not a business-regression result. No dependency,
WSL, Docker, system setting, real credential or deployment was installed/configured.

## Implemented behavior exercised locally

- Atomic STOP acceptance and withdrawal; deterministic STOP-before-submit and
  already-in-flight ordering, current retry claims, pending legacy STOP, early
  callbacks without database/network deadlock, uncertain outcomes without resend.
- Durable provider-source authority, human-only reviewed unknown inquiries,
  original resource deadline, fixed outbox snapshots, missing/deleted sources,
  START proof/restoration barriers and no marketing enrollment.
- Explicit test/formal recipient policies, independent formal approval, service
  purpose restriction, pause/emergency/restore/segment/minute/day/contact-hour gates.
- Fifth requests FAQ, independent request drafts, complete/multi-turn requests,
  conservative old-data upgrade, event-ID deduplication and pending confirmation.
- Trusted single-hop login buckets, spoofed/malformed headers, malformed Cookies,
  strict password hashes, authentication/CSRF and bounded shutdown.
- Portable restore-freeze check: even a valid SQLite file lacking settings gets
  pause and independent review gates before Engine reconstructs schema.

Actual process exclusion, POSIX permissions/symlinks, consistent backup/restore,
retention/deletion integration and applicable official-SDK cases remain pending.
Windows skips cannot establish those Linux behaviors.

## Authenticated browser acceptance

Browser: {browser['user_agent']}; timezone {browser['timezone']}.
Existing cached Playwright and installed Chrome were used, with a separate profile
and a loopback-only synthetic fixture. Real sending remained false; a fake provider
would reject any send. **Provider submission attempts: {summary['provider_submissions']}.**

The {summary['assertions_passed']} assertions cover login gating; explicit unchecked
unknown-inquiry confirmation; real human queue creation; fixed human-only source
and deadline; takeover/resume; STOP blocking/cancellation; five approved editors
and the edited fifth answer; second-request re-collection and two pending requests;
queue cancellation/pause; audit and consent UI; privacy masking; CSRF; logout and
readable invalid-login feedback. Final browser JavaScript-runtime error list is empty.
Expected unauthenticated/invalid-CSRF responses and an unserved favicon are recorded
separately from application failures. Early acceptance-script selector/click-target
diagnostics were corrected and repeated; the four final phase logs contain success.

Selected evidence:

- [Human-only reply queued](../output/playwright/02-human-reply-queued.png)
- [Second request starts with new fields](../output/playwright/06-second-request-new-draft.png)
- [Two independent pending requests](../output/playwright/07-two-pending-requests.png)
- [Final STOP and privacy view](../output/playwright/10-final-reviewed-stop.png)
- [Login/exit feedback](../output/playwright/09-logout-and-login-error.png)

The fixture server and browser are stopped. Synthetic database/profile/temp cleanup
is recorded in the UI summary; screenshots, structured summaries and logs remain.
This fixture adds no simulation route or permission bypass to the application.

## Reproduce and remaining acceptance

Run `python scripts/run_offline_regression.py` with existing project dependencies.
On an approved existing Linux host with Twilio 9.11.2, execute the complete suite
and inspect every failure/error/skip. The Linux process/operations scenarios are
retained as real tests; do not substitute mocked locks or remove gates.

UI fixture and check scripts are in `scripts/ui_acceptance_*.py/js`; use a separate
profile, the documented synthetic password and local fake inbound commands. They
must not be launched with client data or mistaken for a production service.

Current offer: $350 / 10 days / 1 agreed-scope revision, one qualified client-owned
number, up to five FAQs and the specified reception features/source/authorized real
testing. Third-party costs belong to the client; see [scope review](SERVICE_SCOPE_REVIEW.md).
Controlled real E2E remains a delivery prerequisite, with its concrete approval
manifest in [REAL_E2E_PLAN](REAL_E2E_PLAN.md). No real test, deployment or publication
is claimed, and the engineering is not marked production-ready.
"""
(ROOT / "docs" / "TEST_REPORT.md").write_text(report, encoding="utf-8")
original = ROOT / "docs" / "TEST_REPORT_ORIGINAL_LINUX.md"
original_text = original.read_text(encoding="utf-8")
if not original_text.startswith('# Historical source snapshot'):
    original.write_text('# Historical source snapshot — original Linux evidence\n\n'
        'Copied before the optimization implementation. Its original “this turn” and\n'
        'test counts refer to that historical run, not the current Windows/UI checks.\n'
        'See [current report](TEST_REPORT.md) for new execution evidence.\n\n' + original_text, encoding="utf-8")
print(json.dumps({"offline_passed": result["passed"], "ui_assertions": summary["assertions_passed"],
    "sdk_pending": sdk_skips, "linux_pending": linux_skips, "provider_submissions": summary["provider_submissions"]}))
