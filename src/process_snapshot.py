# -*- coding: utf-8 -*-
"""Shared ctypes Toolhelp32-snapshot primitives (kernel32 only, no psutil/
pywin32 dependency) -- process *names* only, never a window title/content/
screenshot. Split out of game_detector.py so game_egg_display.py (pid ->
process name, for foreground-window occlusion checks) can reuse the same
struct/bindings without a circular import between the two.
"""
import ctypes
from ctypes import wintypes

TH32CS_SNAPPROCESS = 0x00000002
_PTR_BITS = ctypes.sizeof(ctypes.c_void_p) * 8
INVALID_HANDLE_VALUE = (1 << _PTR_BITS) - 1


class ProcessEntry32W(ctypes.Structure):
    _fields_ = [
        ("dwSize", wintypes.DWORD),
        ("cntUsage", wintypes.DWORD),
        ("th32ProcessID", wintypes.DWORD),
        ("th32DefaultHeapID", ctypes.c_void_p),
        ("th32ModuleID", wintypes.DWORD),
        ("cntThreads", wintypes.DWORD),
        ("th32ParentProcessID", wintypes.DWORD),
        ("pcPriClassBase", wintypes.LONG),
        ("dwFlags", wintypes.DWORD),
        ("szExeFile", ctypes.c_wchar * 260),
    ]


kernel32 = ctypes.windll.kernel32
kernel32.CreateToolhelp32Snapshot.restype = wintypes.HANDLE
kernel32.CreateToolhelp32Snapshot.argtypes = [wintypes.DWORD, wintypes.DWORD]
kernel32.Process32FirstW.restype = wintypes.BOOL
kernel32.Process32FirstW.argtypes = [wintypes.HANDLE, ctypes.POINTER(ProcessEntry32W)]
kernel32.Process32NextW.restype = wintypes.BOOL
kernel32.Process32NextW.argtypes = [wintypes.HANDLE, ctypes.POINTER(ProcessEntry32W)]
kernel32.CloseHandle.argtypes = [wintypes.HANDLE]


def running_process_names():
    """Lowercase exe-name snapshot via CreateToolhelp32Snapshot. Any
    failure just yields an empty set (this is a background poll, not worth
    crashing over)."""
    names = set()
    snapshot = kernel32.CreateToolhelp32Snapshot(TH32CS_SNAPPROCESS, 0)
    if not snapshot or snapshot == INVALID_HANDLE_VALUE:
        return names
    try:
        entry = ProcessEntry32W()
        entry.dwSize = ctypes.sizeof(ProcessEntry32W)
        if kernel32.Process32FirstW(snapshot, ctypes.byref(entry)):
            while True:
                names.add(entry.szExeFile.lower())
                if not kernel32.Process32NextW(snapshot, ctypes.byref(entry)):
                    break
    except OSError:
        pass
    finally:
        kernel32.CloseHandle(snapshot)
    return names


def process_name_for_pid(pid):
    """Lowercase exe name of a single pid, or None if it can't be found --
    used to identify which process owns the current foreground window."""
    snapshot = kernel32.CreateToolhelp32Snapshot(TH32CS_SNAPPROCESS, 0)
    if not snapshot or snapshot == INVALID_HANDLE_VALUE:
        return None
    try:
        entry = ProcessEntry32W()
        entry.dwSize = ctypes.sizeof(ProcessEntry32W)
        if kernel32.Process32FirstW(snapshot, ctypes.byref(entry)):
            while True:
                if entry.th32ProcessID == pid:
                    return entry.szExeFile.lower()
                if not kernel32.Process32NextW(snapshot, ctypes.byref(entry)):
                    break
    except OSError:
        pass
    finally:
        kernel32.CloseHandle(snapshot)
    return None
