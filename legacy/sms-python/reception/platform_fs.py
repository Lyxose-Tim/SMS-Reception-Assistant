"""Native local-file ownership for Windows NTFS and the existing Linux path.

This coordinates participating application processes. Directory ACLs remain an
operator responsibility; it cannot make shared/customer-data directories private.
"""
from contextlib import contextmanager
import ctypes
import os
from pathlib import Path
import re
import stat
import sys
import time


def _windows():
    return os.name == 'nt'


class _WindowsAPI:
    def __init__(self):
        from ctypes import wintypes as w
        self.w = w
        self.dll = ctypes.WinDLL('kernel32', use_last_error=True)
        class FileInfo(ctypes.Structure):
            _fields_ = [('attributes', w.DWORD), ('creation', w.FILETIME),
                        ('access', w.FILETIME), ('write', w.FILETIME),
                        ('volume', w.DWORD), ('size_high', w.DWORD), ('size_low', w.DWORD),
                        ('links', w.DWORD), ('index_high', w.DWORD), ('index_low', w.DWORD)]
        class Overlapped(ctypes.Structure):
            _fields_ = [('internal', ctypes.c_size_t), ('internal_high', ctypes.c_size_t),
                        ('offset', w.DWORD), ('offset_high', w.DWORD), ('event', w.HANDLE)]
        self.FileInfo, self.Overlapped = FileInfo, Overlapped
        definitions = {
            'CreateFileW': ([w.LPCWSTR, w.DWORD, w.DWORD, ctypes.c_void_p, w.DWORD, w.DWORD, w.HANDLE], w.HANDLE),
            'CloseHandle': ([w.HANDLE], w.BOOL),
            'GetFileType': ([w.HANDLE], w.DWORD),
            'GetFileInformationByHandle': ([w.HANDLE, ctypes.POINTER(FileInfo)], w.BOOL),
            'GetFinalPathNameByHandleW': ([w.HANDLE, w.LPWSTR, w.DWORD, w.DWORD], w.DWORD),
            'GetLongPathNameW': ([w.LPCWSTR, w.LPWSTR, w.DWORD], w.DWORD),
            'GetShortPathNameW': ([w.LPCWSTR, w.LPWSTR, w.DWORD], w.DWORD),
            'GetVolumePathNameW': ([w.LPCWSTR, w.LPWSTR, w.DWORD], w.BOOL),
            'GetDriveTypeW': ([w.LPCWSTR], w.UINT),
            'GetVolumeInformationW': ([w.LPCWSTR, w.LPWSTR, w.DWORD, ctypes.POINTER(w.DWORD), ctypes.POINTER(w.DWORD), ctypes.POINTER(w.DWORD), w.LPWSTR, w.DWORD], w.BOOL),
            'LockFileEx': ([w.HANDLE, w.DWORD, w.DWORD, w.DWORD, w.DWORD, ctypes.POINTER(Overlapped)], w.BOOL),
            'UnlockFileEx': ([w.HANDLE, w.DWORD, w.DWORD, w.DWORD, ctypes.POINTER(Overlapped)], w.BOOL),
            'FlushFileBuffers': ([w.HANDLE], w.BOOL),
            'MoveFileExW': ([w.LPCWSTR, w.LPCWSTR, w.DWORD], w.BOOL),
            'ReplaceFileW': ([w.LPCWSTR, w.LPCWSTR, w.LPCWSTR, w.DWORD, ctypes.c_void_p, ctypes.c_void_p], w.BOOL),
            'DeviceIoControl': ([w.HANDLE, w.DWORD, ctypes.c_void_p, w.DWORD, ctypes.c_void_p, w.DWORD, ctypes.POINTER(w.DWORD), ctypes.c_void_p], w.BOOL),
        }
        for name, (arguments, result) in definitions.items():
            function = getattr(self.dll, name)
            function.argtypes, function.restype = arguments, result

    @staticmethod
    def error():
        return ctypes.WinError(ctypes.get_last_error())

    def open(self, path, *, access=0x80, share=7, disposition=3, follow=False):
        # BACKUP_SEMANTICS permits directory metadata probes without privileges.
        flags = 0x02000000 | (0 if follow else 0x00200000)
        sys.audit('harbor.native_fs_open', str(path), not follow, flags, access, share, disposition)
        handle = self.dll.CreateFileW(str(path), access, share, None, disposition, flags, None)
        if handle == ctypes.c_void_p(-1).value:
            raise self.error()
        return handle

    def info(self, handle, *, regular=False):
        metadata = self.FileInfo()
        if not self.dll.GetFileInformationByHandle(handle, ctypes.byref(metadata)):
            raise self.error()
        if self.dll.GetFileType(handle) != 1:
            raise ValueError('Only local disk files are supported')
        if regular and (metadata.attributes & (0x10 | 0x400) or metadata.links != 1):
            raise ValueError('File must be regular, without reparse points or hard-link aliases')
        return metadata

    def final(self, handle, guid=False):
        size = 512
        while size <= 32768:
            buffer = ctypes.create_unicode_buffer(size)
            needed = self.dll.GetFinalPathNameByHandleW(handle, buffer, size, 1 if guid else 0)
            if not needed:
                raise self.error()
            if needed < size:
                return self.long(buffer.value)
            size = needed + 1
        raise ValueError('Canonical path exceeds the Windows API limit')

    def long(self, path):
        size = 512
        while size <= 32768:
            buffer = ctypes.create_unicode_buffer(size)
            needed = self.dll.GetLongPathNameW(str(path), buffer, size)
            if not needed:
                raise self.error()
            if needed < size:
                return buffer.value
            size = needed + 1
        raise ValueError('Canonical path exceeds the Windows API limit')

    def local_ntfs(self, path):
        # Reject mapped/network drives before any file or volume-path probe.
        value = str(path)
        drive = value[4:7] if value.startswith('\\\\?\\') else value[:3]
        kind = self.dll.GetDriveTypeW(drive) if re.fullmatch(r'[A-Za-z]:\\', drive) else None
        sys.audit('harbor.native_drive_checked', drive, kind)
        if kind != 3:
            raise ValueError('Windows service data requires a local fixed NTFS drive')
        root = ctypes.create_unicode_buffer(32768)
        if not self.dll.GetVolumePathNameW(str(path), root, len(root)):
            raise self.error()
        filesystem = ctypes.create_unicode_buffer(32)
        if self.dll.GetDriveTypeW(root.value) != 3:
            raise ValueError('Windows service data requires a local fixed NTFS drive')
        if not self.dll.GetVolumeInformationW(root.value, None, 0, None, None, None, filesystem, len(filesystem)):
            raise self.error()
        if filesystem.value.upper() != 'NTFS':
            raise ValueError('Windows service data requires NTFS')
        sys.audit('harbor.native_filesystem_checked', root.value, filesystem.value)

    def local_reparse_target(self, handle, source):
        buffer = ctypes.create_string_buffer(16384)
        returned = self.w.DWORD()
        if not self.dll.DeviceIoControl(handle, 0x900A8, None, 0, buffer, len(buffer), ctypes.byref(returned), None):
            raise self.error()
        data = buffer.raw[:returned.value]
        if len(data) < 16:
            raise ValueError('Reparse metadata is invalid')
        tag = int.from_bytes(data[:4], 'little')
        if tag not in (0xA0000003, 0xA000000C):
            raise ValueError('Unsupported ancestor reparse point')
        start = int.from_bytes(data[8:10], 'little')
        length = int.from_bytes(data[10:12], 'little')
        base = 20 if tag == 0xA000000C else 16
        if base + start + length > len(data) or length % 2:
            raise ValueError('Reparse metadata is invalid')
        target = data[base + start:base + start + length].decode('utf-16-le')
        relative = tag == 0xA000000C and int.from_bytes(data[16:20], 'little') & 1
        sys.audit('harbor.native_reparse_target_read', str(source), target, tag, bool(relative))
        if relative:
            target = str(Path(source).parent / target)
        else:
            if target.startswith('\\??\\'):
                target = target[4:]
            if not re.match(r'^[A-Za-z]:\\', target):
                raise ValueError('Ancestor reparse target must be a local drive path')
        validated = _windows_input(target)
        sys.audit('harbor.native_reparse_target_validated', str(source), str(validated))
        return validated


