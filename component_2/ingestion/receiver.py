"""SSE GPS receiver with validation, reconnects, queueing, CSV archive and logs.

Expected producer event data:
{
  "batch_id": 1,
  "sim_time_s": 1.0,
  "count": 1,
  "observations": [
    {"device_id": "...", "timestamp": "...", "latitude": 50.9, "longitude": 6.9}
  ]
}
The receiver also tolerates an optional "type" field in control messages.
"""
from __future__ import annotations

import argparse
import csv
import json
import logging
import math
import queue
import threading
import time
from datetime import datetime
from pathlib import Path
from typing import Any
from urllib.request import Request, urlopen
from urllib.error import URLError, HTTPError

DEFAULT_FIELDS = ["device_id", "timestamp", "latitude", "longitude"]


def load_config(path: str) -> tuple[dict, Path]:
    config_path = Path(path).resolve()
    with config_path.open("r", encoding="utf-8") as f:
        return json.load(f), config_path.parent


def resolve_path(root: Path, value: str) -> Path:
    p = Path(value)
    return p if p.is_absolute() else root / p


def configure_logging(config: dict, root: Path) -> logging.Logger:
    settings = config.get("logging", {})
    log_path = resolve_path(root, settings.get("log_file", "logs/receiver.log"))
    log_path.parent.mkdir(parents=True, exist_ok=True)
    level = getattr(logging, settings.get("level", "INFO").upper(), logging.INFO)
    logger = logging.getLogger("component2.gps_receiver")
    logger.setLevel(level)
    logger.handlers.clear()
    formatter = logging.Formatter("%(asctime)s | %(levelname)s | %(message)s")
    file_handler = logging.FileHandler(log_path, encoding="utf-8")
    file_handler.setFormatter(formatter)
    console_handler = logging.StreamHandler()
    console_handler.setFormatter(formatter)
    logger.addHandler(file_handler)
    logger.addHandler(console_handler)
    logger.info("Logging to %s", log_path)
    return logger


def validate_observation(record: Any, expected_fields: list[str]) -> dict:
    if not isinstance(record, dict):
        raise ValueError("Observation is not a JSON object")
    if set(record.keys()) != set(expected_fields):
        raise ValueError(
            f"Schema mismatch: expected exactly {expected_fields}; "
            f"received {list(record.keys())}"
        )
    if not isinstance(record["device_id"], str) or not record["device_id"].strip():
        raise ValueError("device_id must be a non-empty string")
    if not isinstance(record["timestamp"], str) or not record["timestamp"].strip():
        raise ValueError("timestamp must be a non-empty ISO timestamp string")
    try:
        datetime.fromisoformat(record["timestamp"].replace("Z", "+00:00"))
    except ValueError as exc:
        raise ValueError(f"Invalid ISO timestamp: {record['timestamp']}") from exc
    for key, low, high in (("latitude", -90, 90), ("longitude", -180, 180)):
        value = record[key]
        if isinstance(value, bool) or not isinstance(value, (int, float)):
            raise ValueError(f"{key} must be numeric")
        if not math.isfinite(value) or not low <= value <= high:
            raise ValueError(f"{key} is outside valid range")
    return {
        "device_id": record["device_id"],
        "timestamp": record["timestamp"],
        "latitude": float(record["latitude"]),
        "longitude": float(record["longitude"]),
    }


class CSVWriter(threading.Thread):
    """Consumes validated observations from the in-memory queue and archives them."""
    def __init__(self, records: queue.Queue, output_path: Path, fields: list[str],
                 flush_interval: float, logger: logging.Logger):
        super().__init__(name="gps-csv-writer", daemon=True)
        self.records = records
        self.output_path = output_path
        self.fields = fields
        self.flush_interval = max(0.1, flush_interval)
        self.logger = logger
        self.stop_event = threading.Event()
        self.write_error = None

    def run(self):
        self.output_path.parent.mkdir(parents=True, exist_ok=True)
        new_file = not self.output_path.exists() or self.output_path.stat().st_size == 0
        try:
            with self.output_path.open("a", newline="", encoding="utf-8") as f:
                writer = csv.DictWriter(f, fieldnames=self.fields)
                if new_file:
                    writer.writeheader()
                    f.flush()
                last_flush = time.monotonic()
                while not self.stop_event.is_set() or not self.records.empty():
                    try:
                        item = self.records.get(timeout=0.25)
                    except queue.Empty:
                        item = None
                    if item is not None:
                        writer.writerow(item)
                        self.records.task_done()
                    if time.monotonic() - last_flush >= self.flush_interval:
                        f.flush()
                        last_flush = time.monotonic()
                f.flush()
        except Exception as exc:
            self.write_error = exc
            self.logger.exception("CSV writer failed")

    def stop(self):
        self.stop_event.set()


def parse_sse_data(response, logger: logging.Logger):
    """Yield parsed JSON objects from SSE data fields (supports multiline data)."""
    data_lines = []
    for raw_line in response:
        line = raw_line.decode("utf-8", errors="replace").rstrip("\r\n")
        if line.startswith(":"):  # SSE comment / keep-alive
            continue
        if line.startswith("data:"):
            data_lines.append(line[5:].lstrip())
        elif line == "":
            if data_lines:
                raw_data = "\n".join(data_lines)
                data_lines.clear()
                try:
                    yield json.loads(raw_data)
                except json.JSONDecodeError:
                    logger.exception("Could not decode SSE JSON data: %s", raw_data[:500])
        # event:, id:, and retry: fields are not needed by this receiver.
    if data_lines:
        try:
            yield json.loads("\n".join(data_lines))
        except json.JSONDecodeError:
            logger.exception("Could not decode final SSE event")


