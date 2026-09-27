from __future__ import annotations

import base64
import ctypes
import json
import os
from ctypes import wintypes
from pathlib import Path

from .config import atomic_write


def _dpapi(data: bytes, protect: bool) -> bytes:
    """Windows current-user DPAPI. The service must run as the enrolling user."""

    class Blob(ctypes.Structure):
        _fields_ = [("size", wintypes.DWORD), ("data", ctypes.POINTER(ctypes.c_char))]

    buffer = ctypes.create_string_buffer(data)
    source = Blob(len(data), ctypes.cast(buffer, ctypes.POINTER(ctypes.c_char)))
    target = Blob()
    crypt = ctypes.WinDLL("crypt32", use_last_error=True)
    kernel = ctypes.WinDLL("kernel32", use_last_error=True)
    kernel.LocalFree.argtypes = [ctypes.c_void_p]
    kernel.LocalFree.restype = ctypes.c_void_p
    fn = crypt.CryptProtectData if protect else crypt.CryptUnprotectData
    fn.argtypes = [
        ctypes.POINTER(Blob),
        ctypes.c_void_p,
        ctypes.c_void_p,
        ctypes.c_void_p,
        ctypes.c_void_p,
        wintypes.DWORD,
        ctypes.POINTER(Blob),
    ]
    fn.restype = wintypes.BOOL
    if not fn(ctypes.byref(source), None, None, None, None, 1, ctypes.byref(target)):
        raise ctypes.WinError(ctypes.get_last_error())
    try:
        return ctypes.string_at(target.data, target.size)
    finally:
        kernel.LocalFree(ctypes.cast(target.data, ctypes.c_void_p))


class CredentialStore:
    def __init__(self, home: Path):
        self.path = home / "credentials.json"

    def save(self, credentials: dict) -> None:
        raw = json.dumps(credentials).encode()
        if os.name == "nt":
            envelope = {"protection": "dpapi", "data": base64.b64encode(_dpapi(raw, True)).decode()}
        else:
            envelope = {"protection": "file-mode-0600", "data": credentials}
        atomic_write(self.path, json.dumps(envelope))

    def load(self) -> dict:
        if not self.path.exists():
            raise ValueError("PC is not enrolled; run 'citrus-agent enroll'")
        envelope = json.loads(self.path.read_text(encoding="utf-8"))
        if envelope["protection"] == "dpapi":
            if os.name != "nt":
                raise ValueError("Windows credentials cannot be moved to another OS")
            return json.loads(_dpapi(base64.b64decode(envelope["data"]), False))
        if os.name == "nt":
            raise ValueError("Re-enroll on Windows to protect credentials with DPAPI")
        if self.path.stat().st_mode & 0o077:
            raise ValueError("credentials.json permissions must be 0600")
        return envelope["data"]
