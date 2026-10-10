"""Real Windows filesystem/SQLite checks with local synthetic files only.

Documented ReplaceFile partial states below are built with real file moves;
they are recovery-state tests, not claims to reproduce native error 1177.
"""
from contextlib import closing
import ctypes
from ctypes import wintypes
import json
import hashlib
import os
from pathlib import Path
import sqlite3
import tempfile
import sys
import unittest
import uuid
from datetime import datetime, timezone

from reception.engine import Engine
from reception.operations import (RestoreRecoveryRequired, _file_hash, _freeze_restored_tasks,
    _recovery_marker, _write_recovery_marker, backup_database, recover_restore,
    restore_database, restore_recovery_pending, service_lock, verify_database)
from reception.platform_fs import file_identity, flush_file, move_no_replace, replace_file

_NATIVE_OPEN_EVENTS = []
def _native_open_observer(event, arguments):
    if event == 'harbor.native_fs_open':
        _NATIVE_OPEN_EVENTS.append(arguments[0])
sys.addaudithook(_native_open_observer)


def current_token_file_access(path):
    """Real DACL evaluation for this token; no claim about other users."""
    advapi = ctypes.WinDLL('advapi32', use_last_error=True)
    kernel = ctypes.WinDLL('kernel32', use_last_error=True)
    class Mapping(ctypes.Structure):
        _fields_ = [('read', wintypes.DWORD), ('write', wintypes.DWORD), ('execute', wintypes.DWORD), ('all', wintypes.DWORD)]
    kernel.GetCurrentProcess.restype = wintypes.HANDLE
    kernel.CloseHandle.argtypes = [wintypes.HANDLE]
    kernel.LocalFree.argtypes = [ctypes.c_void_p]
    kernel.LocalFree.restype = ctypes.c_void_p
    advapi.OpenProcessToken.argtypes = [wintypes.HANDLE, wintypes.DWORD, ctypes.POINTER(wintypes.HANDLE)]
    advapi.DuplicateToken.argtypes = [wintypes.HANDLE, ctypes.c_int, ctypes.POINTER(wintypes.HANDLE)]
    advapi.GetNamedSecurityInfoW.argtypes = [wintypes.LPWSTR, ctypes.c_int, wintypes.DWORD,
        ctypes.POINTER(ctypes.c_void_p), ctypes.POINTER(ctypes.c_void_p), ctypes.POINTER(ctypes.c_void_p),
        ctypes.POINTER(ctypes.c_void_p), ctypes.POINTER(ctypes.c_void_p)]
    advapi.GetNamedSecurityInfoW.restype = wintypes.DWORD
    advapi.AccessCheck.argtypes = [ctypes.c_void_p, wintypes.HANDLE, wintypes.DWORD, ctypes.POINTER(Mapping),
        ctypes.c_void_p, ctypes.POINTER(wintypes.DWORD), ctypes.POINTER(wintypes.DWORD), ctypes.POINTER(wintypes.BOOL)]
    token, impersonation = wintypes.HANDLE(), wintypes.HANDLE()
    owner, group, dacl, descriptor = (ctypes.c_void_p() for _ in range(4))
    try:
        if not advapi.OpenProcessToken(kernel.GetCurrentProcess(), 0x0002 | 0x0008, ctypes.byref(token)):
            raise ctypes.WinError(ctypes.get_last_error())
        if not advapi.DuplicateToken(token, 2, ctypes.byref(impersonation)):
            raise ctypes.WinError(ctypes.get_last_error())
        code = advapi.GetNamedSecurityInfoW(str(path), 1, 7, ctypes.byref(owner), ctypes.byref(group), ctypes.byref(dacl), None, ctypes.byref(descriptor))
        if code:
            raise ctypes.WinError(code)
        if not owner.value or not group.value or not dacl.value:
            raise RuntimeError('Missing owner/group/DACL cannot be security evidence')
        mapping = Mapping(0x00120089, 0x00120116, 0x001200A0, 0x001F01FF)
        buffer = ctypes.create_string_buffer(4096)
        length, granted, allowed = wintypes.DWORD(len(buffer)), wintypes.DWORD(), wintypes.BOOL()
        # Generic rights have already been mapped to the concrete file masks.
        if not advapi.AccessCheck(descriptor, impersonation, mapping.read | mapping.write,
                ctypes.byref(mapping), buffer, ctypes.byref(length), ctypes.byref(granted), ctypes.byref(allowed)):
            raise ctypes.WinError(ctypes.get_last_error())
        return bool(allowed.value), granted.value
    finally:
        if descriptor.value:
            kernel.LocalFree(descriptor)
        for handle in (impersonation, token):
            if handle.value:
                kernel.CloseHandle(handle)


