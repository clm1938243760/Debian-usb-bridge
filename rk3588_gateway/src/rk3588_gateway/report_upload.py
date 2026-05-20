from __future__ import annotations

import asyncio
import concurrent.futures
import hashlib
import json
import logging
import time
import uuid
import urllib.error
import urllib.request
from datetime import datetime
from pathlib import Path
from typing import Dict, Iterable, Tuple

from .compat import to_thread
from .config import ReportPdfConfig, ReportUploadConfig
from .events import GatewayEvent
from .queue import EventQueue

LOGGER = logging.getLogger(__name__)


class ReportUploadWorker:
    def __init__(
        self,
        config: ReportUploadConfig,
        report_pdf_config: ReportPdfConfig,
        queue: EventQueue,
        device_id: str,
    ) -> None:
        self.config = config
        self.report_pdf_config = report_pdf_config
        self.queue = queue
        self.device_id = device_id
        self.watch_dir = Path(report_pdf_config.output_dir)
        self.state_dir = Path(config.state_dir)
        self.records_file = self.state_dir / "uploads.jsonl"
        self._stop = asyncio.Event()

    def stop(self) -> None:
        self._stop.set()

    async def run(self) -> None:
        if not self.config.enabled:
            LOGGER.info("report upload disabled")
            return
        if not self.config.endpoint:
            LOGGER.warning("report upload endpoint is empty")
            return

        self.state_dir.mkdir(parents=True, exist_ok=True)
        self.records_file.touch(exist_ok=True)
        self.watch_dir.mkdir(parents=True, exist_ok=True)
        LOGGER.info("report upload worker watching: %s", self.watch_dir)

        await to_thread(self._init_baseline_if_needed)
        while not self._stop.is_set():
            try:
                await to_thread(self._scan_once)
            except (asyncio.CancelledError, concurrent.futures.CancelledError):
                return
            except Exception:
                LOGGER.exception("report upload scan failed")
            await asyncio.sleep(self.config.poll_interval_seconds)

    def _init_baseline_if_needed(self) -> None:
        if not self.config.init_baseline:
            return
        if self.records_file.stat().st_size > 0:
            return
        files = self._pdf_files()
        if not files:
            return
        LOGGER.info("report upload baseline existing pdf count=%d", len(files))
        for path in files:
            try:
                info = self._file_info(path)
                self._append_record(info, "baseline", 0, "")
            except Exception:
                LOGGER.exception("report upload baseline failed: %s", path)

    def _scan_once(self) -> None:
        if not Path(self.config.report_info_path).exists():
            LOGGER.warning("report upload ReportInfo.xml missing: %s", self.config.report_info_path)
            return

        records = self._load_records()
        now = time.time()
        for path in self._pdf_files():
            info = self._file_info(path)
            signature = str(info["signature"])
            record = records.get(signature)
            if record and str(record.get("status", "")) in ("uploaded", "baseline"):
                continue

            attempts = int(record.get("attempts", 0)) if record else 0
            last_attempt_at = float(record.get("attempt_at", 0) or 0) if record else 0.0
            if attempts >= self.config.max_attempts:
                continue
            if attempts and now - last_attempt_at < self.config.retry_interval_seconds:
                continue

            ok, error = self._upload(path)
            attempts += 1
            status = "uploaded" if ok else "failed"
            self._append_record(info, status, attempts, error)
            records[signature] = {
                "status": status,
                "attempts": attempts,
                "attempt_at": time.time(),
            }
            self.queue.put(
                GatewayEvent(
                    type="report.uploaded" if ok else "report.upload_failed",
                    device_id=self.device_id,
                    payload={"path": str(path), "signature": signature, "attempts": attempts, "error": error},
                )
            )

    def _upload(self, pdf_path: Path) -> Tuple[bool, str]:
        boundary = "----rk3568-gateway-%s" % uuid.uuid4().hex
        report_info = Path(self.config.report_info_path)
        body = _multipart_body(
            boundary,
            (
                ("Report", pdf_path, "application/pdf"),
                ("ReportInfo", report_info, "application/xml"),
            ),
        )
        request = urllib.request.Request(
            self.config.endpoint,
            data=body,
            headers={
                "Content-Type": "multipart/form-data; boundary=%s" % boundary,
                "Content-Length": str(len(body)),
                "User-Agent": "RK3568-Gateway",
            },
            method="POST",
        )
        try:
            with urllib.request.urlopen(request, timeout=self.config.timeout_seconds) as response:
                status = int(getattr(response, "status", response.getcode()))
                text = response.read().decode("utf-8", errors="replace")
            if 200 <= status < 300:
                LOGGER.info("report upload submitted path=%s status=%s response=%s", pdf_path, status, text[:500])
                return True, ""
            error = "status=%s response=%s" % (status, text[:500])
            LOGGER.error("report upload failed path=%s %s", pdf_path, error)
            return False, error
        except urllib.error.HTTPError as exc:
            detail = exc.read().decode("utf-8", errors="replace")
            error = "status=%s response=%s" % (exc.code, detail[:500])
            LOGGER.error("report upload http error path=%s %s", pdf_path, error)
            return False, error
        except Exception as exc:
            error = str(exc)
            LOGGER.exception("report upload exception path=%s", pdf_path)
            return False, error

    def _pdf_files(self) -> list[Path]:
        if not self.watch_dir.exists():
            return []
        return sorted(path for path in self.watch_dir.glob("*.pdf") if path.is_file())

    def _file_info(self, path: Path) -> Dict[str, object]:
        stat = path.stat()
        digest = hashlib.sha256()
        with path.open("rb") as handle:
            for chunk in iter(lambda: handle.read(1024 * 1024), b""):
                digest.update(chunk)
        sha256 = digest.hexdigest()
        signature = "%s|%s|%s" % (path.name, stat.st_size, sha256)
        return {
            "path": str(path),
            "name": path.name,
            "size": stat.st_size,
            "sha256": sha256,
            "signature": signature,
        }

    def _load_records(self) -> Dict[str, Dict[str, object]]:
        records = {}
        try:
            lines = self.records_file.read_text(encoding="utf-8").splitlines()
        except FileNotFoundError:
            return records
        for line in lines:
            if not line.strip():
                continue
            try:
                item = json.loads(line)
            except json.JSONDecodeError:
                continue
            signature = str(item.get("signature", "")).strip()
            if signature:
                records[signature] = item
        return records

    def _append_record(self, info: Dict[str, object], status: str, attempts: int, error: str) -> None:
        record = dict(info)
        record.update(
            {
                "status": status,
                "attempts": attempts,
                "error": error[:500],
                "attempt_at": time.time(),
                "created_at": datetime.now().isoformat(timespec="seconds"),
            }
        )
        with self.records_file.open("a", encoding="utf-8") as handle:
            handle.write(json.dumps(record, ensure_ascii=False) + "\n")


def _multipart_body(boundary: str, files: Iterable[Tuple[str, Path, str]]) -> bytes:
    chunks = []
    boundary_bytes = boundary.encode("ascii")
    for field_name, path, content_type in files:
        chunks.extend(
            [
                b"--" + boundary_bytes + b"\r\n",
                (
                    'Content-Disposition: form-data; name="%s"; filename="%s"\r\n'
                    % (field_name, path.name)
                ).encode("utf-8"),
                ("Content-Type: %s\r\n\r\n" % content_type).encode("ascii"),
                path.read_bytes(),
                b"\r\n",
            ]
        )
    chunks.append(b"--" + boundary_bytes + b"--\r\n")
    return b"".join(chunks)
