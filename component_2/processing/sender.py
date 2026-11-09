"""Durable HTTP sender from Component 2 to the Component 3 receiver.

Every state is first appended to a local JSONL outbox. The background thread
retries delivery until Component 3 acknowledges the request. An idempotency
key lets Component 3 safely ignore delivery retries.
"""
from __future__ import annotations

import hashlib
import json
import threading
import time
from pathlib import Path
from urllib.error import HTTPError, URLError
from urllib.request import Request, urlopen

STATE_FIELDS = {
    "vehicle_id",
    "window_start_timestamp",
    "window_duration_s",
    "velocity_mps",
    "direction_degrees",
    "task_capable_bit_integer",
}


class DurableComponent3Sender(threading.Thread):
    def __init__(
        self,
        receiver_url: str,
        spool_path: Path,
        logger,
        timeout_s: float = 5.0,
        retry_initial_s: float = 1.0,
        retry_max_s: float = 30.0,
    ):
        super().__init__(name="component3-http-sender", daemon=True)
        self.receiver_url = receiver_url
        self.spool_path = Path(spool_path)
        self.spool_path.parent.mkdir(parents=True, exist_ok=True)
        self.logger = logger
        self.timeout_s = max(0.1, float(timeout_s))
        self.retry_initial_s = max(0.1, float(retry_initial_s))
        self.retry_max_s = max(self.retry_initial_s, float(retry_max_s))
        self.stop_event = threading.Event()
        self.new_record_event = threading.Event()
        self.file_lock = threading.Lock()

    def submit(self, state: dict) -> None:
        if set(state) != STATE_FIELDS:
            raise ValueError(
                "Component 3 payload must contain exactly the six Component 2 state fields"
            )
        line = json.dumps(state, separators=(",", ":"), sort_keys=True)
        # Durable append: the record is on disk before the sender attempts HTTP.
        with self.file_lock:
            with self.spool_path.open("a", encoding="utf-8") as handle:
                handle.write(line + chr(10))
                handle.flush()
        self.new_record_event.set()

    def _first_pending_line(self) -> str | None:
        with self.file_lock:
            if not self.spool_path.exists():
                return None
            with self.spool_path.open("r", encoding="utf-8") as handle:
                return handle.readline().rstrip("\r\n") or None

    def _acknowledge_first(self, acknowledged_line: str) -> None:
        with self.file_lock:
            if not self.spool_path.exists():
                return
            lines = self.spool_path.read_text(encoding="utf-8").splitlines()
            if not lines or lines[0] != acknowledged_line:
                self.logger.error("Component 3 outbox changed unexpectedly; retaining spool")
                return
            remaining = lines[1:]
            temporary = self.spool_path.with_suffix(self.spool_path.suffix + ".tmp")
            with temporary.open("w", encoding="utf-8") as handle:
                if remaining:
                    handle.write(chr(10).join(remaining) + chr(10))
                handle.flush()
            temporary.replace(self.spool_path)

    def _send(self, line: str) -> None:
        payload = json.loads(line)
        key = hashlib.sha256(line.encode("utf-8")).hexdigest()
        request = Request(
            self.receiver_url,
            data=json.dumps(payload, separators=(",", ":")).encode("utf-8"),
            headers={
                "Content-Type": "application/json",
                "Accept": "application/json",
                "Idempotency-Key": key,
            },
            method="POST",
        )
        with urlopen(request, timeout=self.timeout_s) as response:
            if response.status < 200 or response.status >= 300:
                raise RuntimeError(f"Component 3 returned HTTP {response.status}")
            response.read()

    def run(self) -> None:
        retry_delay = self.retry_initial_s
        self.logger.info(
            "Component 3 sender started | endpoint=%s | durable outbox=%s",
            self.receiver_url, self.spool_path
        )
        while not self.stop_event.is_set():
            line = self._first_pending_line()
            if line is None:
                self.new_record_event.wait(timeout=0.5)
                self.new_record_event.clear()
                continue
            try:
                self._send(line)
                self._acknowledge_first(line)
                self.logger.info("Delivered vehicle state to Component 3")
                retry_delay = self.retry_initial_s
            except (HTTPError, URLError, TimeoutError, OSError, ValueError, RuntimeError) as exc:
                self.logger.warning(
                    "Component 3 delivery failed (%s); retrying in %.1fs. "
                    "The record remains in the durable outbox.",
                    exc, retry_delay
                )
                self.stop_event.wait(retry_delay)
                retry_delay = min(retry_delay * 2, self.retry_max_s)

        pending = self._first_pending_line()
        if pending is not None:
            self.logger.info(
                "Sender stopped with unsent records retained in %s", self.spool_path
            )

    def stop(self) -> None:
        self.stop_event.set()
        self.new_record_event.set()