def file_security_snapshot(path):
    """Read OWNER/GROUP/DACL bytes; never request SACL or modify file ACLs."""
    advapi=ctypes.WinDLL('advapi32',use_last_error=True)
    kernel=ctypes.WinDLL('kernel32',use_last_error=True)
    class ACLHeader(ctypes.Structure):
        _fields_=[('revision',wintypes.BYTE),('reserved',wintypes.BYTE),('size',wintypes.WORD),
                  ('ace_count',wintypes.WORD),('reserved2',wintypes.WORD)]
    advapi.GetNamedSecurityInfoW.argtypes=[wintypes.LPWSTR,ctypes.c_int,wintypes.DWORD,
        ctypes.POINTER(ctypes.c_void_p),ctypes.POINTER(ctypes.c_void_p),ctypes.POINTER(ctypes.c_void_p),
        ctypes.POINTER(ctypes.c_void_p),ctypes.POINTER(ctypes.c_void_p)]
    advapi.GetNamedSecurityInfoW.restype=wintypes.DWORD
    advapi.IsValidSecurityDescriptor.argtypes=[ctypes.c_void_p]
    advapi.IsValidSecurityDescriptor.restype=wintypes.BOOL
    advapi.IsValidAcl.argtypes=[ctypes.c_void_p]
    advapi.IsValidAcl.restype=wintypes.BOOL
    advapi.GetSecurityDescriptorControl.argtypes=[ctypes.c_void_p,ctypes.POINTER(wintypes.WORD),ctypes.POINTER(wintypes.DWORD)]
    advapi.GetSecurityDescriptorLength.argtypes=[ctypes.c_void_p]
    advapi.GetSecurityDescriptorLength.restype=wintypes.DWORD
    advapi.GetLengthSid.argtypes=[ctypes.c_void_p]
    advapi.GetLengthSid.restype=wintypes.DWORD
    advapi.MakeSelfRelativeSD.argtypes=[ctypes.c_void_p,ctypes.c_void_p,ctypes.POINTER(wintypes.DWORD)]
    kernel.LocalFree.argtypes=[ctypes.c_void_p]
    kernel.LocalFree.restype=ctypes.c_void_p
    owner,group,dacl,descriptor=(ctypes.c_void_p() for _ in range(4))
    try:
        code=advapi.GetNamedSecurityInfoW(str(path),1,7,ctypes.byref(owner),ctypes.byref(group),ctypes.byref(dacl),None,ctypes.byref(descriptor))
        if code:
            raise ctypes.WinError(code)
        if not owner.value or not group.value or not dacl.value or not advapi.IsValidSecurityDescriptor(descriptor) or not advapi.IsValidAcl(dacl):
            raise RuntimeError('A complete valid OWNER/GROUP/DACL snapshot is required')
        control,revision=wintypes.WORD(),wintypes.DWORD()
        if not advapi.GetSecurityDescriptorControl(descriptor,ctypes.byref(control),ctypes.byref(revision)):
            raise ctypes.WinError(ctypes.get_last_error())
        if control.value & 0x8000:
            size=advapi.GetSecurityDescriptorLength(descriptor)
            descriptor_bytes=ctypes.string_at(descriptor,size)
        else:
            size=wintypes.DWORD(0)
            advapi.MakeSelfRelativeSD(descriptor,None,ctypes.byref(size))
            if ctypes.get_last_error()!=122 or not size.value:
                raise ctypes.WinError(ctypes.get_last_error())
            buffer=ctypes.create_string_buffer(size.value)
            if not advapi.MakeSelfRelativeSD(descriptor,buffer,ctypes.byref(size)):
                raise ctypes.WinError(ctypes.get_last_error())
            descriptor_bytes=buffer.raw[:size.value]
        header=ACLHeader.from_address(dacl.value)
        if header.size<ctypes.sizeof(ACLHeader):
            raise RuntimeError('DACL snapshot size is invalid')
        return {'descriptor':descriptor_bytes,'dacl':ctypes.string_at(dacl,header.size),
                'owner':ctypes.string_at(owner,advapi.GetLengthSid(owner)),
                'group':ctypes.string_at(group,advapi.GetLengthSid(group)),
                'control':control.value,'revision':revision.value,'ace_count':header.ace_count}
    finally:
        if descriptor.value:
            kernel.LocalFree(descriptor)


