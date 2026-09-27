from __future__ import annotations

import os
from pathlib import Path

from .config import private_dir


class InstanceLock:
    def __init__(self, home: Path):
        private_dir(home)
        self.path = home / "agent.lock"
        self.file = None

    def __enter__(self):
        self.file = self.path.open("a+b")
        self.file.seek(0)
        try:
            if os.name == "nt":
                import msvcrt

                msvcrt.locking(self.file.fileno(), msvcrt.LK_NBLCK, 1)
            else:
                import fcntl

                fcntl.flock(self.file.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
            # Windows byte-range locks also prohibit reads through another handle.
            # Acquire first, then initialize; locking past EOF is supported.
            if os.fstat(self.file.fileno()).st_size == 0:
                self.file.write(b"0")
                self.file.flush()
        except OSError:
            self.file.close()
            raise ValueError(
                "Another agent or configuration command is using this data directory"
            ) from None
        return self

    def __exit__(self, *_):
        if self.file:
            self.file.close()
