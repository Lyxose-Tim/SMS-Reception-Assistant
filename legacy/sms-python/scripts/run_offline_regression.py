"""Run offline tests with workspace temporary files and attributable evidence."""
import argparse
import hashlib
import importlib.metadata
import importlib.util
import json
import os
import platform
import sqlite3
import socket
import sys
import tempfile
import unittest
from contextlib import contextmanager
from datetime import datetime, timezone
import time
import threading
import traceback
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))


def utc():
    return datetime.now(timezone.utc).isoformat()


def flatten(suite):
    for entry in suite:
        if isinstance(entry, unittest.TestSuite):
            yield from flatten(entry)
        else:
            yield entry


class EvidenceResult(unittest.TextTestResult):
    """Method results and capability subcases are counted separately."""
    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.rows = {}

    def startTest(self, test):
        self.rows[test.id()] = {"id": test.id(), "started_utc": utc(), "status": "running", "subcases": []}
        super().startTest(test)

    def stopTest(self, test):
        row = self.rows[test.id()]
        # unittest suppresses addSuccess for a parent containing a skipped
        # subtest. Its remaining assertions still execute, so retain partial.
        if row["status"] == "running" and any(case["status"] == "skip" for case in row["subcases"]):
            row["status"] = "partial"
        row["finished_utc"] = utc()
        super().stopTest(test)

    def addSuccess(self, test):
        row = self.rows[test.id()]
        row["status"] = "partial" if any(case["status"] == "skip" for case in row["subcases"]) else "passed"
        super().addSuccess(test)

    def addFailure(self, test, err):
        self.rows[test.id()].update(status="failure", detail=self._exc_info_to_string(err, test))
        super().addFailure(test, err)

    def addError(self, test, err):
        self.rows[test.id()].update(status="error", detail=self._exc_info_to_string(err, test))
        super().addError(test, err)

    def addSkip(self, test, reason):
        parent = getattr(test, "test_case", None)
        if parent is not None:
            self.rows[parent.id()]["subcases"].append({"id": test.id(), "status": "skip", "reason": reason})
        else:
            self.rows[test.id()].update(status="skip", reason=reason)
        super().addSkip(test, reason)

    def addSubTest(self, test, subtest, err):
        status = "passed" if err is None else "failure" if issubclass(err[0], test.failureException) else "error"
        self.rows[test.id()]["subcases"].append({"id": subtest.id(), "status": status,
            "detail": self._exc_info_to_string(err, subtest) if err else ""})
        if err:
            self.rows[test.id()]["status"] = status
        super().addSubTest(test, subtest, err)


