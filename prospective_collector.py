"""Single-instance, restart-safe polling worker for prospective holdout cohorts.

The collector never registers a cohort or changes its frozen configuration. It
only reads status and submits idempotent tick requests to a loopback API.
"""
from __future__ import annotations

import argparse
import json
import logging
import os
import signal
import tempfile
import time
from contextlib import contextmanager
from datetime import UTC, datetime
from pathlib import Path
from typing import Any
from urllib.parse import urlparse

import requests

LOG = logging.getLogger("chartanalyzer.prospective_collector")


def _utc_now() -> str:
    return datetime.now(UTC).isoformat()


def _load_config(path: str | Path) -> dict[str, Any]:
    config_path = Path(path).expanduser().resolve()
    with config_path.open("r", encoding="utf-8") as stream:
        config = json.load(stream)
    if not isinstance(config, dict):
        raise TypeError("collector config must be a JSON object")
    base_url = str(config.get("api_base_url", "http://127.0.0.1:8765")).rstrip("/")
    parsed = urlparse(base_url)
    if parsed.scheme != "http" or parsed.hostname not in ("127.0.0.1", "127.1.0.1", "localhost", "::1"):
        raise ValueError("collector API must use HTTP loopback only; do not expose the holdout API publicly")
    if parsed.path or parsed.query or parsed.fragment or parsed.username or parsed.password:
        raise ValueError("api_base_url must contain only the loopback origin and optional port")
    payload = config.get("cohort_payload")
    if not isinstance(payload, dict):
        raise TypeError("cohort_payload must be the frozen request object used to register the cohort")
    required = ("cohort_id", "source", "symbol", "tf", "horizon_bars", "paths", "n")
    missing = [key for key in required if key not in payload]
    if missing:
        raise ValueError("cohort_payload missing required keys: " + ", ".join(missing))
    poll_seconds = int(config.get("poll_seconds", 300))
    timeout_seconds = int(config.get("request_timeout_seconds", 45))
    if not 30 <= poll_seconds <= 3600:
        raise ValueError("poll_seconds must be between 30 and 3600")
    if not 5 <= timeout_seconds <= 180:
        raise ValueError("request_timeout_seconds must be between 5 and 180")
    cohort_id = str(payload["cohort_id"])
    if not cohort_id or len(cohort_id) > 64:
        raise ValueError("invalid cohort_id")
    state_file = Path(config.get("state_file", "./prospective_collector_state.json")).expanduser()
    if not state_file.is_absolute():
        state_file = config_path.parent / state_file
    return {
        "api_base_url": base_url,
        "cohort_payload": payload,
        "poll_seconds": poll_seconds,
        "request_timeout_seconds": timeout_seconds,
        "state_file": state_file.resolve(),
        "cohort_id": cohort_id,
    }


