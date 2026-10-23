"""Windows stage barrier and Job Object ownership for the offline check runner."""
from __future__ import annotations
import ctypes
import json
import subprocess
import sys
import time
from ctypes import wintypes as W


class Accounting(ctypes.Structure):
    _fields_ = [(name, ctypes.c_int64) for name in ('user', 'kernel', 'periodUser', 'periodKernel')] + [
        (name, W.DWORD) for name in ('faults', 'total', 'active', 'terminated')]


class BasicLimit(ctypes.Structure):
    _fields_ = [('processTime', ctypes.c_int64), ('jobTime', ctypes.c_int64),
                ('flags', W.DWORD), ('minimumWorkingSet', ctypes.c_size_t),
                ('maximumWorkingSet', ctypes.c_size_t), ('activeProcessLimit', W.DWORD),
                ('affinity', ctypes.c_size_t), ('priorityClass', W.DWORD), ('schedulingClass', W.DWORD)]


class IoCounters(ctypes.Structure):
    _fields_ = [(name, ctypes.c_uint64) for name in ('readOperations', 'writeOperations',
                'otherOperations', 'readBytes', 'writeBytes', 'otherBytes')]


class ExtendedLimit(ctypes.Structure):
    _fields_ = [('basic', BasicLimit), ('io', IoCounters),
                ('processMemory', ctypes.c_size_t), ('jobMemory', ctypes.c_size_t),
                ('peakProcessMemory', ctypes.c_size_t), ('peakJobMemory', ctypes.c_size_t)]


class WindowsJob:
    def __init__(self, process):
        self.k = ctypes.WinDLL('kernel32', use_last_error=True)
        for name, args, result in (
            ('CreateJobObjectW', [ctypes.c_void_p, W.LPCWSTR], W.HANDLE),
            ('AssignProcessToJobObject', [W.HANDLE, W.HANDLE], W.BOOL),
            ('SetInformationJobObject', [W.HANDLE, ctypes.c_int, ctypes.c_void_p, W.DWORD], W.BOOL),
            ('QueryInformationJobObject', [W.HANDLE, ctypes.c_int, ctypes.c_void_p, W.DWORD, ctypes.c_void_p], W.BOOL),
            ('TerminateJobObject', [W.HANDLE, W.UINT], W.BOOL),
            ('CloseHandle', [W.HANDLE], W.BOOL)):
            getattr(self.k, name).argtypes, getattr(self.k, name).restype = args, result
        self.handle = self.k.CreateJobObjectW(None, None)
        if not self.handle:
            raise ctypes.WinError(ctypes.get_last_error())
        try:
            limits = ExtendedLimit()
            limits.basic.flags = 0x2000  # JOB_OBJECT_LIMIT_KILL_ON_JOB_CLOSE
            if not self.k.SetInformationJobObject(self.handle, 9, ctypes.byref(limits), ctypes.sizeof(limits)):
                raise ctypes.WinError(ctypes.get_last_error())
            if not self.k.AssignProcessToJobObject(self.handle, W.HANDLE(int(process._handle))):
                raise ctypes.WinError(ctypes.get_last_error())
        except BaseException:
            self.k.CloseHandle(self.handle)
            self.handle = None
            raise

    def active(self):
        if not self.handle:
            return 0
        data = Accounting()
        if not self.k.QueryInformationJobObject(self.handle, 1, ctypes.byref(data), ctypes.sizeof(data), None):
            raise ctypes.WinError(ctypes.get_last_error())
        return data.active

    def wait_empty(self, timeout=5):
        deadline = time.monotonic() + timeout
        while self.active():
            if time.monotonic() >= deadline:
                return False
            time.sleep(.025)
        return True

    def terminate(self, code=99):
        if self.handle and not self.k.TerminateJobObject(self.handle, code):
            raise ctypes.WinError(ctypes.get_last_error())

    def close(self):
        if self.handle:
            try:
                self.terminate()
                if not self.wait_empty(timeout=5):
                    raise RuntimeError('Windows Job Object descendants did not exit after termination')
            finally:
                handle, self.handle = self.handle, None
                if not self.k.CloseHandle(handle):
                    raise ctypes.WinError(ctypes.get_last_error())


if __name__ == '__main__':
    # Parent assigns the wrapper before allowing it to create any descendants.
    if sys.stdin.buffer.read(1) != b'G':
        raise SystemExit(99)
    command = json.loads(sys.argv[1])
    if '--forward-stdin' in sys.argv[2:]:
        payload = sys.stdin.buffer.read()
        raise SystemExit(subprocess.run(command, input=payload).returncode)
    raise SystemExit(subprocess.call(command, stdin=subprocess.DEVNULL))
