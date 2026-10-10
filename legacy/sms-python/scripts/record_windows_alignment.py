"""Summarize one selected native run without overwriting historical evidence."""
import argparse
import ast
from collections import Counter
from datetime import datetime, timezone
import difflib
import hashlib
import json
from pathlib import Path
import re
from urllib.parse import urlsplit
import zipfile

ROOT = Path(__file__).resolve().parents[1]


def read_json(path):
    return json.loads(path.read_text(encoding='utf-8-sig'))


def digest(path):
    return hashlib.sha256(path.read_bytes()).hexdigest()


def write_json(path, value):
    path.write_text(json.dumps(value, ensure_ascii=False, indent=2) + '\n', encoding='utf-8')


def result_from_log(path):
    text = path.read_text(encoding='utf-8-sig')
    match = re.search(r'### Result\s*\n([^\r\n]+)', text)
    if not match:
        raise ValueError('Missing successful UI result: ' + str(path))
    return json.loads(match.group(1))


def source_location(test_id):
    module, cls, method = test_id.split('.', 2)
    file = ROOT / 'tests' / (module + '.py')
    tree = ast.parse(file.read_text(encoding='utf-8-sig'))
    for node in tree.body:
        if isinstance(node, ast.ClassDef) and node.name == cls:
            for child in node.body:
                if isinstance(child, ast.FunctionDef) and child.name == method:
                    return f'tests/{module}.py:{child.lineno}'
    raise ValueError('Test source not found: ' + test_id)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--run', required=True, type=Path)
    parser.add_argument('--regression', required=True, type=Path)
    parser.add_argument('--ui-directory', default='ui')
    parser.add_argument('--entry-start', default=1, type=int)
    parser.add_argument('--restore-entry', type=int)
    args = parser.parse_args()
    run = args.run.resolve()
    if not run.is_relative_to(ROOT / 'artifacts' / 'windows-alignment-runs'):
        raise ValueError('Run must be in the approved evidence directory')
    regression = args.regression.resolve()
    ui_directory = run / args.ui_directory
    if not ui_directory.resolve().is_relative_to(run):
        raise ValueError('UI evidence must be within the selected run')
    current = read_json(regression / 'regression.json')
    original = read_json(run / 'original-regression.json')
    before = read_json(run / 'before-manifest.json')
    rows = {row['id']: row for row in current['cases']}
    old_ids = re.findall(r'^test_\w+ \((test_\w+\.\w+\.test_\w+)\) \.\.\.', (run / 'original-regression.txt').read_text(encoding='utf-8-sig'), re.M)
    if len(old_ids) != original['discovered'] or len(set(old_ids)) != len(old_ids):
        raise ValueError('Original collected IDs do not reconcile')
    new_ids = set(current['collected_ids'])
    delta = {'original_count': len(old_ids), 'current_count': len(new_ids),
             'added': sorted(new_ids - set(old_ids)), 'removed': sorted(set(old_ids) - new_ids),
             'semantic_mapping': [{'old': 'test_operations.OperationsTests.test_windows_operations_fail_explicitly_without_linux_lock',
                 'new': 'test_operations.OperationsTests.test_unsupported_platform_operations_fail_closed',
                 'reason': 'Windows now has native ownership; retain fail-closed rejection on unsupported platforms. This original passed ID is outside the 72 skips.'}]}
    write_json(run / 'collection-delta.json', delta)
    mixed_methods = {
        'test_backup_captures_committed_wal_and_verifies_permissions': 'M: Windows WAL/backup executed; original Linux 0600 assertion retained, not executed. Current-token Windows access does not establish cross-user privacy.',
        'test_backup_rejects_symlinks_and_corrupt_foreign_keys': 'M: Foreign-key rejection executed; original file-symlink attempt blocked by WinError 1314. Local junction evidence is additional, not a substitute.',
        'test_database_and_lock_symlinks_and_database_hardlinks_are_rejected': 'M: Real hard-link/leaf-reparse rejection executed; original file/lock symlink subcases blocked by WinError 1314.',
        'test_path_aliases_share_one_lock_and_yield_canonical_database': 'M: Relative/case and real local junction aliases executed; original directory-symlink attempt blocked by WinError 1314.'}
    matrix = []
    for item in original['skipped']:
        test_id = re.search(r'\(([^()]+)\)', item['test']).group(1)
        row = rows[test_id]
        dependency = 'Install requirements-twilio.txt' in item['reason']
        method = test_id.rsplit('.', 1)[1]
        category = 'D_missing_dependency' if dependency else 'M_mixed_platform_capability' if method in mixed_methods else 'P_portable_behavior_previously_OS_skipped'
        disposition = ('Installed declared SDK in isolated venv; original ID/assertions/SDK mocks executed, no real provider call.' if dependency else mixed_methods.get(method, 'Removed whole Linux restriction; original assertions executed with actual Windows lock/files/SQLite. Linux branch retained, not executed this turn.'))
        case_path = regression / 'cases' / (hashlib.sha256(test_id.encode()).hexdigest() + '.json')
        matrix.append({'id': test_id, 'original_status': 'skipped', 'original_reason': item['reason'],
                       'category': category, 'source': source_location(test_id), 'disposition': disposition,
                       'applicable_platforms': ['Windows local NTFS', 'Linux'], 'windows_result': row['status'],
                       'subcases': row['subcases'], 'linux_result': 'not_run',
                       'run_id': current['run_id'], 'process_exit_code': current['exit_code'],
                       'evidence': str(case_path.relative_to(ROOT)), 'evidence_sha256': digest(case_path),
                       'log': str((regression / 'regression.log').relative_to(ROOT))})
    if len(matrix) != 72 or len({item['id'] for item in matrix}) != 72:
        raise ValueError('Original 72 IDs must be preserved exactly')
    write_json(run / 'skip-closure-72.json', matrix)
    matrix_text = ['# 原 72 个跳过 ID 的逐项闭环', '',
        '仅选定本次完整运行，不累计重复执行。D=原缺 SDK；P=原实现整项 Linux 限制而行为可移植；M=混合平台/权限。',
        'partial 是已执行共享断言但子场景受阻，不能计作完整通过。Linux 的权限/flock 分支保留，本轮未跑。', '',
        '| 完整 ID / 源码 | 原始原因 | 分类及处置 | Windows 实际结果 | Linux | 证据 |',
        '|---|---|---|---|---|---|']
    for item in matrix:
        reason = item['original_reason'].replace('|', '\\|')
        limited = '; '.join(sub.get('reason', '') for sub in item['subcases'] if sub['status'] == 'skip')
        evidence = item['evidence'].replace('\\', '/')
        absolute_evidence = (ROOT / item['evidence']).as_posix()
        matrix_text.append(f"| `{item['id']}`<br>{item['source']} | {reason} | {item['category']}<br>{item['disposition']} | **{item['windows_result']}** {limited} | not_run | [case JSON](<{absolute_evidence}>) |")
    matrix_text.extend(['', '完整原始原因、子场景、run ID、退出码、证据 SHA256 见对应 JSON；Windows 执行成立不表示 Linux 分支本轮通过。'])
    matrix_md = '\n'.join(matrix_text) + '\n'
    (run / 'skip-closure-72.md').write_text(matrix_md, encoding='utf-8')
    (ROOT / 'docs' / 'WINDOWS_SKIP_MATRIX.md').write_text(matrix_md, encoding='utf-8')

    phases = [result_from_log(ui_directory / f'phase{i}-result.log') for i in range(1, 5)]
    entry = result_from_log(ui_directory / 'entry-result.log')
    with zipfile.ZipFile(ui_directory / 'trace.zip') as archive:
        events = [json.loads(line) for name in archive.namelist() if name.endswith('.trace') for line in archive.read(name).decode().splitlines()]
        network = [json.loads(line)['snapshot'] for name in archive.namelist() if name.endswith('.network') for line in archive.read(name).decode().splitlines()]
    requests = [{'time_utc': item['startedDateTime'], 'method': item['request']['method'],
                 'url': item['request']['url'].split('?', 1)[0], 'status': item['response']['status']} for item in network]
    external = [request for request in requests if urlsplit(request['url']).hostname != '127.0.0.1']
    runtime_errors = [event for event in events if event['type'] == 'event' and event.get('method') == 'pageError']
    console_errors = [event for event in events if event['type'] == 'console' and event.get('messageType') == 'error']
    unexpected = [request for request in requests if request['status'] >= 400 and not (
        request['status'] == 401 and urlsplit(request['url']).path in ('/api/session', '/api/state', '/api/login') or
        request['status'] == 403 and urlsplit(request['url']).path == '/api/handoff' or
        urlsplit(request['url']).path == '/favicon.ico')]
    if external or runtime_errors or unexpected:
        raise ValueError('Unexpected UI traffic or runtime errors: ' + repr((external, runtime_errors, unexpected)))
    restore_ui = result_from_log(ui_directory / 'restore-result.log')
    entries = [*range(args.entry_start, args.entry_start + 3), args.restore_entry or args.entry_start + 3]
    manifests = [read_json(run / f'app-ui-entry-{i:02d}' / 'run-manifest.json') for i in entries]
    launchers = [read_json(run / f'app-ui-entry-{i:02d}' / 'launcher-ready.json') for i in entries]
    exits = [read_json(run / f'app-ui-entry-{i:02d}' / 'launcher-exit.json') for i in entries]
    for manifest, launcher, end in zip(manifests, launchers, exits):
        assert manifest['status'] == 'stopped' and manifest['instance_lock'] == 'released'
        assert launcher['app_mode'] == 'service' and launcher['real_outbound'] is False
        assert launcher['python_prefix'] == str(ROOT / '.venv')
        assert 'app.py' in launcher['app_identity']['os_command_line']
        assert end['launcher_exit_code'] == 0
    ui = {'core_assertions_passed': sum(phase['passed'] for phase in phases),
          'restore_assertions_passed': restore_ui['passed'], 'restore_ui': restore_ui,
          'entry_assertions': entry['checks'], 'phases': phases,
          'real_entry': 'PowerShell Start -> project .venv Python -> app.py / APP_MODE=service; no HTTP fixture',
          'manifests': manifests, 'launchers': launchers, 'requests': requests,
          'network_from_first_application_navigation': True, 'external_requests': external,
          'runtime_errors': runtime_errors, 'unexpected_http_errors': unexpected,
          'expected_http_rejections': [item for item in requests if item['status'] >= 400],
          'console_errors': console_errors, 'favicon_requests': [item for item in requests if urlsplit(item['url']).path == '/favicon.ico'],
          'trace': str((ui_directory / 'trace.zip').relative_to(ROOT)), 'real_e2e_executed': False,
          'diagnostics': ['Initial CLI evaluation referenced URL outside its sandbox globals and was corrected before verification session.',
              'CLI globals are isolated per evaluation; trace archive, not a missing global export, supplies full-navigation network/error evidence.']}
    write_json(ui_directory / 'ui-acceptance-summary.json', ui)

    protected = []
    for item in before['protected_files']:
        path = ROOT / item['path']
        protected.append({'path': item['path'], 'before_sha256': item['sha256'].lower(),
                          'after_sha256': digest(path) if path.is_file() else None,
                          'unchanged': path.is_file() and digest(path) == item['sha256'].lower()})
    for original_path, copy_path in [('artifacts/offline-regression-current.json', 'original-regression.json'),
                                     ('artifacts/offline-regression-current.txt', 'original-regression.txt')]:
        protected.append({'path': original_path, 'unchanged': digest(ROOT / original_path) == digest(run / copy_path)})
    observed_changes = [item for item in protected if not item['unchanged']]
    for item in observed_changes:
        # The external coordinator updated approval status seven seconds after
        # the implementation preimage. Observe it, never restore/overwrite it.
        if item['path'].replace('\\', '/') != 'artifacts/windows-alignment-evidence/STATUS.json':
            raise ValueError('Protected baseline changed: ' + item['path'])
        status = read_json(ROOT / item['path'])
        if status.get('UpdatedUtc') != '2026-10-10T03:39:11.6695675Z' or status.get('NativeApprovalConfirmed') is not True:
            raise ValueError('Unrecognized external status change')
        item['attribution'] = 'Observed external approval-status update after preimage; this implementation does not write this file.'
        item['observed_updated_utc'] = status['UpdatedUtc']
        (run / 'external-status-observed.json').write_bytes((ROOT / item['path']).read_bytes())
    write_json(run / 'protected-evidence-verification.json', protected)
    before_sources = {item['path'].replace('\\', '/'): item for item in before['sources']}
    sources = set(before_sources)
    sources.add('requirements-windows.lock.txt')
    for folder in ('reception', 'tests', 'scripts', 'docs'):
        for path in (ROOT / folder).rglob('*'):
            if path.is_file() and path.suffix in ('.py', '.md', '.json', '.txt', '.ps1', '.bat'):
                sources.add(path.relative_to(ROOT).as_posix())
    changes, difference = [], []
    after_sources = []
    for name in sorted(sources):
        current_path = ROOT / name
        previous = before_sources.get(name)
        after_hash = digest(current_path) if current_path.is_file() else None
        if after_hash is not None:
            after_sources.append({'path': name, 'sha256': after_hash, 'size': current_path.stat().st_size})
        if previous and previous['sha256'].lower() == after_hash:
            continue
        changes.append({'path': name, 'before_sha256': previous['sha256'].lower() if previous else None,
                        'after_sha256': after_hash, 'status': 'modified' if previous and after_hash else 'added' if after_hash else 'removed'})
        before_path = run / 'before-source' / name
        old_text = before_path.read_text(encoding='utf-8-sig').splitlines(keepends=True) if before_path.is_file() else []
        new_text = current_path.read_text(encoding='utf-8-sig').splitlines(keepends=True) if current_path.is_file() else []
        difference.extend(difflib.unified_diff(old_text, new_text, fromfile='before/' + name, tofile='after/' + name))
    write_json(run / 'source-changes.json', changes)
    write_json(run / 'after-manifest.json', {'recorded_utc': datetime.now(timezone.utc).isoformat(),
        'sources': after_sources, 'source_count': len(after_sources), 'selected_regression': str(regression.relative_to(ROOT)),
        'selected_ui': str(ui_directory.relative_to(ROOT)), 'all_docs_written_before_manifest': True})
    (run / 'source-changes.diff').write_text(''.join(difference), encoding='utf-8')
    write_json(run / 'implementation-status.json', {'recorded_utc': datetime.now(timezone.utc).isoformat(),
        'native_plan_original': 'native-plan-original.md', 'native_plan_sha256': digest(run / 'native-plan-original.md'),
        'current_mode': 'Default execution', 'approval_basis': 'User preauthorization, reviewed scope, then native Yes implement and latest instructions.',
        'git_present': (ROOT / '.git').exists(), 'no_git_diff_claim': True,
        'implementation': 'implemented; remaining capability/access-boundary conditions listed in report',
        'selected_regression': str(regression.relative_to(ROOT)), 'selected_exit_code': current['exit_code'],
        'original_72_statuses': dict(Counter(item['windows_result'] for item in matrix)),
        'ui_core_assertions': ui['core_assertions_passed'], 'protected_file_count': len(protected),
        'protected_unchanged_count': sum(item['unchanged'] for item in protected),
        'observed_external_metadata_changes': observed_changes,
        'linux_tested_this_turn': False, 'real_e2e_executed': False,
        'system_settings_acl_changes': False, 'customer_data_platform_status': 'candidate / private-directory and independent user evidence blocked'})
    print(json.dumps({'selected': str(regression), 'counts': {key: current[key] for key in ('discovered','actual_execution_attempts','passed','partial','failures','errors','whole_skips')},
        'original72': dict(Counter(item['windows_result'] for item in matrix)), 'ui_core': ui['core_assertions_passed'], 'changes': len(changes), 'protected': len(protected)}, ensure_ascii=False))


if __name__ == '__main__':
    main()
