#!/usr/bin/env python3
"""Local operator utilities. Credentials are read from server environment only."""
import argparse
import getpass
import json
import os
import sys

from reception.operations import acknowledge_restore, backup_database, delete_contact, recover_restore, restore_database, run_retention, verify_database


def main(argv=None):
    parser = argparse.ArgumentParser(description="Harbor & Home operator utilities; no sending command")
    commands = parser.add_subparsers(dest="command", required=True)
    commands.add_parser("password-hash", help="Prompt privately and print a salted password hash")
    commands.add_parser("provider-readiness", help="Read account/number metadata; never sends, buys or changes settings")
    backup = commands.add_parser("backup", help="Create a new verified SQLite backup, including committed WAL data")
    backup.add_argument("--db", required=True)
    backup.add_argument("--output", required=True)
    backup.add_argument('--timeout-seconds', type=float, default=5,
                        help='Backup wait/SQLite work budget, greater than zero and at most 120 seconds (default: 5)')
    verify = commands.add_parser("verify-backup")
    verify.add_argument("--backup", required=True)
    restore = commands.add_parser("restore", help="Offline only; restored sends are paused/canceled and pending intake frozen")
    restore.add_argument("--db", required=True)
    restore.add_argument("--backup", required=True)
    restore.add_argument("--offline-confirmed", action="store_true")
    recovery = commands.add_parser('recover-restore', help='Offline recovery of a verified Windows filesystem restore outcome; unknown files are preserved')
    recovery.add_argument('--db', required=True)
    recovery.add_argument('--offline-confirmed', action='store_true')
    acknowledge = commands.add_parser("acknowledge-restore", help="Offline post-backup safety review acknowledgment; leaves all sending paused")
    acknowledge.add_argument("--db", required=True)
    acknowledge.add_argument("--offline-confirmed", action="store_true")
    acknowledge.add_argument("--review-confirmed", action="store_true")
    maintenance = commands.add_parser("maintenance", help="Offline retention; retain only safety suppression/event hashes")
    maintenance.add_argument("--db", required=True)
    maintenance.add_argument("--retention-days", type=int, default=os.environ.get("RETENTION_DAYS", "30"))
    maintenance.add_argument("--offline-confirmed", action="store_true")
    deletion = commands.add_parser("delete-contact", help="Offline customer deletion; privately prompts for phone")
    deletion.add_argument("--db", required=True)
    deletion.add_argument("--offline-confirmed", action="store_true")
    args = parser.parse_args(argv)
    try:
        if args.command == "password-hash":
            from reception.auth import hash_password
            first = getpass.getpass("New admin password (12–256 characters): ")
            second = getpass.getpass("Repeat password: ")
            if first != second:
                raise ValueError("Passwords do not match")
            # Passwords are never command-line arguments, log entries or output.
            print(hash_password(first))
            return 0
        if args.command == "provider-readiness":
            from reception.provider import TwilioRESTProvider
            provider = TwilioRESTProvider(account_sid=os.environ.get("TWILIO_ACCOUNT_SID", ""),
                auth_token=os.environ.get("TWILIO_AUTH_TOKEN", ""), phone_number=os.environ.get("TWILIO_PHONE_NUMBER", ""),
                public_base_url=os.environ.get("TWILIO_PUBLIC_BASE_URL", ""),
                api_key_sid=os.environ.get("TWILIO_API_KEY_SID", ""), api_key_secret=os.environ.get("TWILIO_API_KEY_SECRET", ""),
                timeout_seconds=5)
            result = {"status": "read_only_metadata", **provider.read_only_readiness(),
                      "real_end_to_end": "not_verified", "sending_authorized": False}
        elif args.command == "backup":
            result = backup_database(args.db, args.output, args.timeout_seconds)
        elif args.command == "verify-backup":
            verify_database(args.backup)
            result = {"status": "integrity_and_foreign_keys_verified"}
        elif args.command == "restore":
            result = restore_database(args.db, args.backup, args.offline_confirmed)
        elif args.command == 'recover-restore':
            result = recover_restore(args.db, args.offline_confirmed)
        elif args.command == "acknowledge-restore":
            result = acknowledge_restore(args.db, args.offline_confirmed, args.review_confirmed)
        elif args.command == "maintenance":
            result = run_retention(args.db, os.environ.get("PRIVACY_HMAC_KEY", ""), args.retention_days, args.offline_confirmed)
        else:
            phone = getpass.getpass("Customer number (E.164, private input): ")
            result = delete_contact(args.db, phone, os.environ.get("PRIVACY_HMAC_KEY", ""), args.offline_confirmed)
        print(json.dumps(result, ensure_ascii=False, indent=2))
        return 0
    except (ValueError, RuntimeError, OSError) as exc:
        # Fixed operational error messages contain no credentials/customer body.
        # Provider transport errors expose only a normalized code.
        print("Operation failed: " + str(exc), file=sys.stderr)
        return 2
    except Exception:
        print("Operation failed; inspect configuration and the documented recovery steps. No sensitive exception detail is printed.", file=sys.stderr)
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
