"""Native self-test proving command timeouts and parent exits cannot hide descendants."""
from __future__ import annotations

import ctypes
import json
import subprocess
import sys
import time
from ctypes import wintypes as W
from pathlib import Path


def is_alive(pid, timeout=0):
    kernel = ctypes.WinDLL('kernel32', use_last_error=True)
    kernel.OpenProcess.argtypes = [W.DWORD, W.BOOL, W.DWORD]
    kernel.OpenProcess.restype = W.HANDLE
    kernel.WaitForSingleObject.argtypes = [W.HANDLE, W.DWORD]
    kernel.WaitForSingleObject.restype = W.DWORD
    kernel.GetExitCodeProcess.argtypes = [W.HANDLE, ctypes.POINTER(W.DWORD)]
    kernel.GetExitCodeProcess.restype = W.BOOL
    kernel.CloseHandle.argtypes = [W.HANDLE]
    kernel.CloseHandle.restype = W.BOOL
    handle = kernel.OpenProcess(0x00100000 | 0x1000, False, pid)  # SYNCHRONIZE + QUERY_LIMITED_INFORMATION
    if not handle:
        error = ctypes.get_last_error()
        if error == 87:  # ERROR_INVALID_PARAMETER: PID no longer exists
            return False
        raise ctypes.WinError(error)
    try:
        result = kernel.WaitForSingleObject(handle, int(timeout * 1000))
        if result == 0:  # The process is signaled even while its PID can still be opened.
            return False
        if result == 258:
            code = W.DWORD()
            if not kernel.GetExitCodeProcess(handle, ctypes.byref(code)):
                raise ctypes.WinError(ctypes.get_last_error())
            if timeout:
                raise AssertionError(f'Process {pid} did not exit within {timeout}s; exit status={code.value}')
            return True
        if result == 0xFFFFFFFF:
            raise ctypes.WinError(ctypes.get_last_error())
        raise AssertionError(f'Unexpected process wait status {result} for PID {pid}')
    finally:
        kernel.CloseHandle(handle)


def wait_pid(path):
    deadline = time.monotonic() + 10
    while not path.exists():
        if time.monotonic() >= deadline:
            raise AssertionError('Fixture never created its required descendant')
        time.sleep(.025)
    return int(path.read_text(encoding='ascii'))


def run_case(suite):
    root = suite.root / 'process-tree-self-test'
    root.mkdir()
    grandchild = root / 'grandchild.py'
    grandchild.write_text('import os,sys,time\nfrom pathlib import Path\n'
                          'Path(sys.argv[1]).write_text(str(os.getpid()),encoding="ascii")\n'
                          'time.sleep(60)\n', encoding='utf-8')
    child = root / 'child.py'
    child.write_text('import os,subprocess,sys,time\nfrom pathlib import Path\n'
                     'grand=Path(sys.argv[1]); mine=Path(sys.argv[2])\n'
                     'subprocess.Popen([sys.executable,sys.argv[3],str(grand)],stdin=subprocess.DEVNULL,stdout=subprocess.DEVNULL,stderr=subprocess.DEVNULL)\n'
                     'deadline=time.monotonic()+10\n'
                     'while not grand.exists():\n'
                     ' if time.monotonic()>deadline: raise SystemExit(7)\n'
                     ' time.sleep(.025)\n'
                     'mine.write_text(str(os.getpid()),encoding="ascii")\n'
                     'if sys.argv[4]=="wait": time.sleep(60)\n', encoding='utf-8')
    scenarios = []
    for mode in ('exit', 'wait'):
        grand_pid, child_pid = root / (mode + '-grand.pid'), root / (mode + '-child.pid')
        command = [sys.executable, str(child), str(grand_pid), str(child_pid), str(grandchild), mode]
        try:
            suite.run(command, timeout=5, descendant_timeout=.2)
        except subprocess.TimeoutExpired:
            assert mode == 'wait', 'Normal parent exit unexpectedly timed out'
        except RuntimeError as error:
            assert mode == 'exit' and 'descendant' in str(error), error
        else:
            raise AssertionError('Harness falsely passed a command that left a running grandchild')
        for path in (child_pid, grand_pid):
            assert path.exists(), 'The fault fixture never created its full process tree'
            # Job accounting can reach zero before process teardown signals the
            # process object. Observe the actual exit, with a bounded deadline.
            assert not is_alive(wait_pid(path), timeout=5), 'Harness returned while a descendant remained alive'
        scenarios.append({'trigger': mode, 'allDescendantsGone': True})

    # Exercise KILL_ON_JOB_CLOSE itself: bypass explicit TerminateJobObject.
    from stage_process import WindowsJob
    grand_pid, child_pid = root / 'close-grand.pid', root / 'close-child.pid'
    command = [sys.executable, str(child), str(grand_pid), str(child_pid), str(grandchild), 'wait']
    process = subprocess.Popen([sys.executable, str(suite.source / 'tools/stage_process.py'), json.dumps(command)],
                               env=suite.env, stdin=subprocess.PIPE, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
    job = WindowsJob(process)
    try:
        process.stdin.write(b'G'); process.stdin.close()
        ids = [wait_pid(path) for path in (child_pid, grand_pid)]
        handle, job.handle = job.handle, None
        if not job.k.CloseHandle(handle):
            raise ctypes.WinError(ctypes.get_last_error())
        process.wait(timeout=5)
        deadline = time.monotonic() + 5
        while any(is_alive(pid) for pid in ids) and time.monotonic() < deadline:
            time.sleep(.025)
        assert all(not is_alive(pid) for pid in ids), 'KILL_ON_JOB_CLOSE did not terminate descendants'
        scenarios.append({'trigger': 'job-handle-close', 'allDescendantsGone': True})
    finally:
        job.close()
        if process.poll() is None:
            process.kill(); process.wait(timeout=5)
    return {'nativeJobObject': True, 'scenarios': scenarios}
