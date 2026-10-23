"""Minimal stdlib ConPTY driver for native Windows user-interaction acceptance.

The real CLI runs in a pseudoconsole, not a pipe pretending to be a terminal.
A Job Object bounds process lifetime, including any descendant process.
"""
from __future__ import annotations

import ctypes
import os
import re
import subprocess
import threading
import time
from ctypes import wintypes as W


class COORD(ctypes.Structure):
    _fields_ = [("X", ctypes.c_short), ("Y", ctypes.c_short)]


class STARTUPINFO(ctypes.Structure):
    _fields_ = [("cb", W.DWORD), ("lpReserved", W.LPWSTR), ("lpDesktop", W.LPWSTR),
                ("lpTitle", W.LPWSTR), ("dwX", W.DWORD), ("dwY", W.DWORD),
                ("dwXSize", W.DWORD), ("dwYSize", W.DWORD), ("dwXCountChars", W.DWORD),
                ("dwYCountChars", W.DWORD), ("dwFillAttribute", W.DWORD),
                ("dwFlags", W.DWORD), ("wShowWindow", W.WORD), ("cbReserved2", W.WORD),
                ("lpReserved2", ctypes.POINTER(ctypes.c_byte)), ("hStdInput", W.HANDLE),
                ("hStdOutput", W.HANDLE), ("hStdError", W.HANDLE)]


class STARTUPINFOEX(ctypes.Structure):
    _fields_ = [("StartupInfo", STARTUPINFO), ("lpAttributeList", ctypes.c_void_p)]


class PROCESS_INFORMATION(ctypes.Structure):
    _fields_ = [("hProcess", W.HANDLE), ("hThread", W.HANDLE),
                ("dwProcessId", W.DWORD), ("dwThreadId", W.DWORD)]


class ACCOUNTING(ctypes.Structure):
    _fields_ = [("TotalUserTime", ctypes.c_int64), ("TotalKernelTime", ctypes.c_int64),
                ("ThisPeriodTotalUserTime", ctypes.c_int64), ("ThisPeriodTotalKernelTime", ctypes.c_int64),
                ("TotalPageFaultCount", W.DWORD), ("TotalProcesses", W.DWORD),
                ("ActiveProcesses", W.DWORD), ("TotalTerminatedProcesses", W.DWORD)]


class BASIC_LIMIT(ctypes.Structure):
    _fields_ = [("PerProcessUserTimeLimit", ctypes.c_int64), ("PerJobUserTimeLimit", ctypes.c_int64),
                ("LimitFlags", W.DWORD), ("MinimumWorkingSetSize", ctypes.c_size_t),
                ("MaximumWorkingSetSize", ctypes.c_size_t), ("ActiveProcessLimit", W.DWORD),
                ("Affinity", ctypes.c_size_t), ("PriorityClass", W.DWORD), ("SchedulingClass", W.DWORD)]


class IO_COUNTERS(ctypes.Structure):
    _fields_ = [(name, ctypes.c_uint64) for name in ("ReadOperationCount", "WriteOperationCount",
                "OtherOperationCount", "ReadTransferCount", "WriteTransferCount", "OtherTransferCount")]


class EXTENDED_LIMIT(ctypes.Structure):
    _fields_ = [("BasicLimitInformation", BASIC_LIMIT), ("IoInfo", IO_COUNTERS),
                ("ProcessMemoryLimit", ctypes.c_size_t), ("JobMemoryLimit", ctypes.c_size_t),
                ("PeakProcessMemoryUsed", ctypes.c_size_t), ("PeakJobMemoryUsed", ctypes.c_size_t)]


