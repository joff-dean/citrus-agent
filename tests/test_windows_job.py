import ctypes
import os
import subprocess
import sys
from ctypes import wintypes

import pytest


@pytest.mark.skipif(os.name != "nt", reason="Windows Job Object requires Windows")
def test_parent_exit_kills_child_process():
    # Assign a disposable Python process, never the pytest runner itself.
    script = """
import os, subprocess, sys
from citrus_agent.windows_job import protect_process_tree
protect_process_tree()
child = subprocess.Popen([sys.executable, '-c', 'import time; time.sleep(120)'],
                         stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
print(child.pid, flush=True)
os._exit(0)
"""
    result = subprocess.run(
        [sys.executable, "-c", script], capture_output=True, text=True, timeout=15, check=True
    )
    pid = int(result.stdout.strip())
    kernel = ctypes.WinDLL("kernel32", use_last_error=True)
    kernel.OpenProcess.argtypes = [wintypes.DWORD, wintypes.BOOL, wintypes.DWORD]
    kernel.OpenProcess.restype = wintypes.HANDLE
    kernel.WaitForSingleObject.argtypes = [wintypes.HANDLE, wintypes.DWORD]
    kernel.WaitForSingleObject.restype = wintypes.DWORD
    kernel.CloseHandle.argtypes = [wintypes.HANDLE]
    handle = kernel.OpenProcess(0x100000, False, pid)  # SYNCHRONIZE
    if handle:
        try:
            assert kernel.WaitForSingleObject(handle, 5000) == 0  # WAIT_OBJECT_0
        finally:
            kernel.CloseHandle(handle)
