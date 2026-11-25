"""One writer per change folder at a time, on Windows and POSIX alike.

A lock file made with O_CREAT | O_EXCL, since fcntl is not on a Windows host.
It holds its writer's token: the release removes only its own lock, and a dead writer's lock
(its pid gone, or older than LOCK_STALE) is taken over only under a second lock, `.record.lock.breaker`, so two
waiters never both break it. Files are replaced durably: fsync, then os.replace,
retried while Windows reports the target open elsewhere.
"""

from __future__ import annotations

import os
import time
import uuid
from collections.abc import Callable, Iterator
from contextlib import contextmanager, suppress
from pathlib import Path

LOCK_WAIT, LOCK_STALE = 10.0, 60.0  # seconds; a lock older than LOCK_STALE lost its writer
RETRIES = 20  # x 0.05 s: Windows refuses a replace or unlink while another process has it open


def retried(action: Callable[[], None]) -> None:
    for _ in range(RETRIES - 1):
        try:
            action()
        except PermissionError:
            time.sleep(0.05)
        else:
            return
    action()


def replace_text(path: Path, text: str) -> None:
    """Write `path` whole: a reader sees the old file or the new, never half, even after a crash."""
    draft = path.with_name(f".{path.name}.{uuid.uuid4().hex}.tmp")
    with draft.open("w", encoding="utf-8", newline="\n") as file:
        file.write(text)
        file.flush()
        os.fsync(file.fileno())
    retried(lambda: os.replace(draft, path))


def take(lock: Path, token: str) -> bool:
    """Create `lock` holding `token`; False when another writer holds it."""
    try:
        handle = os.open(lock, os.O_CREAT | os.O_EXCL | os.O_WRONLY)
    except FileExistsError:
        return False
    os.write(handle, token.encode())
    os.close(handle)
    return True


def release(lock: Path, token: str) -> None:
    """Remove `lock` only while it is still `token`'s: never another writer's lock."""
    with suppress(FileNotFoundError):
        if lock.read_text(encoding="utf-8") == token:
            retried(lock.unlink)


def dead(lock: Path) -> bool:
    """True when the pid in `lock`'s token is gone (POSIX only: a Windows pid is never probed)."""
    try:
        os.kill(int(lock.read_text(encoding="utf-8").split()[0]), 0)  # ponytail: same host only
    except ProcessLookupError:
        return os.name == "posix"
    except (OSError, ValueError, IndexError):
        pass
    return False


def break_stale(lock: Path, token: str) -> None:
    """Remove `lock` if its writer died; only the breaker's holder decides, so it is decided once."""
    breaker = lock.with_name(lock.name + ".breaker")
    with suppress(FileNotFoundError):  # a dead breaker's holder: its breaker goes the same way
        if time.time() - breaker.stat().st_mtime > LOCK_STALE:
            breaker.unlink()
    if not take(breaker, token):
        return
    try:  # no other breaker runs, and no writer can create the lock while the stale one exists
        with suppress(FileNotFoundError):
            if dead(lock) or time.time() - lock.stat().st_mtime > LOCK_STALE:
                retried(lock.unlink)
    finally:
        release(breaker, token)


@contextmanager
def locked(folder: Path, wait: float = LOCK_WAIT) -> Iterator[None]:
    """Hold the change's lock file while the block runs; wait up to `wait` for another writer."""
    lock, token = folder / ".record.lock", f"{os.getpid()} {uuid.uuid4().hex}"
    deadline = time.monotonic() + wait
    while not take(lock, token):
        break_stale(lock, token)
        if time.monotonic() > deadline:
            raise TimeoutError(f"{lock} held over {wait:.0f} s")
        time.sleep(0.05)
    try:
        yield
    finally:
        release(lock, token)
