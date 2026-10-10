"""Independently record a new offline run without modifying previous evidence.

Uses the project's Windows Test entry and exact current sources. This is evidence
recording only: no dependency installation, real SMS or access-control mutation.
"""
import argparse
import ast
from collections import Counter
from datetime import datetime, timezone
import hashlib
import json
import os
from pathlib import Path
import re
import shutil
import subprocess
import sys


ROOT = Path(__file__).resolve().parents[1]


def utc():
    return datetime.now(timezone.utc).isoformat()


def digest(path):
    return hashlib.sha256(path.read_bytes()).hexdigest()


def write_new(path, value):
    with path.open("x", encoding="utf-8") as stream:
        json.dump(value, stream, ensure_ascii=False, indent=2)


def source_manifest():
    source_extensions = {".py", ".ps1", ".bat", ".cmd", ".sh", ".sql", ".js", ".css", ".html", ".toml"}
    files = set(path for path in ROOT.iterdir() if path.is_file() and path.suffix in source_extensions)
    for name in ("package.json", "requirements.txt", "requirements-twilio.txt", "requirements-windows.lock.txt"):
        path = ROOT / name
        if path.is_file():
            files.add(path)
    business_fixture = ROOT / "data" / "business.json"
    if business_fixture.is_file():
        files.add(business_fixture)
    for folder in ("reception", "tests", "scripts", "migrations", "web"):
        files.update(path for path in (ROOT / folder).rglob("*") if path.is_file()
                     and "__pycache__" not in path.parts and path.suffix in source_extensions)
    return [{"path": path.relative_to(ROOT).as_posix(), "sha256": digest(path),
             "size": path.stat().st_size} for path in sorted(files)]


def source_methods():
    rows = {}
    for path in (ROOT / "tests").glob("test*.py"):
        source = path.read_text(encoding="utf-8")
        tree = ast.parse(source)
        for cls in tree.body:
            if not isinstance(cls, ast.ClassDef):
                continue
            for method in cls.body:
                if isinstance(method, (ast.FunctionDef, ast.AsyncFunctionDef)) and method.name.startswith("test"):
                    test_id = f"{path.stem}.{cls.name}.{method.name}"
                    rows[test_id] = {"path": path.relative_to(ROOT).as_posix(), "line": method.lineno,
                                     "file_sha256": digest(path),
                                     "method_sha256": hashlib.sha256(ast.get_source_segment(source, method).encode()).hexdigest()}
    return rows