class Console:
    def __init__(self, command: list[str], env: dict[str, str], cwd: str):
        if os.name != "nt":
            raise RuntimeError("ConPTY requires native Windows")
        k = self.k = ctypes.WinDLL("kernel32", use_last_error=True)
        signatures = {
            "CreatePipe": ([ctypes.POINTER(W.HANDLE), ctypes.POINTER(W.HANDLE), ctypes.c_void_p, W.DWORD], W.BOOL),
            "CreatePseudoConsole": ([COORD, W.HANDLE, W.HANDLE, W.DWORD, ctypes.POINTER(W.HANDLE)], ctypes.c_long),
            "InitializeProcThreadAttributeList": ([ctypes.c_void_p, W.DWORD, W.DWORD, ctypes.POINTER(ctypes.c_size_t)], W.BOOL),
            "UpdateProcThreadAttribute": ([ctypes.c_void_p, W.DWORD, ctypes.c_size_t, ctypes.c_void_p, ctypes.c_size_t, ctypes.c_void_p, ctypes.c_void_p], W.BOOL),
            "CreateProcessW": ([W.LPCWSTR, W.LPWSTR, ctypes.c_void_p, ctypes.c_void_p, W.BOOL, W.DWORD, ctypes.c_void_p, W.LPCWSTR, ctypes.POINTER(STARTUPINFOEX), ctypes.POINTER(PROCESS_INFORMATION)], W.BOOL),
            "ReadFile": ([W.HANDLE, ctypes.c_void_p, W.DWORD, ctypes.POINTER(W.DWORD), ctypes.c_void_p], W.BOOL),
            "WriteFile": ([W.HANDLE, ctypes.c_void_p, W.DWORD, ctypes.POINTER(W.DWORD), ctypes.c_void_p], W.BOOL),
            "WaitForSingleObject": ([W.HANDLE, W.DWORD], W.DWORD),
            "GetExitCodeProcess": ([W.HANDLE, ctypes.POINTER(W.DWORD)], W.BOOL),
            "CloseHandle": ([W.HANDLE], W.BOOL),
            "ClosePseudoConsole": ([W.HANDLE], None),
            "DeleteProcThreadAttributeList": ([ctypes.c_void_p], None),
            "CreateJobObjectW": ([ctypes.c_void_p, W.LPCWSTR], W.HANDLE),
            "AssignProcessToJobObject": ([W.HANDLE, W.HANDLE], W.BOOL),
            "SetInformationJobObject": ([W.HANDLE, ctypes.c_int, ctypes.c_void_p, W.DWORD], W.BOOL),
            "TerminateJobObject": ([W.HANDLE, W.UINT], W.BOOL),
            "QueryInformationJobObject": ([W.HANDLE, ctypes.c_int, ctypes.c_void_p, W.DWORD, ctypes.c_void_p], W.BOOL),
            "ResumeThread": ([W.HANDLE], W.DWORD),
        }
        for name, (args, restype) in signatures.items():
            getattr(k, name).argtypes, getattr(k, name).restype = args, restype
        self.input_read, self.input_write, self.output_read, self.output_write = (W.HANDLE() for _ in range(4))
        self.pc = W.HANDLE()
        self.info = PROCESS_INFORMATION()
        self.job = None
        self.parts: list[bytes] = []
        self.reader = None
        self.attributes = None
        self.closed = False
        try:
            self.check(k.CreatePipe(ctypes.byref(self.input_read), ctypes.byref(self.input_write), None, 0))
            self.check(k.CreatePipe(ctypes.byref(self.output_read), ctypes.byref(self.output_write), None, 0))
            hr = k.CreatePseudoConsole(COORD(140, 50), self.input_read, self.output_write, 0, ctypes.byref(self.pc))
            if hr != 0:
                raise OSError(f"CreatePseudoConsole HRESULT {hr:#x}")
            size = ctypes.c_size_t()
            k.InitializeProcThreadAttributeList(None, 1, 0, ctypes.byref(size))
            self.attributes = ctypes.create_string_buffer(size.value)
            self.check(k.InitializeProcThreadAttributeList(self.attributes, 1, 0, ctypes.byref(size)))
            self.check(k.UpdateProcThreadAttribute(self.attributes, 0, 0x00020016, self.pc, ctypes.sizeof(W.HANDLE), None, None))
            startup = STARTUPINFOEX()
            startup.StartupInfo.cb = ctypes.sizeof(startup)
            # A redirected parent can otherwise leak its file/pipe stdio even
            # with bInheritHandles=False. NULL handles plus USESTDHANDLES make
            # ConPTY populate real console handles (microsoft/terminal#4380).
            startup.StartupInfo.dwFlags = 0x00000100  # STARTF_USESTDHANDLES
            startup.StartupInfo.hStdInput = None
            startup.StartupInfo.hStdOutput = None
            startup.StartupInfo.hStdError = None
            startup.lpAttributeList = ctypes.addressof(self.attributes)
            block = ctypes.create_unicode_buffer("\0".join(f"{key}={value}" for key, value in sorted(env.items(), key=lambda row: row[0].upper())) + "\0\0")
            flags = 0x00080000 | 0x00000400 | 0x00000004  # extended, Unicode, suspended
            self.check(k.CreateProcessW(None, ctypes.create_unicode_buffer(subprocess.list2cmdline(command)),
                                        None, None, False, flags, block, cwd, ctypes.byref(startup), ctypes.byref(self.info)))
            self.job = k.CreateJobObjectW(None, None)
            self.check(self.job)
            limits = EXTENDED_LIMIT()
            limits.BasicLimitInformation.LimitFlags = 0x2000  # KILL_ON_JOB_CLOSE
            self.check(k.SetInformationJobObject(self.job, 9, ctypes.byref(limits), ctypes.sizeof(limits)))
            self.check(k.AssignProcessToJobObject(self.job, self.info.hProcess))
            if k.ResumeThread(self.info.hThread) == 0xFFFFFFFF:
                raise ctypes.WinError(ctypes.get_last_error())
            k.CloseHandle(self.input_read); self.input_read = W.HANDLE()
            k.CloseHandle(self.output_write); self.output_write = W.HANDLE()
            self.reader = threading.Thread(target=self._read, name="conpty-output", daemon=True)
            self.reader.start()
        except BaseException:
            self.close()
            raise

    @staticmethod
    def check(ok):
        if not ok:
            raise ctypes.WinError(ctypes.get_last_error())

    def _read(self):
        buffer = ctypes.create_string_buffer(65536)
        count = W.DWORD()
        while self.k.ReadFile(self.output_read, buffer, len(buffer), ctypes.byref(count), None) and count.value:
            self.parts.append(buffer.raw[:count.value])

    @property
    def output(self) -> str:
        text = b"".join(self.parts).decode("utf-8", errors="replace")
        return re.sub(r"\x1b\[[0-?]*[ -/]*[@-~]|\x1b\][^\x07]*(?:\x07|\x1b\\)", "", text)

    def send(self, text: str):
        data = text.encode("utf-8")
        count = W.DWORD()
        self.check(self.k.WriteFile(self.input_write, data, len(data), ctypes.byref(count), None))
        if count.value != len(data):
            raise AssertionError("ConPTY input was truncated")

    def until(self, text: str, timeout=20):
        deadline = time.monotonic() + timeout
        while text not in self.output:
            if time.monotonic() >= deadline or self.k.WaitForSingleObject(self.info.hProcess, 0) == 0:
                raise AssertionError(f"Console did not reach {text!r}: {self.output[-4000:]}")
            time.sleep(.025)
        return self.output

    def finish(self, timeout=30) -> int:
        if self.k.WaitForSingleObject(self.info.hProcess, int(timeout * 1000)) != 0:
            raise TimeoutError("Console process timed out: " + self.output[-4000:])
        code = W.DWORD()
        self.check(self.k.GetExitCodeProcess(self.info.hProcess, ctypes.byref(code)))
        accounting = ACCOUNTING()
        deadline = time.monotonic() + 20
        while True:
            self.check(self.k.QueryInformationJobObject(self.job, 1, ctypes.byref(accounting), ctypes.sizeof(accounting), None))
            if not accounting.ActiveProcesses:
                break
            if time.monotonic() >= deadline:
                raise AssertionError(f"CLI left {accounting.ActiveProcesses} descendant processes alive")
            time.sleep(.05)
        # Closing the console flushes its last output while the reader drains it.
        self.k.ClosePseudoConsole(self.pc); self.pc = W.HANDLE()
        self.reader.join(timeout=5)
        if self.reader.is_alive():
            raise AssertionError("Console output reader did not finish")
        return code.value

    def close(self):
        if self.closed:
            return
        self.closed = True
        if self.job:
            self.k.TerminateJobObject(self.job, 99)
        if self.pc:
            self.k.ClosePseudoConsole(self.pc)
        if self.reader:
            self.reader.join(timeout=5)
        for handle in (self.input_read, self.input_write, self.output_read, self.output_write,
                       self.info.hThread, self.info.hProcess, self.job):
            if handle:
                self.k.CloseHandle(handle)
        if self.attributes:
            self.k.DeleteProcThreadAttributeList(self.attributes)

    def __enter__(self):
        return self

    def __exit__(self, *_):
        self.close()