def record_security_comparison(case,before,after,scenario_kind):
    root=os.environ.get('HARBOR_WINDOWS_SECURITY_EVIDENCE_DIR')
    if not root:
        return
    destination=Path(root)/f'{case}-{uuid.uuid4().hex[:12]}'
    destination.mkdir(parents=True,exist_ok=False)
    summary={'case':case,'utc':datetime.now(timezone.utc).isoformat(),'scenario_kind':scenario_kind,
        'security_information':'OWNER|GROUP|DACL (0x7); SACL not requested',
        'descriptor_equal':before['descriptor']==after['descriptor'],'dacl_equal':before['dacl']==after['dacl'],
        'owner_equal':before['owner']==after['owner'],'group_equal':before['group']==after['group'],
        'control_before':before['control'],'control_after':after['control'],
        'control_xor':before['control']^after['control'],
        'control_xor_interpretation':'SE_DACL_AUTO_INHERITED (0x0400)' if before['control']^after['control']==0x0400 else 'unchanged' if before['control']==after['control'] else 'unexpected control delta',
        'descriptor_changed_byte_offsets':[index for index,(old,new) in enumerate(zip(before['descriptor'],after['descriptor'])) if old!=new],
        'explicit_acl_setter_api_called_by_project':False,'customer_data_privacy':'candidate/blocked; independent-user validation not performed',
        'native_error_1177_reproduced':False,'snapshots':{}}
    if case=='normal-restore':
        summary['strict_raw_equality_diagnostic_test_id']='test_windows_operations.WindowsOperationsTests.test_normal_restore_preserves_real_descriptor_and_dacl_bytes'
        summary['current_test_id']='test_windows_operations.WindowsOperationsTests.test_normal_restore_preserves_dacl_and_records_descriptor_flags'
    for label,snapshot in (('before',before),('after',after)):
        evidence={key:snapshot[key] for key in ('control','revision','ace_count')}
        for component in ('descriptor','dacl','owner','group'):
            path=destination/f'{label}-{component}.bin'
            path.write_bytes(snapshot[component])
            evidence[component]={'path':str(path.resolve()),'length':len(snapshot[component]),
                'sha256':hashlib.sha256(snapshot[component]).hexdigest()}
        summary['snapshots'][label]=evidence
    report=destination/'comparison.json'
    report.write_text(json.dumps(summary,ensure_ascii=False,indent=2),encoding='utf-8')
    print('WINDOWS_SECURITY_EVIDENCE:',report.resolve())


