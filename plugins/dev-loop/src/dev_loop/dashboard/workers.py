"""The dashboard's background threads and Restart."""

from __future__ import annotations

import threading
from typing import TYPE_CHECKING

from . import scan

if TYPE_CHECKING:
    from collections.abc import Callable

    from .scan import Scanner


class Workers:
    """The scan loop and any poller added to `targets` (part d), kept alive."""

    def __init__(self, scanner: Scanner, interval: float = 2.0) -> None:
        self.scanner, self.interval = scanner, interval
        self.wake = threading.Event()  # wakes any poller in `targets` now
        self.stopped = threading.Event()  # ends the loops (tests; the server runs until killed)
        self.targets: dict[str, Callable[[], None]] = {"scanner": self.scan_forever}
        self.threads: dict[str, threading.Thread] = {}
        self.starting = threading.Lock()  # two Restarts at once start one thread each (D-6)

    def scan_forever(self) -> None:
        while not self.stopped.is_set():  # refresh() never raises: a failed pass is recorded
            self.scanner.refresh()
            self.stopped.wait(self.interval)

    def start(self) -> list[str]:
        """Start each thread that is not running; return the names started."""
        with self.starting:
            alive = {name for name, thread in self.threads.items() if thread.is_alive()}
            dead = [name for name in self.targets if name not in alive]
            for name in dead:
                thread = threading.Thread(target=self.targets[name], name=name, daemon=True)
                self.threads[name] = thread
                thread.start()
            return dead

    def restart(self) -> list[str]:
        """Re-read every transcript, wake the pollers and revive any dead thread."""
        scan.RESTART.set()
        self.wake.set()
        return self.start()