_api_instance = None


def _api():
    global _api_instance
    if _api_instance is None:
        _api_instance = _WindowsAPI()
    return _api_instance


def _windows_input(value):
    raw = os.fspath(value)
    if not isinstance(raw, str) or not raw or any(ord(c) < 32 for c in raw):
        raise ValueError('Windows path must be nonempty text without controls')
    raw = raw.replace('/', '\\')
    if raw.startswith('\\\\?\\'):
        raw = raw[4:]
        if not re.match(r'^[A-Za-z]:\\', raw):
            raise ValueError('Extended UNC and device Windows paths are unsupported')
    if raw.startswith('\\') or re.match(r'^[A-Za-z]:[^\\]', raw) or re.fullmatch(r'[A-Za-z]:', raw):
        raise ValueError('UNC, device and drive-relative Windows paths are unsupported')
    for index, component in enumerate(raw.split('\\')):
        if not component or component in ('.', '..') or index == 0 and re.fullmatch(r'[A-Za-z]:', component):
            continue
        stem = component.split('.', 1)[0].upper()
        if (component.endswith((' ', '.')) or any(c in component for c in '<>:"|?*') or
                stem in {'CON', 'PRN', 'AUX', 'NUL'} or re.fullmatch(r'(COM|LPT)[1-9¹²³]', stem)):
            raise ValueError('Device, alternate-stream and ambiguous Windows names are unsupported')
    path = Path(os.path.abspath(raw))
    if not re.match(r'^[A-Za-z]:\\', str(path)):
        raise ValueError('Only local Windows drive paths are supported')
    for component in path.parts[1:]:
        stem = component.split('.', 1)[0].upper()
        if (component.endswith((' ', '.')) or ':' in component or
                stem in {'CON', 'PRN', 'AUX', 'NUL'} or
                re.fullmatch(r'(COM|LPT)[1-9¹²³]', stem)):
            raise ValueError('Device, alternate-stream and ambiguous Windows names are unsupported')
    return path


