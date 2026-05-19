from __future__ import annotations

import asyncio
import logging
from pathlib import Path
from tempfile import NamedTemporaryFile

from .config import PrinterConfig

LOGGER = logging.getLogger(__name__)


class Printer:
    def __init__(self, config: PrinterConfig) -> None:
        self.config = config

    async def print_text(self, text: str, title: str = "rk3588-gateway") -> bool:
        if not self.config.enabled:
            LOGGER.info("printer disabled, skipped print job: %s", title)
            return False

        with NamedTemporaryFile("w", encoding="utf-8", delete=False, suffix=".txt") as handle:
            handle.write(text)
            temp_path = Path(handle.name)

        command = [self.config.command]
        if self.config.printer_name:
            command.extend(["-d", self.config.printer_name])
        command.extend(["-t", title, str(temp_path)])

        try:
            proc = await asyncio.create_subprocess_exec(
                *command,
                stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.PIPE,
            )
            stdout, stderr = await asyncio.wait_for(
                proc.communicate(),
                timeout=self.config.timeout_seconds,
            )
            if proc.returncode == 0:
                LOGGER.info("print submitted: %s", stdout.decode(errors="replace").strip())
                return True
            LOGGER.error("print failed: %s", stderr.decode(errors="replace").strip())
            return False
        finally:
            temp_path.unlink(missing_ok=True)