@contextmanager
def _single_instance_lock(lock_path: Path):
    """OS advisory lock; automatically released if the process exits or crashes."""
    lock_path.parent.mkdir(parents=True, exist_ok=True)
    handle = lock_path.open("a+", encoding="utf-8")
    try:
        if os.name == "nt":
            import msvcrt
            # On Windows the byte-range lock is mandatory: while another handle holds
            # byte 0, even reading it raises PermissionError. That is the "already
            # locked" signal, so it must be reported like a failed lock attempt.
            try:
                handle.seek(0)
                if handle.read(1) == "":
                    handle.write(" ")
                    handle.flush()
                handle.seek(0)
                msvcrt.locking(handle.fileno(), msvcrt.LK_NBLCK, 1)
            except OSError as exc:
                raise RuntimeError("another prospective collector already holds the lock") from exc
        else:
            import fcntl
            try:
                fcntl.flock(handle.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
            except OSError as exc:
                raise RuntimeError("another prospective collector already holds the lock") from exc
        yield
    finally:
        try:
            if os.name == "nt":
                import msvcrt
                handle.seek(0)
                msvcrt.locking(handle.fileno(), msvcrt.LK_UNLCK, 1)
            else:
                import fcntl
                fcntl.flock(handle.fileno(), fcntl.LOCK_UN)
        except OSError:
            pass
        handle.close()


def _atomic_state_write(path: Path, state: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    fd, temp_name = tempfile.mkstemp(prefix=path.name + ".", suffix=".tmp", dir=str(path.parent))
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as stream:
            json.dump(state, stream, ensure_ascii=False, sort_keys=True, indent=2)
            stream.write("\n")
            stream.flush()
            os.fsync(stream.fileno())
        os.replace(temp_name, path)
        if os.name == "posix":
            os.chmod(path, 0o600)
            # Persist the directory entry as well as the file contents.
            directory_fd = os.open(path.parent, os.O_RDONLY)
            try:
                os.fsync(directory_fd)
            finally:
                os.close(directory_fd)
    finally:
        try:
            os.unlink(temp_name)
        except FileNotFoundError:
            pass


class ProspectiveCollector:
    def __init__(self, config: dict[str, Any], session: requests.Session | None = None):
        self.config = config
        self.session = session or requests.Session()
        self.session.headers.update({"User-Agent": "ChartAnalyzer-ProspectiveCollector/1.0"})
        self.stop_requested = False
        self.failures = 0

    def _url(self, suffix: str) -> str:
        return self.config["api_base_url"] + suffix

    def run_once(self) -> dict[str, Any]:
        cohort_id = self.config["cohort_id"]
        timeout = self.config["request_timeout_seconds"]
        status_response = self.session.get(
            self._url("/api/prospective-holdout/status"),
            params={"cohort_id": cohort_id},
            timeout=timeout,
        )
        status_response.raise_for_status()
        status_body = status_response.json()
        cohort = status_body.get("cohort") or {}
        if cohort.get("cohort_id") != cohort_id:
            raise RuntimeError("API returned a different or unregistered cohort")
        integrity = status_body.get("integrity") or {}
        if integrity.get("status") != "PASS":
            raise RuntimeError("prospective ledger integrity check did not pass")
        if cohort.get("report_available"):
            return {"terminal": True, "cohort": cohort, "issued_forecast": None, "resolved_this_tick": 0}

        tick_response = self.session.post(
            self._url("/api/prospective-holdout/tick"),
            json=self.config["cohort_payload"],
            timeout=timeout,
        )
        tick_response.raise_for_status()
        result = tick_response.json()
        tick_cohort = result.get("cohort") or {}
        if tick_cohort.get("cohort_id") != cohort_id:
            raise RuntimeError("tick response cohort identity mismatch")
        if result.get("report_available"):
            raise RuntimeError("collector must not reveal or consume the final holdout report")
        return {
            "terminal": False,
            "cohort": tick_cohort,
            "issued_forecast": result.get("issued_forecast"),
            "resolved_this_tick": int(result.get("resolved_this_tick", 0)),
            "unresolved_due_count": len(result.get("unresolved_due") or []),
            "next_issuance_due": result.get("next_issuance_due"),
        }

    def _write_state(self, *, success: bool, result: dict[str, Any] | None = None, error: str | None = None):
        old = {}
        state_path = self.config["state_file"]
        if state_path.exists():
            try:
                old = json.loads(state_path.read_text(encoding="utf-8"))
            except (OSError, UnicodeDecodeError, json.JSONDecodeError) as exc:
                # Never overwrite evidence of a damaged state file with a fresh heartbeat.
                raise RuntimeError("collector state file is unreadable; operator recovery is required") from exc
            if not isinstance(old, dict):
                raise RuntimeError("collector state file must contain a JSON object")
            if old.get("cohort_id") not in (None, self.config["cohort_id"]):
                raise RuntimeError("collector state belongs to a different cohort; refusing to overwrite it")
        state = {
            "cohort_id": self.config["cohort_id"],
            "updated_at": _utc_now(),
            "last_success_at": _utc_now() if success else old.get("last_success_at"),
            "consecutive_failures": 0 if success else self.failures,
            "last_error": None if success else error,
            "last_tick": result,
        }
        _atomic_state_write(self.config["state_file"], state)

    def run_forever(self):
        poll = self.config["poll_seconds"]
        while not self.stop_requested:
            started = time.monotonic()
            try:
                result = self.run_once()
                self.failures = 0
                self._write_state(success=True, result=result)
                cohort = result["cohort"]
                LOG.info(
                    "cohort=%s status=%s forecasts=%s resolved=%s issued=%s unresolved_due=%s",
                    self.config["cohort_id"], cohort.get("status"),
                    cohort.get("forecast_count"), cohort.get("resolved_count"),
                    bool(result.get("issued_forecast")), result.get("unresolved_due_count", 0),
                )
                if result.get("terminal"):
                    LOG.info("cohort already evaluated; collector is stopping")
                    return
            except Exception as exc:
                self.failures += 1
                message = f"{type(exc).__name__}: {exc}"
                self._write_state(success=False, error=message)
                LOG.exception("collector tick failed; ledger remains the source of truth")
            elapsed = time.monotonic() - started
            backoff = min(poll, 30 * (2 ** min(max(self.failures - 1, 0), 5))) if self.failures else poll
            wait_seconds = max(1.0, backoff - elapsed)
            deadline = time.monotonic() + wait_seconds
            while not self.stop_requested and time.monotonic() < deadline:
                time.sleep(min(1.0, deadline - time.monotonic()))

    def request_stop(self, *_):
        self.stop_requested = True


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Run the frozen ChartAnalyzer prospective holdout collector")
    parser.add_argument("--config", default=os.getenv("CHARTANALYZER_COLLECTOR_CONFIG", "prospective_collector.json"))
    parser.add_argument("--once", action="store_true", help="perform one status/tick cycle and exit")
    args = parser.parse_args(argv)
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
    try:
        config = _load_config(args.config)
        lock_path = config["state_file"].with_suffix(config["state_file"].suffix + ".lock")
        with _single_instance_lock(lock_path):
            collector = ProspectiveCollector(config)
            signal.signal(signal.SIGTERM, collector.request_stop)
            if hasattr(signal, "SIGBREAK"):
                signal.signal(signal.SIGBREAK, collector.request_stop)
            if args.once:
                result = collector.run_once()
                collector._write_state(success=True, result=result)
                LOG.info("one-shot tick complete: %s", json.dumps(result, ensure_ascii=False))
                return 0
            collector.run_forever()
        return 0
    except KeyboardInterrupt:
        LOG.info("collector stopped by operator")
        return 0
    except Exception as exc:  # noqa: BLE001
        LOG.error("%s: %s", type(exc).__name__, exc)
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
