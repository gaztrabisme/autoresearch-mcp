"""Admission and observable state for one background loop in an MCP process."""

import threading
from contextlib import contextmanager

from . import loop


def stop(session):
    path = session.session_dir / "stop"
    path.touch()
    return {"stop_flag": str(path)}


class RunManager:
    def __init__(self, *, run_function=loop.run):
        self.run_function = run_function
        self._lock = threading.Lock()
        self._thread = None
        self._session = None
        self._busy = False
        self._error = None
        self._result = None

    def _check_idle(self):
        if self._busy or (self._thread is not None and self._thread.is_alive()):
            raise RuntimeError("an autoresearch operation is already running")

    @contextmanager
    def exclusive(self):
        with self._lock:
            self._check_idle()
            self._busy = True
        try:
            yield
        finally:
            with self._lock:
                self._busy = False

    def start(self, session, *, max_iter=None, time_cap_s=None, plateau_limit=None):
        with self._lock:
            self._check_idle()
            loop.validate_limits(session, max_iter, time_cap_s, plateau_limit)
            self._session = session
            self._error = self._result = None

            def worker():
                try:
                    result = self.run_function(
                        session,
                        max_iter=max_iter,
                        time_cap_s=time_cap_s,
                        plateau_limit=plateau_limit,
                    )
                    with self._lock:
                        self._result = result
                except Exception as exc:
                    with self._lock:
                        self._error = str(exc)

            self._thread = threading.Thread(
                target=worker, name=f"autoresearch-{session.name}", daemon=True
            )
            self._thread.start()
        return {"running": True, "session": session.name}

    def join(self, timeout=None):
        with self._lock:
            thread = self._thread
        if thread is not None:
            thread.join(timeout)

    def shutdown(self):
        with self._lock:
            session = self._session
            active = self._thread is not None and self._thread.is_alive()
        if active:
            stop(session)
            self.join()  # A normal stdio shutdown waits for the current iteration's cleanup.

    def status(self, session):
        with self._lock:
            active = self._thread is not None and self._thread.is_alive()
            same = self._session is not None and self._session.session_dir == session.session_dir
            info = {
                "run_active": active,
                "active_session": self._session.name if active else None,
                "error": self._error if same else None,
                "result": self._result if same else None,
            }
        return {
            "state": (session.session_dir / "state.md").read_text(),
            "last_10": session.rows()[-10:],
            "stop_requested": (session.session_dir / "stop").exists(),
            **info,
        }
