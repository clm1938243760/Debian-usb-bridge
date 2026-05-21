from __future__ import annotations

import argparse
import asyncio
import logging
import signal
from pathlib import Path

from .api import LocalApi
from .config import load_config
from .events import GatewayEvent
from .gpio import GpioController
from .hid import ScannerReader
from .msc_monitor import MscMonitor
from .printer import Printer
from .print_capture import PrintCapture
from .queue import EventQueue
from .report_pdf import ReportPdfConverter
from .report_upload import ReportUploadWorker
from .uploader import Uploader
from .vm_transfer import VmTransfer
from .workflow import GatewayWorkflow


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="RK3568 headless gateway service")
    parser.add_argument(
        "--config",
        default="config.yaml",
        help="Path to config YAML file",
    )
    return parser.parse_args()


async def main_async() -> None:
    args = parse_args()
    config = load_config(Path(args.config))
    logging.basicConfig(
        level=getattr(logging, config.logging.level, logging.INFO),
        format="%(asctime)s %(levelname)s %(name)s: %(message)s",
    )

    queue = EventQueue(config.storage.sqlite_path)
    printer = Printer(config.printer)
    gpio = GpioController(config.gpio)
    report_pdf = ReportPdfConverter(config.report_pdf)
    report_upload = ReportUploadWorker(config.report_upload, config.report_pdf, queue, config.device.id)
    vm_transfer = VmTransfer(config.vm_transfer)
    print_capture = PrintCapture(config.print_capture, queue, config.device.id, vm_transfer, report_pdf, printer)
    scanner = ScannerReader(config.scanner, config.device.id)
    uploader = Uploader(config.uploader, queue)
    local_api = LocalApi(config, queue, printer, gpio)
    workflow = GatewayWorkflow(config, queue)
    msc_monitor = MscMonitor(config.msc, queue, config.device.id, report_pdf, printer, workflow.is_hid_input_active)
    local_api.workflow = workflow

    stop_event = asyncio.Event()

    def request_stop() -> None:
        scanner.stop()
        print_capture.stop()
        msc_monitor.stop()
        report_upload.stop()
        uploader.stop()
        stop_event.set()

    loop = asyncio.get_running_loop()
    for sig in (signal.SIGINT, signal.SIGTERM):
        loop.add_signal_handler(sig, request_stop)

    workflow_tasks = set()

    async def run_scan_workflow(code: str) -> None:
        try:
            await workflow.handle_scan(code)
        except Exception:
            logging.getLogger(__name__).exception("scan workflow failed code=%s", code)

    async def queue_event(event: GatewayEvent) -> None:
        queue.put(event)
        logging.getLogger(__name__).info(
            "queued event %s %s payload=%s",
            event.type,
            event.id,
            event.payload,
        )
        if event.type == "barcode.scan":
            code = str(event.payload.get("code", ""))
            if code:
                task = asyncio.create_task(run_scan_workflow(code))
                workflow_tasks.add(task)
                task.add_done_callback(workflow_tasks.discard)

    async def gpio_key_loop() -> None:
        last_values = {}
        while not stop_event.is_set():
            try:
                await gpio.refresh_inputs()
                for line in gpio.snapshot():
                    name = str(line.get("name", ""))
                    value = int(line.get("value", 0))
                    previous = last_values.get(name, 0)
                    if value == 1 and previous == 0:
                        workflow.handle_key(name)
                    last_values[name] = value
            except Exception:
                logging.getLogger(__name__).exception("gpio key loop failed")
            await asyncio.sleep(0.12)

    await gpio.start()
    await local_api.start()
    tasks = [
        asyncio.create_task(scanner.run(queue_event)),
        asyncio.create_task(print_capture.run()),
        asyncio.create_task(msc_monitor.run()),
        asyncio.create_task(report_upload.run()),
        asyncio.create_task(uploader.run()),
        asyncio.create_task(gpio_key_loop()),
    ]

    await stop_event.wait()
    await local_api.stop()
    await gpio.stop()
    for task in tasks:
        task.cancel()
    for task in workflow_tasks:
        task.cancel()
    await asyncio.gather(*tasks, return_exceptions=True)
    await asyncio.gather(*workflow_tasks, return_exceptions=True)


def main() -> None:
    asyncio.run(main_async())


if __name__ == "__main__":
    main()
