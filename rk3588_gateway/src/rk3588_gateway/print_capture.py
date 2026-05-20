from __future__ import annotations

import asyncio
import logging
import os
import select
import time
from datetime import datetime
from pathlib import Path
from threading import Event
from typing import Optional

from .compat import to_thread, unlink_missing_ok
from .config import PrintCaptureConfig
from .events import GatewayEvent
from .printer import Printer
from .queue import EventQueue
from .report_pdf import ReportPdfConverter
from .vm_transfer import VmTransfer

LOGGER = logging.getLogger(__name__)


class PrintCapture:
    def __init__(
        self,
        config: PrintCaptureConfig,
        queue: EventQueue,
        device_id: str,
        vm_transfer: Optional[VmTransfer] = None,
        report_pdf: Optional[ReportPdfConverter] = None,
        printer: Optional[Printer] = None,
    ) -> None:
        self.config = config
        self.queue = queue
        self.device_id = device_id
        self.vm_transfer = vm_transfer
        self.report_pdf = report_pdf
        self.printer = printer
        self.output_dir = Path(config.output_dir)
        self.output_dir.mkdir(parents=True, exist_ok=True)
        self._stop = asyncio.Event()
        self._thread_stop = Event()

    def stop(self) -> None:
        self._stop.set()
        self._thread_stop.set()

    async def run(self) -> None:
        if not self.config.enabled:
            LOGGER.info("print capture disabled")
            return

        while not self._stop.is_set():
            if not Path(self.config.device).exists():
                LOGGER.warning("waiting for printer gadget: %s", self.config.device)
                await asyncio.sleep(2)
                continue

            try:
                await to_thread(self._capture_loop)
            except Exception:
                LOGGER.exception("print capture loop failed")
                await asyncio.sleep(2)

    def _capture_loop(self) -> None:
        LOGGER.info("printer capture opened: %s", self.config.device)
        try:
            fd = os.open(self.config.device, os.O_RDWR | os.O_NONBLOCK)
        except OSError:
            LOGGER.exception("printer capture read-write open failed, fallback to read-only")
            fd = os.open(self.config.device, os.O_RDONLY | os.O_NONBLOCK)
        current: Optional[Path] = None
        handle = None
        total = 0
        last_data = 0.0
        poller = select.poll()
        poller.register(fd, select.POLLIN)

        try:
            while not self._thread_stop.is_set():
                data = b""
                events = poller.poll(100)
                if events:
                    try:
                        data = os.read(fd, self.config.chunk_size)
                    except BlockingIOError:
                        data = b""
                    except InterruptedError:
                        continue

                now = time.monotonic()
                if data:
                    if handle is None:
                        current = self._new_job_path()
                        handle = current.open("wb")
                        total = 0
                        LOGGER.info("print job start: %s", current)
                    handle.write(data)
                    total += len(data)
                    last_data = now
                    continue

                if handle is not None and now - last_data >= self.config.idle_complete_seconds:
                    handle.close()
                    handle = None
                    assert current is not None
                    if total >= self.config.min_job_bytes:
                        self._finish_job(current, total)
                    else:
                        LOGGER.warning("print job too small: %s bytes=%d", current, total)
                        unlink_missing_ok(current)
                    current = None
                    total = 0

        finally:
            if handle is not None:
                handle.close()
            os.close(fd)

    def _new_job_path(self) -> Path:
        stamp = datetime.now().strftime("%Y%m%d_%H%M%S_%f")
        return self.output_dir / f"print_{stamp}.prn"

    def _finish_job(self, path: Path, total: int) -> None:
        LOGGER.info("print job done: %s bytes=%d", path, total)
        self.queue.put(
            GatewayEvent(
                type="print.captured",
                device_id=self.device_id,
                payload={"path": str(path), "bytes": total},
            )
        )
        if self.report_pdf:
            pdf_path = self.report_pdf.convert(path, "print")
            if pdf_path:
                self.queue.put(
                    GatewayEvent(
                        type="report.pdf_created",
                        device_id=self.device_id,
                        payload={"source": str(path), "path": str(pdf_path), "source_type": "print"},
                    )
                )
                self._print_pdf(pdf_path, "print report")
        if self.vm_transfer:
            try:
                asyncio.run(self._send_to_vm(path, total))
            except Exception:
                LOGGER.exception("vm transfer after print failed")

    async def _send_to_vm(self, path: Path, total: int) -> None:
        assert self.vm_transfer is not None
        ok = await self.vm_transfer.send_file(path)
        self.queue.put(
            GatewayEvent(
                type="print.transferred" if ok else "print.transfer_failed",
                device_id=self.device_id,
                payload={"path": str(path), "bytes": total},
            )
        )

    def _print_pdf(self, path: Path, title: str) -> None:
        if not self.printer:
            return
        try:
            ok = self.printer.print_file_blocking(path, title=title)
            self.queue.put(
                GatewayEvent(
                    type="report.printed" if ok else "report.print_failed",
                    device_id=self.device_id,
                    payload={"path": str(path), "source_type": "print"},
                )
            )
        except Exception:
            LOGGER.exception("print converted pdf failed: %s", path)