def _dos_path(value):
    if not re.match(r'^\\\\\?\\[A-Za-z]:\\', value):
        raise ValueError('Canonical Windows path must name a local drive')
    return Path(value[4:])


def _windows_description(value, allow_create=False):
    api = _api()
    original = _walk_windows_path(_windows_input(value), allow_create=allow_create)
    handle = None
    try:
        handle = api.open(original)
    except OSError as exc:
        if not allow_create or exc.winerror not in (2, 3):
            raise
    if handle is not None:
        try:
            metadata = api.info(handle, regular=True)
            dos, guid = api.final(handle), api.final(handle, guid=True)
            api.local_ntfs(dos)
            return _dos_path(dos), guid, (metadata.volume, (metadata.index_high << 32) | metadata.index_low)
        finally:
            api.dll.CloseHandle(handle)
    # Only parents may be junction aliases. Existing leaf reparse points never
    # reach this branch because their metadata probe succeeds and is rejected.
    parent = api.open(original.parent, follow=True)
    try:
        metadata = api.info(parent)
        if not metadata.attributes & 0x10:
            raise ValueError('Database parent must be an existing directory')
        dos, guid = api.final(parent), api.final(parent, guid=True)
        api.local_ntfs(dos)
        return _dos_path(dos) / original.name, guid.rstrip('\\') + '\\' + original.name, None
    finally:
        api.dll.CloseHandle(parent)