@unittest.skipUnless(os.name == 'nt', 'Windows native replacement/recovery acceptance')
class WindowsOperationsTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.base = Path(self.temp.name)
        self.path = self.base / 'service.db'
        self.engine = Engine(self.path, production=True, seed_demo=False)

    def tearDown(self):
        if self.engine:
            self.engine.close()
        self.temp.cleanup()

    def close_engine(self):
        self.engine.close()
        self.engine = None

    def tree_fingerprint(self):
        result=[]
        for path in [self.base, *sorted(self.base.rglob('*'))]:
            metadata=path.lstat()
            digest=_file_hash(path) if path.is_file() else None
            result.append((str(path.relative_to(self.base)),metadata.st_size,metadata.st_mtime_ns,metadata.st_ctime_ns,digest))
        return result

    def test_invalid_backup_output_is_rejected_before_metadata_or_creation(self):
        (self.base/'sentinel.txt').write_text('preserved synthetic sentinel',encoding='utf-8')
        prefix=str(self.base/'never-created')
        invalid=[prefix+'\\CON\\backup.db',prefix+'\\NUL.db',prefix+'\\COM1\\backup.db',
            prefix+'\\backup.db:stream',prefix+'\\trailing.\\backup.db',prefix+'\\trailing \\backup.db',
            prefix+'\\wild*card\\backup.db',prefix+'\\control\x01\\backup.db',
            'D:drive-relative\\backup.db', '\\\\not-queried.invalid\\share\\backup.db',
            '\\\\?\\UNC\\not-queried.invalid\\share\\backup.db', '\\\\.\\PhysicalDrive0']
        for output in invalid:
            with self.subTest(output=output):
                before=self.tree_fingerprint()
                count=len(_NATIVE_OPEN_EVENTS)
                with self.assertRaises(ValueError):
                    backup_database(self.path,output)
                self.assertEqual(len(_NATIVE_OPEN_EVENTS),count,'Rejected lexical names must never reach native CreateFileW')
                self.assertEqual(self.tree_fingerprint(),before)
        self.assertFalse((self.base/'never-created').exists())

    def test_valid_nested_backup_parent_is_created_only_after_validation(self):
        output=self.base/'new-local-parent'/'nested'/'backup.db'
        backup_database(self.path,output)
        self.assertTrue(output.exists())
        self.assertTrue(verify_database(output))

    def test_native_non_directory_ancestor_rejects_without_creating_children(self):
        parent_file=self.base/'existing-file'
        parent_file.write_text('synthetic non-directory ancestor',encoding='utf-8')
        before=self.tree_fingerprint()
        with self.assertRaises((ValueError,OSError)):
            backup_database(self.path,parent_file/'must-not-exist'/'backup.db')
        self.assertEqual(self.tree_fingerprint(),before)

    def test_invalid_source_is_lexically_rejected_before_output_parent_queries(self):
        output=self.base/'must-not-create'/'backup.db'
        before=self.tree_fingerprint()
        count=len(_NATIVE_OPEN_EVENTS)
        with self.assertRaises(ValueError):
            backup_database('\\\\?\\UNC\\not-queried.invalid\\share\\source.db',output)
        self.assertEqual(len(_NATIVE_OPEN_EVENTS),count)
        self.assertEqual(self.tree_fingerprint(),before)

    def recovery_fixture(self):
        self.close_engine()
        logical = self.base / (self.path.name + '.rollback-synthetic')
        prepared = self.base / '.sqlite-restore-synthetic.db'
        native = self.base / (self.path.name + '.original-synthetic')
        backup_database(self.path, logical)
        backup_database(self.path, prepared)
        with closing(sqlite3.connect(prepared)) as db:
            _freeze_restored_tasks(db)
            db.commit()
        flush_file(prepared)
        payload = {'version': 1, 'database': self.path.name, 'original_hash': _file_hash(self.path),
            'prepared_hash': _file_hash(prepared), 'original_identity': list(file_identity(self.path)),
            'prepared_identity': list(file_identity(prepared)), 'logical_rollback': logical.name,
            'native_original': native.name, 'prepared': prepared.name, 'sidecars': []}
        _write_recovery_marker(self.path, payload)
        return payload, prepared, native, logical

    def test_native_backup_no_clobber_and_all_handles_close_before_rename(self):
        destination=self.base/'backup.db'
        backup_database(self.path,destination)
        self.assertTrue(verify_database(destination))
        renamed=self.base/'renamed.db'
        move_no_replace(destination,renamed)
        self.assertFalse(destination.exists())
        self.assertTrue(verify_database(renamed))
        destination.write_bytes(b'preserve synthetic destination')
        with self.assertRaises(OSError):
            move_no_replace(renamed,destination)
        self.assertEqual(destination.read_bytes(),b'preserve synthetic destination')
        self.assertTrue(renamed.exists())

    def test_backup_rejects_real_ntfs_hardlink_without_skipping_it(self):
        linked=self.base/'hardlink.db'
        os.link(self.path,linked)
        try:
            with self.assertRaises(ValueError):
                backup_database(linked,self.base/'must-not-exist.db')
        finally:
            linked.unlink()
        self.assertFalse((self.base/'must-not-exist.db').exists())

    def test_wal_reader_blocks_restore_without_removing_committed_sidecar(self):
        self.engine.db.execute('PRAGMA journal_mode=WAL')
        backup=self.base/'before.db'
        backup_database(self.path,backup)
        reader=sqlite3.connect(self.path)
        try:
            reader.execute('BEGIN')
            reader.execute('SELECT count(*) FROM settings').fetchone()
            with self.engine.db:
                self.engine.db.execute("INSERT INTO settings VALUES ('new-committed-synthetic-marker','kept')")
            self.close_engine()
            wal=Path(str(self.path)+'-wal')
            original_main=self.path.read_bytes()
            original_wal=wal.read_bytes()
            with self.assertRaises(RuntimeError):
                restore_database(self.path,backup,True)
            self.assertEqual(self.path.read_bytes(),original_main)
            self.assertEqual(wal.read_bytes(),original_wal)
            self.assertFalse(restore_recovery_pending(self.path))
        finally:
            reader.close()

    def test_marker_recovery_verifies_original_and_preserves_logical_rollback(self):
        payload,prepared,native,logical=self.recovery_fixture()
        self.assertTrue(restore_recovery_pending(self.path))
        result=recover_restore(self.path,True)
        self.assertEqual(result['status'],'original_database_preserved')
        self.assertEqual(_file_hash(self.path),payload['original_hash'])
        self.assertTrue(logical.exists())
        self.assertFalse(prepared.exists())
        self.assertFalse(restore_recovery_pending(self.path))

    def test_documented_partial_rename_state_restores_missing_original_name(self):
        payload,prepared,native,logical=self.recovery_fixture()
        before=file_security_snapshot(self.path)
        # Real file state matching the documented native failure, not a fake API.
        move_no_replace(self.path,native)
        result=recover_restore(self.path,True)
        self.assertEqual(result['status'],'original_database_restored')
        self.assertEqual(_file_hash(self.path),payload['original_hash'])
        self.assertTrue(logical.exists())
        self.assertFalse(native.exists())
        self.assertFalse(restore_recovery_pending(self.path))
        after=file_security_snapshot(self.path)
        record_security_comparison('missing-name-rollback',before,after,'state construction using real native MoveNoReplace; error 1177 not observed')
        self.assertEqual(before['descriptor'],after['descriptor'])
        self.assertEqual(before['dacl'],after['dacl'])
        self.assertEqual(before['owner'],after['owner'])
        self.assertEqual(before['group'],after['group'])

    def test_normal_restore_preserves_dacl_and_records_descriptor_flags(self):
        backup=self.base/'selected-synthetic-backup.db'
        backup_database(self.path,backup)
        with self.engine.db:
            self.engine.db.execute("INSERT INTO settings VALUES ('current-synthetic-marker','retained-in-rollback')")
        self.close_engine()
        before=file_security_snapshot(self.path)
        result=restore_database(self.path,backup,True)
        self.assertEqual(result['status'],'restored_paused')
        after=file_security_snapshot(self.path)
        record_security_comparison('normal-restore',before,after,'observed successful native ReplaceFileW restore')
        self.assertEqual(before['dacl'],after['dacl'])
        self.assertEqual(before['owner'],after['owner'])
        self.assertEqual(before['group'],after['group'])
        self.assertIn(after['control'],(before['control'],before['control']|0x0400))
        # Preserve and report raw inequality. Only addition of Windows'
        # automatic inheritance marker is permitted; no byte is normalized.
        self.assertEqual(before['descriptor'][:2],after['descriptor'][:2])
        self.assertEqual(before['descriptor'][4:],after['descriptor'][4:])
        self.assertEqual(before['revision'],after['revision'])

    def test_actual_native_replacement_can_be_confirmed_after_missing_receipt(self):
        payload,prepared,native,logical=self.recovery_fixture()
        replace_file(self.path,prepared,native)
        self.assertTrue(native.exists())
        result=recover_restore(self.path,True)
        self.assertEqual(result['status'],'restored_paused')
        self.assertTrue(result['manual_safety_review_required'])
        self.assertTrue(native.exists())
        self.assertTrue(logical.exists())
        self.assertFalse(restore_recovery_pending(self.path))

    def test_real_native_replace_sharing_failure_preserves_original_and_can_recover(self):
        from reception.platform_fs import _api
        payload,prepared,native,logical=self.recovery_fixture()
        before=file_security_snapshot(self.path)
        api=_api()
        # Deny the delete/write access requested by the real ReplaceFileW call.
        handle=api.open(prepared,access=0x80000000,share=1)
        try:
            with self.assertRaises(OSError) as failure:
                replace_file(self.path,prepared,native)
            self.assertEqual(failure.exception.winerror,32)
            self.assertEqual(_file_hash(self.path),payload['original_hash'])
            self.assertTrue(prepared.exists())
            self.assertTrue(logical.exists())
            self.assertTrue(restore_recovery_pending(self.path))
        finally:
            api.dll.CloseHandle(handle)
        after=file_security_snapshot(self.path)
        self.assertEqual(before['descriptor'],after['descriptor'])
        self.assertEqual(before['dacl'],after['dacl'])
        record_security_comparison('native-sharing-failure-32',before,after,'observed native ReplaceFileW failure: ERROR_SHARING_VIOLATION (32); no fault mock')
        result=recover_restore(self.path,True)
        self.assertEqual(result['status'],'original_database_preserved')
        self.assertFalse(restore_recovery_pending(self.path))

    def test_unknown_target_keeps_marker_and_all_recovery_evidence(self):
        payload,prepared,native,logical=self.recovery_fixture()
        with closing(sqlite3.connect(self.path)) as db:
            db.execute("INSERT INTO settings VALUES ('unexpected-synthetic-content','review')")
            db.commit()
        original=self.path.read_bytes()
        with self.assertRaises(RestoreRecoveryRequired):
            recover_restore(self.path,True)
        self.assertEqual(self.path.read_bytes(),original)
        self.assertTrue(prepared.exists())
        self.assertTrue(logical.exists())
        self.assertTrue(restore_recovery_pending(self.path))

    def test_malformed_marker_never_creates_a_missing_database(self):
        self.close_engine()
        self.path.unlink()
        _recovery_marker(self.path).write_text('{"unexpected":"synthetic"}',encoding='utf-8')
        with self.assertRaises(RestoreRecoveryRequired):
            recover_restore(self.path,True)
        self.assertFalse(self.path.exists())
        self.assertTrue(restore_recovery_pending(self.path))

    def test_recovery_requires_offline_confirmation_and_refuses_active_owner(self):
        self.recovery_fixture()
        with self.assertRaises(ValueError):
            recover_restore(self.path)
        with service_lock(self.path):
            with self.assertRaises(RuntimeError):
                recover_restore(self.path,True)

    def test_dacl_and_current_token_are_evaluated_by_real_windows_api(self):
        allowed,granted=current_token_file_access(self.path)
        self.assertTrue(allowed)
        self.assertTrue(granted & 1)
        # Current-token success is functional access, not private-directory proof.

    def test_private_directory_cross_user_access_remains_blocked(self):
        self.skipTest('Private customer-data directory and an authorized independent user token are not provided; candidate/blocked; ACLs unchanged')