def run_receiver(config: dict, root: Path, logger: logging.Logger,
                 url_override: str | None = None):
    conn = config.get("connection", {})
    receiver = config.get("receiver", {})
    storage = config.get("storage", {})
    processing = config.get("processing", {})
    stream_url = url_override or conn.get(
        "server_url", "http://127.0.0.1:8765/gps/stream"
    )
    fields = receiver.get("expected_fields", DEFAULT_FIELDS)
    if fields != DEFAULT_FIELDS:
        raise ValueError(f"This GPS contract requires exactly {DEFAULT_FIELDS}")

    save_raw = bool(storage.get("save_raw_data", True))
    output_dir = resolve_path(root, storage.get("output_directory", "data/received"))
    output_path = output_dir / storage.get("output_filename", "received_gps.csv")
    max_records = int(receiver.get("queue_max_records", 50000))
    record_queue: queue.Queue = queue.Queue(maxsize=max_records)

    writer = None
    if save_raw:
        output_dir.mkdir(parents=True, exist_ok=True)
        writer = CSVWriter(
            record_queue, output_path, fields,
            float(storage.get("flush_interval_s", 5)), logger
        )
        writer.start()
        logger.info("Raw GPS archive: %s", output_path)

    reconnect = bool(conn.get("reconnect_enabled", True))
    delay = float(conn.get("reconnect_initial_delay_s", 1))
    max_delay = float(conn.get("reconnect_max_delay_s", 30))
    multiplier = float(conn.get("reconnect_backoff_multiplier", 2))
    timeout = float(conn.get("read_timeout_s", 75))
    detect_gaps = bool(receiver.get("detect_batch_gaps", True))
    drop_invalid = bool(receiver.get("drop_invalid_records", True))
    validate = bool(receiver.get("validate_gps_schema", True))
    total_valid = 0
    total_invalid = 0
    last_batch_id = None

    logger.info("Connecting to Component 1 SSE endpoint: %s", stream_url)
    try:
        while True:
            try:
                request = Request(
                    stream_url,
                    headers={"Accept": "text/event-stream", "Cache-Control": "no-cache"}
                )
                with urlopen(request, timeout=timeout) as response:
                    logger.info("SSE connected (HTTP %s)", response.status)
                    delay = float(conn.get("reconnect_initial_delay_s", 1))

                    for payload in parse_sse_data(response, logger):
                        message_type = payload.get("type")
                        if message_type == "error":
                            raise RuntimeError(payload.get("message", "Producer error"))
                        if message_type == "complete":
                            logger.info("Producer marked stream complete")
                            return

                        # Producer batches contain batch_id/sim_time_s/count/observations.
                        observations = payload.get("observations")
                        if observations is None:
                            logger.debug("Ignoring SSE control payload: %s", payload)
                            continue

                        batch_id = payload.get("batch_id")
                        if batch_id is not None:
                            try:
                                batch_id = int(batch_id)
                                if detect_gaps and last_batch_id is not None and batch_id != last_batch_id + 1:
                                    logger.warning(
                                        "Batch ID gap: expected %s, received %s",
                                        last_batch_id + 1, batch_id
                                    )
                                last_batch_id = batch_id
                            except (TypeError, ValueError):
                                logger.warning("Non-integer batch_id: %r", batch_id)

                        batch_valid = 0
                        batch_invalid = 0
                        for raw_record in observations:
                            try:
                                record = (
                                    validate_observation(raw_record, fields)
                                    if validate else raw_record
                                )
                            except (ValueError, TypeError) as exc:
                                batch_invalid += 1
                                total_invalid += 1
                                logger.warning("Rejected invalid GPS record: %s", exc)
                                if not drop_invalid:
                                    raise
                                continue

                            if processing.get("forward_to_queue", True) or save_raw:
                                try:
                                    record_queue.put(record, timeout=5)
                                except queue.Full:
                                    logger.error(
                                        "Receiver queue full; record could not be queued. "
                                        "Increase receiver.queue_max_records or speed up consumers."
                                    )
                                    raise RuntimeError("GPS receiver queue is full")
                            batch_valid += 1
                            total_valid += 1

                        logger.info(
                            "Batch %s | sim_time=%s | received=%s | valid=%s | invalid=%s | total_valid=%s",
                            batch_id, payload.get("sim_time_s"),
                            len(observations), batch_valid, batch_invalid, total_valid
                        )

            except KeyboardInterrupt:
                logger.info("Receiver stopped by user")
                break
            except (HTTPError, URLError, TimeoutError, OSError, RuntimeError) as exc:
                logger.exception("SSE connection/processing failed: %s", exc)
                if not reconnect:
                    raise
                logger.info("Reconnecting in %.1f seconds", delay)
                time.sleep(delay)
                delay = min(delay * multiplier, max_delay)
    finally:
        if writer:
            writer.stop()
            writer.join(timeout=15)
            if writer.is_alive():
                logger.error("CSV writer did not stop within 15 seconds")
            if writer.write_error:
                raise RuntimeError("CSV writer failed") from writer.write_error
        logger.info(
            "Receiver stopped. Valid records=%s, invalid records=%s",
            total_valid, total_invalid
        )


def main():
    parser = argparse.ArgumentParser(description="Component 2 live GPS SSE receiver")
    parser.add_argument("--config", default="receiver_config.json")
    parser.add_argument("--url", default=None, help="Override configured SSE URL")
    args = parser.parse_args()

    config, root = load_config(args.config)
    logger = configure_logging(config, root)
    run_receiver(config, root, logger, args.url)


if __name__ == "__main__":
    main()