def requirement_groups(cases):
    rules = {
        "faq_configuration_save_hit_disable": ("faq", "all_configured_faq"),
        "unknown_human_review_fixed_deadline_no_marketing": ("human_review", "human_only", "unknown", "review_source", "reviewed_source", "source", "marketing", "window", "permission"),
        "takeover_repeat_human_idempotence": ("takeover", "handoff", "human_reply", "concurrent_duplicate", "repeated_takeover"),
        "appointment_fresh_drafts_corrections_pending": ("booking", "appointment", "request_recollects", "new_request", "second", "requests_faq", "new_area", "new_time", "new_service", "negated_single", "old_and_new_contact", "single_email_correction"),
        "stop_deterministic_ordering_and_recovery": ("stop", "inflight", "submission_barrier", "withdraw", "start", "suppression", "carrier"),
        "sdk_signed_intake_callback_and_error_outcomes": ("test_provider.", "test_twilio_adapter.", "callback", "signed", "signature", "uncertain", "429", "400", "21610", "provider_5xx", "retry", "transport"),
        "auth_session_csrf_origin_trusted_proxy": ("test_auth_security.", "FinalAuthenticationSecurityTests", "login", "cookie", "session", "csrf", "origin", "host", "proxy", "forward", "logout", "credential"),
        "durable_inbox_outbox_dedup_quota_privacy": ("durable", "duplicate", "quota", "limit", "retention", "delete", "tombstone", "restart", "purge", "privacy"),
        "native_process_and_offline_operations": ("test_windows_lifecycle.", "test_windows_operations.", "test_process_lock.", "test_operations.", "test_demo_lifecycle.", "FinalNativeSecurityOpsTests", "FinalDesktopInputSecurityTests", "test_desktop"),
        "dry_run_no_external_submission": ("dry_run", "real_send", "disabled", "simulat", "live_gate", "no_seeded", "no_historical"),
    }
    groups = []
    for name, tokens in rules.items():
        rows = [row for row in cases if any(token in row["id"] for token in tokens)]
        groups.append({"requirement": name, "mapping_basis": "canonical test ID keywords plus independent source review",
                       "case_ids": [row["id"] for row in rows], "status_counts": dict(Counter(row["status"] for row in rows))})
    return groups


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output-dir", type=Path, required=True)
    parser.add_argument("--pattern", default="test*.py")
    args = parser.parse_args()
    if os.name != "nt":
        parser.error("This evidence entry is specifically for an authorized Windows run")
    expected = (ROOT / ".venv" / "Scripts" / "python.exe").resolve()
    if Path(sys.executable).resolve() != expected or Path(sys.prefix).resolve() != (ROOT / ".venv").resolve():
        parser.error("Use the existing project .venv interpreter")
    output = args.output_dir.resolve()
    approved_root = (ROOT / "artifacts" / "final-acceptance-runs").resolve()
    if not output.is_relative_to(approved_root):
        parser.error("Evidence must remain inside artifacts/final-acceptance-runs")
    output.mkdir(parents=True, exist_ok=False)
    shell = shutil.which("pwsh")
    if shell is None:
        raise ValueError("Existing PowerShell 7 is required; no installation is performed")
    command = [shell, "-NoLogo", "-NoProfile", "-File", str(ROOT / "scripts" / "windows.ps1"),
               "-Action", "Test", "-RunDirectory", str(output / "regression"), "-TestPattern", args.pattern]
    before = source_manifest()
    write_new(output / "source-before.json", before)
    write_new(output / "test-methods-before.json", source_methods())
    environment = os.environ.copy()
    for name in ("TWILIO_ACCOUNT_SID", "TWILIO_AUTH_TOKEN", "TWILIO_API_KEY_SID", "TWILIO_API_KEY_SECRET",
                 "TWILIO_PHONE_NUMBER", "TWILIO_PUBLIC_BASE_URL", "ADMIN_PASSWORD_HASH", "PRIVACY_HMAC_KEY",
                 "TEST_RECIPIENT_ALLOWLIST", "ADMIN_ORIGIN", "TRUSTED_PROXY_CIDRS"):
        environment.pop(name, None)
    environment["ENABLE_REAL_SEND"] = "false"
    environment["HARBOR_FINAL_SECURITY_EVIDENCE_DIR"] = str(output / "independent-security-evidence")
    for name in ("CLIENT_READINESS_APPROVED", "TEST_SEND_APPROVAL_RECORDED", "INBOUND_SERVICE_APPROVAL_RECORDED"):
        environment[name] = "false"
    started = utc()
    write_new(output / "execution-request.json", {"wrapper_command": sys.argv, "wrapper_executable": sys.executable,
        "actual_command": command, "cwd": str(ROOT), "started_utc": started,
        "synthetic_only": True, "real_sending": False, "credential_values_inherited": False,
        "independent_security_evidence_dir": environment["HARBOR_FINAL_SECURITY_EVIDENCE_DIR"],
        "preexisting_short_alias_fixture_root": environment.get("HARBOR_FINAL_SHORT_ALIAS_ROOT")})
    completed = subprocess.run(command, cwd=ROOT, env=environment, capture_output=True, text=True,
                               encoding="utf-8", errors="replace", timeout=900)
    finished = utc()
    (output / "launcher.stdout.log").write_text(completed.stdout, encoding="utf-8")
    (output / "launcher.stderr.log").write_text(completed.stderr, encoding="utf-8")
    after = source_manifest()
    write_new(output / "source-after.json", after)
    execution = {"actual_command": command, "started_utc": started, "finished_utc": finished,
        "process_exit_code": completed.returncode, "sources_unchanged": before == after,
        "stdout": str(output / "launcher.stdout.log"), "stderr": str(output / "launcher.stderr.log"),
        "source_before": str(output / "source-before.json"), "source_after": str(output / "source-after.json")}
    write_new(output / "execution-result.json", execution)
    report_path = output / "regression" / "regression.json"
    if not report_path.is_file():
        print(json.dumps(execution))
        return completed.returncode or 1
    report = json.loads(report_path.read_text(encoding="utf-8"))
    methods = source_methods()
    cases = {row["id"]: row for row in report["cases"]}
    full = args.pattern == "test*.py"
    original_path = ROOT / "artifacts" / "offline-regression-current.json"
    original = json.loads(original_path.read_text(encoding="utf-8"))
    original_ids = []
    closure = []
    for row in original["skipped"]:
        match = re.search(r"\(([^()]+)\)", row["test"])
        if match is None:
            raise ValueError("Cannot extract original canonical skipped ID")
        test_id = match.group(1)
        original_ids.append(test_id)
        current = cases.get(test_id)
        case_path = output / "regression" / "cases" / (hashlib.sha256(test_id.encode()).hexdigest() + ".json")
        closure.append({"id": test_id, "original_reason": row["reason"],
            "category": "SDK_dependency" if "Install requirements-twilio.txt" in row["reason"] else "portable_or_mixed_platform",
            "current_status": current["status"] if current else "not_selected" if test_id in report["collected_ids"] or not full and test_id in methods else "missing",
            "subcases": current["subcases"] if current else [], "source": methods.get(test_id),
            "evidence": str(case_path) if case_path.is_file() else None,
            "evidence_sha256": digest(case_path) if case_path.is_file() else None,
            "process_exit_code": completed.returncode, "linux_result": "not_run"})
    if len(original_ids) != 72 or len(set(original_ids)) != 72:
        raise ValueError("Original 72 skipped IDs must remain exact")
    write_new(output / "original-72-current-closure.json", {"original_json": str(original_path),
        "original_json_sha256": digest(original_path), "selected_regression": str(report_path), "rows": closure})
    md = ["# Original 72 IDs — this invocation only", "",
          "Each current result comes from this invocation. Linux branches remain not_run; subcase skips leave parent partial.", "",
          "| ID | Original reason | Current result | Restricted subcases | Evidence |", "|---|---|---|---|---|"]
    for row in closure:
        reasons = "; ".join(case.get("reason", "") for case in row["subcases"] if case["status"] == "skip")
        evidence = f"[case JSON](<{Path(row['evidence']).as_posix()}>)" if row["evidence"] else "not selected"
        md.append(f"| `{row['id']}` | {row['original_reason'].replace('|', '/') } | {row['current_status']} | {reasons} | {evidence} |")
    (output / "original-72-current-closure.md").write_text("\n".join(md) + "\n", encoding="utf-8")
    previous_path = ROOT / "artifacts" / "windows-alignment-runs" / "20261010T033904198Z-f42f3b72" / "full-windows-03" / "regression.json"
    previous = json.loads(previous_path.read_text(encoding="utf-8"))
    discovered = set(report["collected_ids"])
    previous_ids = set(previous["collected_ids"])
    write_new(output / "collection-delta-from-historical-315.json", {
        "historical_json": str(previous_path), "historical_json_sha256": digest(previous_path),
        "comparison_applicable_to_full_suite": full, "collection_pattern": args.pattern,
        "historical_count": len(previous_ids), "current_count": len(discovered),
        "added": sorted(discovered - previous_ids), "removed": sorted(previous_ids - discovered) if full else [],
        "historical_ids_excluded_by_targeted_pattern": len(previous_ids - discovered) if not full else 0,
        "original_72_missing": sorted(set(original_ids) - discovered) if full else []})
    write_new(output / "requirements-current-coverage.json", requirement_groups(report["cases"]))
    current_counts = Counter(row["status"] for row in report["cases"])
    summary = {**execution, "regression": str(report_path), "regression_sha256": digest(report_path),
        "full_suite": full, "collected": report["discovered"], "selected": report["selected_count"],
        "executed_method_attempts": report["actual_execution_attempts"], "status_counts": dict(current_counts),
        "uncaught_thread_errors": report["harness_thread_errors"], "original_72_counts": dict(Counter(row["current_status"] for row in closure)),
        "sdk_original_45_counts": dict(Counter(row["current_status"] for row in closure if row["category"] == "SDK_dependency")),
        "missing_historical_315": sorted(previous_ids - discovered) if full else [],
        "missing_original_72": sorted(set(original_ids) - discovered) if full else [],
        "reviewer": "independent_acceptance, not involved in prior implementation", "linux": "not_run",
        "console_ctrl_c_ctrl_break": "not_run", "real_sms_e2e": "not_run", "customer_data_windows": "candidate/blocked"}
    write_new(output / "independent-summary.json", summary)
    print(json.dumps({key: summary[key] for key in (
        "process_exit_code", "sources_unchanged", "regression", "full_suite", "collected", "selected",
        "executed_method_attempts", "status_counts", "original_72_counts", "sdk_original_45_counts",
        "uncaught_thread_errors")}, ensure_ascii=False))
    problems = (not execution["sources_unchanged"] or completed.returncode != 0 or report["exit_code"] != 0
                or any(row["status"] == "running" for row in report["cases"])
                or full and bool(previous_ids - discovered or set(original_ids) - discovered))
    return 1 if problems else 0


if __name__ == "__main__":
    raise SystemExit(main())
