"""Runs scraper.py as one subprocess at a time (keeps the single-threaded
politeness guarantee even if several buttons are clicked)."""

from __future__ import annotations

import collections
import os
import subprocess
import sys
import threading
from dataclasses import dataclass, field
from pathlib import Path

from .. import config
from ..state import QUEUE_MODES, now

EXIT_MEANINGS = {0: "finished", 3: "captcha", 4: "session_error", 130: "stopped"}
MODES = {
    "recon": ["--recon"],
    "test": ["--test-mode"],
    "crawl": ["--resume"],
}


@dataclass
class Job:
    mode: str
    args: list[str]
    started_at: str
    proc: subprocess.Popen | None = None
    ended_at: str | None = None
    exit_code: int | None = None
    lines: collections.deque = field(default_factory=lambda: collections.deque(maxlen=1500))
    stop_requested: bool = False

    @property
    def running(self) -> bool:
        return self.proc is not None and self.proc.poll() is None

    @property
    def state(self) -> str:
        if self.running:
            if (config.CONTROL_DIR / "STOP").exists():
                return "stopping"
            if (config.CONTROL_DIR / "PAUSE").exists():
                return "paused"
            return "running"
        if self.exit_code is None:
            return "starting"
        if self.exit_code == 0 and self.stop_requested:
            return "stopped"
        return EXIT_MEANINGS.get(self.exit_code, "failed")


class JobManager:
    def __init__(self, project_root: Path = config.PROJECT_ROOT):
        self.root = project_root
        self.job: Job | None = None
        self._lock = threading.Lock()

    def _clear_controls(self):
        config.CONTROL_DIR.mkdir(exist_ok=True)
        for name in ("PAUSE", "STOP"):
            (config.CONTROL_DIR / name).unlink(missing_ok=True)

    def start(self, mode: str, cookie_file: Path, user_agent: str | None = None,
              retry_failed: bool = False, queue: str = "audio-first", limit: int = 0,
              workers: int = 1) -> Job:
        if mode not in MODES:
            raise ValueError(f"unknown mode {mode}")
        if queue not in QUEUE_MODES:
            raise ValueError(f"queue must be one of {QUEUE_MODES}")
        if limit < 0:
            raise ValueError("limit must be >= 0 (0 = unlimited)")
        if not 1 <= workers <= config.PARALLEL_MAX_WORKERS:
            raise ValueError(f"workers must be between 1 and {config.PARALLEL_MAX_WORKERS}")
        with self._lock:
            if self.job and self.job.running:
                raise RuntimeError(f"a '{self.job.mode}' job is already running")
            if not cookie_file.exists():
                raise RuntimeError("no cookies saved yet - paste your session first")
            self._clear_controls()
            args = self.command(mode, cookie_file, user_agent, retry_failed, queue, limit, workers)
            job = Job(mode=mode, args=args, started_at=now())
            env = {**os.environ, "PYTHONIOENCODING": "utf-8", "PYTHONUNBUFFERED": "1"}
            flags = subprocess.CREATE_NEW_PROCESS_GROUP if os.name == "nt" else 0
            job.proc = subprocess.Popen(args, cwd=self.root, env=env, stdout=subprocess.PIPE,
                                        stderr=subprocess.STDOUT, stdin=subprocess.DEVNULL,
                                        creationflags=flags)
            self.job = job
            threading.Thread(target=self._pump, args=(job,), daemon=True).start()
            return job

    def command(self, mode: str, cookie_file: Path, user_agent: str | None = None,
                retry_failed: bool = False, queue: str = "audio-first", limit: int = 0,
                workers: int = 1) -> list[str]:
        """The scraper.py command line for a dashboard job. Queue policy and
        batch limit apply to the full crawl only."""
        args = [sys.executable, "-u", str(self.root / "scraper.py"), *MODES[mode],
                "--cookie-file", str(cookie_file)]
        if user_agent:
            args += ["--user-agent", user_agent]
        if retry_failed and mode != "recon":
            args.append("--retry-failed")
        if mode == "crawl":
            args += ["--queue", queue]
            if limit:
                args += ["--limit", str(limit)]
            if workers > 1:
                args += ["--workers", str(workers)]
        return args

    @staticmethod
    def _pump(job: Job):
        assert job.proc and job.proc.stdout
        for raw in job.proc.stdout:
            job.lines.append(raw.decode("utf-8", errors="replace").rstrip("\r\n"))
        job.exit_code = job.proc.wait()
        job.ended_at = now()
        for name in ("PAUSE", "STOP"):
            (config.CONTROL_DIR / name).unlink(missing_ok=True)

    def _signal(self, name: str, on: bool = True):
        if not (self.job and self.job.running):
            raise RuntimeError("no job is running")
        config.CONTROL_DIR.mkdir(exist_ok=True)
        f = config.CONTROL_DIR / name
        if on:
            f.touch()
        else:
            f.unlink(missing_ok=True)

    def pause(self):
        self._signal("PAUSE")

    def resume(self):
        self._signal("PAUSE", on=False)

    def stop(self):
        self.job.stop_requested = True if self.job else False
        self._signal("STOP")

    def kill(self):
        if self.job and self.job.running:
            self.job.stop_requested = True
            self.job.proc.terminate()

    def status(self, tail: int = 200) -> dict | None:
        j = self.job
        if j is None:
            return None
        return {
            "mode": j.mode, "state": j.state, "running": j.running, "exit_code": j.exit_code,
            "started_at": j.started_at, "ended_at": j.ended_at,
            "log": list(j.lines)[-tail:],
        }