def _walk_windows_path(original, allow_create=False, directory=False, reject_new_alias=False, depth=0):
    """Probe one component at a time so unknown/remote reparse targets stay unopened."""
    if depth > 32:
        raise ValueError('Ancestor reparse chain is too deep or cyclic')
    api = _api()
    api.local_ntfs(original.anchor)
    handle = api.open(original.anchor)
    try:
        api.info(handle)
        current = _dos_path(api.final(handle))
        sys.audit('harbor.native_parent_resolved', str(original.anchor), str(current))
    finally:
        api.dll.CloseHandle(handle)
    components = original.parts[1:]
    alias_seen = False
    for index, component in enumerate(components):
        candidate = current / component
        sys.audit('harbor.native_component_probe', str(current), str(candidate))
        try:
            handle = api.open(candidate)
        except OSError as exc:
            if not allow_create or exc.winerror not in (2, 3):
                raise
            if reject_new_alias and alias_seen:
                raise ValueError('New files require an existing parent without reparse aliases')
            return current.joinpath(*components[index:])
        try:
            metadata = api.info(handle)
            final = index == len(components) - 1
            if metadata.attributes & 0x400:
                if final:
                    raise ValueError('Leaf reparse points are unsupported')
                target = api.local_reparse_target(handle, candidate)
                current = _walk_windows_path(target, directory=True, depth=depth + 1)
                alias_seen = True
                continue
            if not final or directory:
                if not metadata.attributes & 0x10:
                    raise ValueError('Creation ancestor must be a directory')
            else:
                api.info(handle, regular=True)
            current = _dos_path(api.final(handle))
            sys.audit('harbor.native_parent_resolved', str(candidate), str(current))
        finally:
            api.dll.CloseHandle(handle)
    return current


class MaintenanceLockTimeout(RuntimeError):
    """A participating backup/offline operation did not release its lease."""