@contextmanager
def offline_network():
    """Fail an unexpected non-loopback request rather than mock its outcome."""
    original_connect, original_connect_ex, original_lookup = socket.socket.connect, socket.socket.connect_ex, socket.getaddrinfo
    allowed = {"127.0.0.1", "::1", "localhost"}
    def check(address):
        if not isinstance(address, tuple) or address[0] not in allowed:
            raise AssertionError("Offline regression attempted a non-loopback connection")
    def connect(sock, address):
        check(address)
        return original_connect(sock, address)
    def connect_ex(sock, address):
        check(address)
        return original_connect_ex(sock, address)
    def lookup(host, *args, **kwargs):
        if host not in allowed and host not in {None, ""}:
            raise AssertionError("Offline regression attempted non-loopback DNS")
        return original_lookup(host, *args, **kwargs)
    socket.socket.connect, socket.socket.connect_ex, socket.getaddrinfo = connect, connect_ex, lookup
    try:
        yield
    finally:
        socket.socket.connect, socket.socket.connect_ex, socket.getaddrinfo = original_connect, original_connect_ex, original_lookup


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output-dir", type=Path)
    parser.add_argument("--pattern", default="test*.py")
    parser.add_argument("--ids-file", type=Path, help="JSON list of canonical IDs for an explicitly targeted run")
    args = parser.parse_args()
    run_id = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%S%fZ")
    output = args.output_dir or ROOT / "artifacts" / "offline-runs" / run_id
    output = output.resolve()
    output.mkdir(parents=True, exist_ok=False)
    cache = output / "test-temp"
    cache.mkdir(parents=True, exist_ok=True)
    tempfile.tempdir = str(cache)
    os.environ["TMP"] = os.environ["TEMP"] = str(cache)
    os.environ["HARBOR_WINDOWS_SECURITY_EVIDENCE_DIR"] = str(output / "security-evidence")
    os.environ["HARBOR_WINDOWS_NATIVE_EVIDENCE_DIR"] = str(output / "native-evidence")
    discovered = unittest.defaultTestLoader.discover(str(ROOT / "tests"), pattern=args.pattern)
    tests = list(flatten(discovered))
    collected_ids = [test.id() for test in tests]
    requested = json.loads(args.ids_file.read_text(encoding="utf-8")) if args.ids_file else None
    if requested is not None and (not isinstance(requested, list) or len(set(requested)) != len(requested)):
        raise ValueError("Targeted IDs must be a unique JSON list")
    missing = sorted(set(requested or []) - set(collected_ids))
    if missing:
        raise ValueError("Requested test IDs are absent: " + ", ".join(missing))
    suite = unittest.TestSuite(test for test in tests if requested is None or test.id() in requested)
    selected_ids = [test.id() for test in flatten(suite)]
    log_path = output / "regression.log"
    started = utc()
    monotonic_start = time.monotonic()
    thread_errors = []
    original_thread_hook = threading.excepthook
    def record_thread_error(arguments):
        thread_errors.append({"thread": arguments.thread.name,
            "traceback": "".join(traceback.format_exception(arguments.exc_type, arguments.exc_value, arguments.exc_traceback))})
        original_thread_hook(arguments)
    threading.excepthook = record_thread_error
    with log_path.open("w", encoding="utf-8") as log:
        log.write(json.dumps({"command": sys.argv, "executable": sys.executable, "started_utc": started}) + "\n")
        try:
            with offline_network():
                result = unittest.TextTestRunner(stream=log, verbosity=2, resultclass=EvidenceResult).run(suite)
        finally:
            threading.excepthook = original_thread_hook
        for error in thread_errors:
            log.write("UNCAUGHT THREAD ERROR: " + json.dumps(error) + "\n")
    skipped = [{"test": str(test), "reason": reason} for test, reason in result.skipped]
    statuses = {status: sum(row["status"] == status for row in result.rows.values()) for status in ("passed", "failure", "error", "skip", "partial")}
    exit_code = 0 if result.wasSuccessful() and not thread_errors else 1
    try:
        sdk_version = importlib.metadata.version("twilio")
    except importlib.metadata.PackageNotFoundError:
        sdk_version = None
    report = {"run_id": run_id, "command": sys.argv, "started_utc": started, "finished_utc": utc(),
        "local_finished": datetime.now().astimezone().isoformat(), "duration_seconds": time.monotonic() - monotonic_start,
        "platform": platform.platform(), "python": sys.version, "sqlite": sqlite3.sqlite_version,
        "executable": sys.executable, "prefix": sys.prefix, "base_prefix": sys.base_prefix,
        "architecture": platform.architecture()[0], "twilio_version": sdk_version,
        "twilio_sdk_available": importlib.util.find_spec("twilio") is not None,
        "collected_ids": collected_ids, "selected_ids": selected_ids, "targeted": requested is not None,
        "discovered": len(collected_ids), "actual_execution_attempts": result.testsRun - statuses["skip"],
        "selected_count": len(selected_ids), "passed": statuses["passed"], "partial": statuses["partial"],
        "failures": statuses["failure"], "errors": statuses["error"], "whole_skips": statuses["skip"], "skipped": skipped,
        "cases": list(result.rows.values()), "exit_code": exit_code,
        "harness_thread_errors": thread_errors,
        "failure_details": [{"test": str(test), "traceback": detail} for test, detail in result.failures + result.errors],
        "real_e2e_executed": False, "log": str(log_path)}
    (output / "regression.json").write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")
    case_folder = output / "cases"
    case_folder.mkdir()
    for row in result.rows.values():
        name = hashlib.sha256(row["id"].encode()).hexdigest() + ".json"
        (case_folder / name).write_text(json.dumps(row, ensure_ascii=False, indent=2), encoding="utf-8")
    print(json.dumps({key: report[key] for key in ("discovered", "passed", "failures", "errors", "twilio_sdk_available")}, ensure_ascii=False))
    print("Skipped:", len(skipped), "Log:", log_path)
    return exit_code


if __name__ == "__main__":
    raise SystemExit(main())
