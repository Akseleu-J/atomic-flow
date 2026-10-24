"""Owned Windows process lifetime, not filesystem/network isolation.

A trusted isolated bootstrap joins the job before starting project code. Only the
host retains a job handle after assignment, so host death closes the last handle.
"""
from __future__ import annotations

import ctypes
import os
import subprocess
import sys
import time
from ctypes import wintypes


class _BasicLimits(ctypes.Structure):
    _fields_ = [("process_time", ctypes.c_int64), ("job_time", ctypes.c_int64),
                ("flags", wintypes.DWORD), ("min_working_set", ctypes.c_size_t),
                ("max_working_set", ctypes.c_size_t), ("active_limit", wintypes.DWORD),
                ("affinity", ctypes.c_size_t), ("priority", wintypes.DWORD),
                ("scheduling", wintypes.DWORD)]


class _ExtendedLimits(ctypes.Structure):
    _fields_ = [("basic", _BasicLimits), ("io", ctypes.c_uint64 * 6),
                ("process_memory", ctypes.c_size_t), ("job_memory", ctypes.c_size_t),
                ("peak_process", ctypes.c_size_t), ("peak_job", ctypes.c_size_t)]


class _Accounting(ctypes.Structure):
    _fields_ = [("times", ctypes.c_int64 * 4), ("page_faults", wintypes.DWORD),
                ("total", wintypes.DWORD), ("active", wintypes.DWORD), ("terminated", wintypes.DWORD)]


_BOOTSTRAP = """
import ctypes, subprocess, sys
from ctypes import wintypes
k = ctypes.WinDLL('kernel32', use_last_error=True)
k.GetCurrentProcess.restype = wintypes.HANDLE
k.AssignProcessToJobObject.argtypes = [wintypes.HANDLE, wintypes.HANDLE]
k.CloseHandle.argtypes = [wintypes.HANDLE]
h = int(sys.argv[1])
if not k.AssignProcessToJobObject(h, k.GetCurrentProcess()):
    raise SystemExit(125)
k.CloseHandle(h)
raise SystemExit(subprocess.call(sys.argv[2:], close_fds=True))
"""


class WindowsJob:
    def __init__(self):
        if os.name != "nt":
            raise OSError("Windows Job Objects unavailable")
        self.api = ctypes.WinDLL("kernel32", use_last_error=True)
        self.api.CreateJobObjectW.argtypes = [ctypes.c_void_p, wintypes.LPCWSTR]
        self.api.CreateJobObjectW.restype = wintypes.HANDLE
        self.api.SetInformationJobObject.argtypes = [wintypes.HANDLE, ctypes.c_int, ctypes.c_void_p, wintypes.DWORD]
        self.api.QueryInformationJobObject.argtypes = [wintypes.HANDLE, ctypes.c_int, ctypes.c_void_p, wintypes.DWORD, ctypes.c_void_p]
        self.api.TerminateJobObject.argtypes = [wintypes.HANDLE, wintypes.UINT]
        self.api.CloseHandle.argtypes = [wintypes.HANDLE]
        self.handle = self.api.CreateJobObjectW(None, None)
        if not self.handle:
            raise ctypes.WinError(ctypes.get_last_error())
        limits = _ExtendedLimits()
        limits.basic.flags = 0x2000  # JOB_OBJECT_LIMIT_KILL_ON_JOB_CLOSE; no breakaway
        if not self.api.SetInformationJobObject(self.handle, 9, ctypes.byref(limits), ctypes.sizeof(limits)):
            self.close()
            raise OSError("Cannot configure owned Windows job")

    def spawn(self, command, **kwargs):
        startup = subprocess.STARTUPINFO()
        startup.lpAttributeList = {"handle_list": [self.handle]}
        os.set_handle_inheritable(self.handle, True)
        try:
            return subprocess.Popen([sys.executable, "-I", "-c", _BOOTSTRAP, str(self.handle), *command],
                                    startupinfo=startup, close_fds=True, **kwargs)
        finally:
            os.set_handle_inheritable(self.handle, False)

    def terminate(self):
        if not self.api.TerminateJobObject(self.handle, 1):
            raise OSError("Owned Windows job termination failed")
        # Under heavy load a terminated process can linger in its exit path well
        # past a second; the deadline bounds confirmation, and expiry still
        # surfaces honest uncertainty instead of claiming a clean cleanup.
        deadline = time.monotonic() + 30
        while True:
            info = _Accounting()
            if not self.api.QueryInformationJobObject(self.handle, 1, ctypes.byref(info), ctypes.sizeof(info), None):
                raise OSError("Owned Windows job cleanup could not be observed")
            if info.active == 0:
                return
            if time.monotonic() >= deadline:
                raise OSError("Owned Windows job processes remain active")
            time.sleep(0.01)

    def close(self):
        if self.handle:
            self.api.CloseHandle(self.handle)
            self.handle = None