class NativePlatform:
    @staticmethod
    def validate_lexical_path(value):
        """No metadata probes: reject invalid raw inputs before any target IO."""
        return _windows_input(value) if _windows() else Path(os.path.abspath(value))

    @staticmethod
    def validate_creation_path(value, directory=False):
        """Validate before exists/mkdir/open; create no directory or file."""
        if not _windows():
            original = Path(os.path.abspath(value))
            if os.path.lexists(original):
                if directory:
                    if original.is_symlink() or not original.is_dir():
                        raise ValueError('Creation directory must be regular and not a symlink')
                    return original.resolve()
                return NativePlatform.canonical_path(original)
            missing = []
            ancestor = original
            while not os.path.lexists(ancestor):
                missing.insert(0, ancestor.name)
                ancestor = ancestor.parent
            if not ancestor.is_dir():
                raise ValueError('Creation parent must be a directory')
            return ancestor.resolve().joinpath(*missing)
        original = _windows_input(value)
        return _walk_windows_path(original, allow_create=True, directory=directory, reject_new_alias=True)

    @staticmethod
    def canonical_path(value, allow_create=False):
        if _windows():
            return _windows_description(value, allow_create)[0]
        original = Path(os.path.abspath(value))
        if original.is_symlink():
            raise ValueError('Database must not be a symlink')
        path = original.parent.resolve() / original.name
        try:
            metadata = path.lstat()
        except FileNotFoundError:
            if allow_create:
                return path
            raise ValueError('Database must be an existing regular single-link file') from None
        if not stat.S_ISREG(metadata.st_mode) or metadata.st_nlink != 1:
            raise ValueError('Database must be a regular single-link file, without symlink or hard-link aliases')
        return path

    @staticmethod
    @contextmanager
    def lock_database(value):
        if _windows():
            api = _api()
            database, guid, identity = _windows_description(value, allow_create=True)
            handle = None
            locked = False
            overlapped = api.Overlapped()
            try:
                try:
                    handle = api.open(guid + '.service.lock', access=0xC0000000, share=3, disposition=4)
                except OSError as exc:
                    if exc.winerror in (32, 33):
                        raise RuntimeError('Service/database lock is active; stop the service before this operation') from None
                    raise
                metadata = api.info(handle, regular=True)
                if not api.dll.LockFileEx(handle, 3, 0, 1, 0, ctypes.byref(overlapped)):
                    code = ctypes.get_last_error()
                    if code in (32, 33):
                        raise RuntimeError('Service/database lock is active; stop the service before this operation') from None
                    raise ctypes.WinError(code)
                locked = True
                check = api.open(guid + '.service.lock')
                try:
                    current = api.info(check, regular=True)
                    if (current.volume, current.index_high, current.index_low) != (metadata.volume, metadata.index_high, metadata.index_low):
                        raise ValueError('Service lock pathname changed while acquiring ownership')
                finally:
                    api.dll.CloseHandle(check)
                canonical, current_guid, current_identity = _windows_description(database, allow_create=True)
                if current_guid.casefold() != guid.casefold() or identity is not None and current_identity != identity:
                    raise ValueError('Database pathname changed while acquiring ownership')
                yield canonical
            finally:
                if handle is not None:
                    if locked:
                        api.dll.UnlockFileEx(handle, 0, 1, 0, ctypes.byref(overlapped))
                    api.dll.CloseHandle(handle)
            return
        if not sys.platform.startswith('linux'):
            raise RuntimeError('Native database ownership supports Windows NTFS and Linux only')
        import fcntl
        database = NativePlatform.canonical_path(value, allow_create=True)
        path = Path(str(database) + '.service.lock')
        path.parent.mkdir(parents=True, exist_ok=True)
        fd = os.open(path, os.O_RDWR | os.O_CREAT | os.O_NOFOLLOW, 0o600)
        try:
            metadata = os.fstat(fd)
            if not stat.S_ISREG(metadata.st_mode) or metadata.st_nlink != 1:
                raise ValueError('Service lock must be a regular single-link file')
            try:
                fcntl.flock(fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
            except BlockingIOError:
                raise RuntimeError('Service/database lock is active; stop the service before this operation') from None
            current = path.lstat()
            if not stat.S_ISREG(current.st_mode) or current.st_nlink != 1 or (current.st_dev, current.st_ino) != (metadata.st_dev, metadata.st_ino):
                raise ValueError('Service lock pathname changed while acquiring ownership')
            os.fchmod(fd, 0o600)
            NativePlatform.canonical_path(database, allow_create=True)
            yield database
        finally:
            os.close(fd)

    @staticmethod
    def file_identity(path):
        if _windows():
            return _windows_description(path)[2]
        metadata = NativePlatform.canonical_path(path).stat()
        return metadata.st_dev, metadata.st_ino

    @staticmethod
    @contextmanager
    def maintenance_lock(value, *, exclusive=False, timeout_seconds=5):
        """Backup readers share this lease; offline destructive operations exclude them.

        The service lease is separate: normal business writes remain possible.
        Callers needing both leases always acquire service ownership first.
        Missing main files are permitted solely so interrupted restores can recover.
        """
        import math
        if (type(exclusive) is not bool or type(timeout_seconds) not in (int, float) or
                not 0 < timeout_seconds <= 120 or not math.isfinite(timeout_seconds)):
            raise ValueError('Maintenance lock timeout must be a finite number greater than zero and at most 120 seconds')
        deadline = time.monotonic() + timeout_seconds
        def wait():
            remaining = deadline - time.monotonic()
            if remaining <= 0:
                raise MaintenanceLockTimeout('Database maintenance lock timed out; another backup or offline operation is active')
            time.sleep(min(.05, remaining))
        if _windows():
            api = _api()
            database, guid, identity = _windows_description(value, allow_create=True)
            handle = None
            locked = False
            overlapped = api.Overlapped()
            try:
                while handle is None:
                    try:
                        handle = api.open(guid + '.maintenance.lock', access=0xC0000000, share=3, disposition=4)
                    except OSError as exc:
                        if exc.winerror not in (32, 33):
                            raise
                        wait()
                metadata = api.info(handle, regular=True)
                while not api.dll.LockFileEx(handle, 3 if exclusive else 1, 0, 1, 0, ctypes.byref(overlapped)):
                    code = ctypes.get_last_error()
                    if code not in (32, 33):
                        raise ctypes.WinError(code)
                    wait()
                locked = True
                if time.monotonic() >= deadline:
                    raise MaintenanceLockTimeout('Database maintenance lock timed out; another backup or offline operation is active')
                check = api.open(guid + '.maintenance.lock')
                try:
                    current = api.info(check, regular=True)
                    if (current.volume, current.index_high, current.index_low) != (metadata.volume, metadata.index_high, metadata.index_low):
                        raise ValueError('Maintenance lock pathname changed while acquiring ownership')
                finally:
                    api.dll.CloseHandle(check)
                canonical, current_guid, current_identity = _windows_description(database, allow_create=True)
                if current_guid.casefold() != guid.casefold() or current_identity != identity:
                    raise ValueError('Database pathname changed while waiting for maintenance ownership; retry the operation')
                yield canonical
            finally:
                if handle is not None:
                    if locked:
                        api.dll.UnlockFileEx(handle, 0, 1, 0, ctypes.byref(overlapped))
                    api.dll.CloseHandle(handle)
            return
        if not sys.platform.startswith('linux'):
            raise RuntimeError('Native maintenance ownership supports Windows NTFS and Linux only')
        import fcntl
        database = NativePlatform.canonical_path(value, allow_create=True)
        identity = NativePlatform.file_identity(database) if database.exists() else None
        path = Path(str(database) + '.maintenance.lock')
        fd = os.open(path, os.O_RDWR | os.O_CREAT | os.O_NOFOLLOW, 0o600)
        try:
            metadata = os.fstat(fd)
            if not stat.S_ISREG(metadata.st_mode) or metadata.st_nlink != 1:
                raise ValueError('Maintenance lock must be a regular single-link file')
            while True:
                try:
                    fcntl.flock(fd, (fcntl.LOCK_EX if exclusive else fcntl.LOCK_SH) | fcntl.LOCK_NB)
                    break
                except BlockingIOError:
                    wait()
            if time.monotonic() >= deadline:
                raise MaintenanceLockTimeout('Database maintenance lock timed out; another backup or offline operation is active')
            current = path.lstat()
            if (not stat.S_ISREG(current.st_mode) or current.st_nlink != 1 or
                    (current.st_dev, current.st_ino) != (metadata.st_dev, metadata.st_ino)):
                raise ValueError('Maintenance lock pathname changed while acquiring ownership')
            os.fchmod(fd, 0o600)
            canonical = NativePlatform.canonical_path(database, allow_create=True)
            current_identity = NativePlatform.file_identity(canonical) if canonical.exists() else None
            if current_identity != identity:
                raise ValueError('Database pathname changed while waiting for maintenance ownership; retry the operation')
            yield canonical
        finally:
            os.close(fd)

    @staticmethod
    def flush_file(path):
        if not _windows():
            with Path(path).open('rb') as handle:
                os.fsync(handle.fileno())
            return
        api = _api()
        canonical = NativePlatform.canonical_path(path)
        handle = api.open(canonical, access=0xC0000000, share=3)
        try:
            api.info(handle, regular=True)
            if not api.dll.FlushFileBuffers(handle):
                raise api.error()
        finally:
            api.dll.CloseHandle(handle)

    @staticmethod
    def move_no_replace(source, target):
        if _windows():
            if not _api().dll.MoveFileExW(str(source), str(target), 8):
                raise _api().error()
        else:
            os.link(source, target)
            Path(source).unlink()

    @staticmethod
    def replace_file(target, prepared, native_backup):
        if not _windows():
            raise RuntimeError('ReplaceFileW is a Windows-only installation operation')
        if os.path.lexists(native_backup):
            raise FileExistsError('Native restore backup destination must be new')
        if not _api().dll.ReplaceFileW(str(target), str(prepared), str(native_backup), 0, None, None):
            raise _api().error()

    @staticmethod
    @contextmanager
    def exclusive_database_probe(path):
        if not _windows():
            yield
            return
        api = _api()
        handle = api.open(NativePlatform.canonical_path(path), access=0xC0000000, share=0)
        try:
            api.info(handle, regular=True)
            yield
        finally:
            api.dll.CloseHandle(handle)


# Operations keeps its public helpers; both styles share the same native code.
canonical_path = NativePlatform.canonical_path
lock_database = NativePlatform.lock_database
maintenance_lock = NativePlatform.maintenance_lock
file_identity = NativePlatform.file_identity
flush_file = NativePlatform.flush_file
move_no_replace = NativePlatform.move_no_replace
replace_file = NativePlatform.replace_file
exclusive_database_probe = NativePlatform.exclusive_database_probe
validate_creation_path = NativePlatform.validate_creation_path
validate_lexical_path = NativePlatform.validate_lexical_path
