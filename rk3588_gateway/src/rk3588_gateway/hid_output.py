from __future__ import annotations

import asyncio
import logging
import os
import subprocess
from pathlib import Path
from typing import Any, Optional

from .compat import to_thread
from .config import HidInputConfig

LOGGER = logging.getLogger(__name__)
KEY_CAPSLOCK = 0x39
CH9350_KEYBOARD_PREFIX = b"\x57\xab\x01"
CH9350_MOUSE_PREFIX = b"\x57\xab\x02"
CH9350_ABS_MOUSE_PREFIX = b"\x57\xab\x04"
CH9350_RELEASE = CH9350_KEYBOARD_PREFIX + bytes(8)

KEY: dict[str, tuple[int, int]] = {
    "\n": (0, 0x28),
    "\t": (0, 0x2B),
    " ": (0, 0x2C),
    "-": (0, 0x2D),
    "=": (0, 0x2E),
    "[": (0, 0x2F),
    "]": (0, 0x30),
    "\\": (0, 0x31),
    ";": (0, 0x33),
    "'": (0, 0x34),
    "`": (0, 0x35),
    ",": (0, 0x36),
    ".": (0, 0x37),
    "/": (0, 0x38),
    "!": (0x02, 0x1E),
    "@": (0x02, 0x1F),
    "#": (0x02, 0x20),
    "$": (0x02, 0x21),
    "%": (0x02, 0x22),
    "^": (0x02, 0x23),
    "&": (0x02, 0x24),
    "*": (0x02, 0x25),
    "(": (0x02, 0x26),
    ")": (0x02, 0x27),
    "_": (0x02, 0x2D),
    "+": (0x02, 0x2E),
    "{": (0x02, 0x2F),
    "}": (0x02, 0x30),
    "|": (0x02, 0x31),
    ":": (0x02, 0x33),
    '"': (0x02, 0x34),
    "~": (0x02, 0x35),
    "<": (0x02, 0x36),
    ">": (0x02, 0x37),
    "?": (0x02, 0x38),
}

for i in range(10):
    KEY[str(i)] = (0, 0x27 if i == 0 else 0x1D + i)
for i, ch in enumerate("abcdefghijklmnopqrstuvwxyz"):
    KEY[ch] = (0, 0x04 + i)
    KEY[ch.upper()] = (0x02, 0x04 + i)


class HidOutput:
    def __init__(self, config: HidInputConfig) -> None:
        self.config = config
        self._led_state: Optional[int] = None
        self._led_task = None
        self._ch9350_fd: Optional[int] = None
        self._ch9350_rx = bytearray()

    async def execute_form(self, task: dict[str, Any]) -> None:
        if not self.config.enabled:
            LOGGER.info("hid input disabled")
            return
        if self.config.keyboard_backend == "ch9350":
            await self._ensure_ch9350()
        else:
            await self._wait_device(self.config.keyboard_device)
        if self.config.mouse_backend == "usb_gadget":
            await self._wait_device(self.config.mouse_device)
        elif self.config.mouse_backend == "ch9350":
            await self._ensure_ch9350()
        self._start_led_reader()
        await asyncio.sleep(self.config.start_delay_ms / 1000)

        patient = task.get("patient", {})
        events = task.get("eventClassList", [])
        LOGGER.info("hid form start events=%d patient_id=%s", len(events), patient.get("patient_id", ""))
        for event in events:
            click_type = int(event.get("clickType", -1))
            x = int(event.get("x", 0))
            y = int(event.get("y", 0))
            if click_type == 0:
                await self.click(x, y)
            elif click_type == 1:
                await self.input_text(
                    str(event.get("value", "")),
                    x,
                    y,
                    field=str(event.get("field", "")),
                )
            elif click_type == 7:
                condition = event.get("condition") or {}
                if str(patient.get(str(condition.get("field", "")), "")) == str(condition.get("equals", "")):
                    await self.click(x, y)
            else:
                LOGGER.warning("unknown hid clickType=%s event=%s", click_type, event)
            await asyncio.sleep(self.config.action_delay_ms / 1000)
        LOGGER.info("hid form done")

    async def click(self, x: int, y: int) -> None:
        if self.config.mouse_backend == "ch9350":
            await self.ch9350_click_abs(x, y)
            return
        if self.config.mouse_backend != "usb_gadget":
            LOGGER.warning("mouse backend %s does not support click", self.config.mouse_backend)
            return
        ax = max(0, min(32767, int(x * 32767 / max(self.config.screen_width - 1, 1))))
        ay = max(0, min(32767, int(y * 32767 / max(self.config.screen_height - 1, 1))))
        LOGGER.info("hid mouse click x=%d y=%d", x, y)
        await self._write_mouse(0, ax, ay)
        await asyncio.sleep(0.025)
        await self._write_mouse(1, ax, ay)
        await asyncio.sleep(0.04)
        await self._write_mouse(0, ax, ay)

    async def ch9350_click_abs(self, x: int, y: int) -> None:
        LOGGER.info("ch9350 mouse click target x=%d y=%d", x, y)
        if self.config.ch9350_mouse_frame == "absolute7":
            await self._write_ch9350_abs_mouse(button=0, x=x, y=y, wheel=0)
            await asyncio.sleep(0.025)
            await self._write_ch9350_abs_mouse(button=1, x=x, y=y, wheel=0)
            await asyncio.sleep(0.04)
            await self._write_ch9350_abs_mouse(button=0, x=x, y=y, wheel=0)
            return

        if self.config.ch9350_mouse_reset_to_origin:
            await self.ch9350_move_relative(-127, -127, repeat=24)
            await asyncio.sleep(0.08)
        await self.ch9350_move_to(x, y)
        await asyncio.sleep(0.025)
        await self._write_ch9350_mouse(button=1, dx=0, dy=0, wheel=0)
        await asyncio.sleep(0.04)
        await self._write_ch9350_mouse(button=0, dx=0, dy=0, wheel=0)

    async def ch9350_move_to(self, x: int, y: int) -> None:
        target_x = max(0, min(self.config.screen_width - 1, x))
        target_y = max(0, min(self.config.screen_height - 1, y))
        if not self.config.ch9350_mouse_reset_to_origin:
            LOGGER.warning("ch9350 absolute target requested without origin reset; using relative dx/dy directly")
        await self.ch9350_move_relative(target_x, target_y)

    async def ch9350_move_relative(self, dx: int, dy: int, repeat: Optional[int] = None) -> None:
        if repeat is not None:
            for _ in range(repeat):
                await self._write_ch9350_mouse(button=0, dx=dx, dy=dy, wheel=0)
                await asyncio.sleep(0.01)
            return

        remaining_x = dx
        remaining_y = dy
        while remaining_x or remaining_y:
            step_x = max(-127, min(127, remaining_x))
            step_y = max(-127, min(127, remaining_y))
            await self._write_ch9350_mouse(button=0, dx=step_x, dy=step_y, wheel=0)
            remaining_x -= step_x
            remaining_y -= step_y
            await asyncio.sleep(0.01)

    async def input_text(self, text: str, x: int, y: int, field: str = "") -> None:
        if not text:
            return
        if all(ch in KEY for ch in text):
            LOGGER.info("hid input field=%s ascii text=%s", field, text)
            await self.click(x, y)
            await asyncio.sleep(0.025)
            await self.select_all()
            await self.type_ascii(text)
        elif self.config.non_ascii_mode == "powershell":
            LOGGER.info("hid input field=%s non-ascii text=%s", field, text)
            await self.paste_text_windows(text, x, y)
        else:
            LOGGER.warning("skip non-ascii hid text len=%d text=%s", len(text), text)

    async def type_ascii(self, text: str) -> None:
        LOGGER.info("hid type ascii len=%d text=%s", len(text), text)
        if self.config.force_caps_ascii:
            await self.type_ascii_caps_guard(text)
            return
        for ch in text:
            mod, code = KEY[ch]
            await self._press_key(mod, code)
            await asyncio.sleep(0.006)

    async def type_ascii_caps_guard(self, text: str) -> None:
        old_caps = await self._wait_caps()
        await self._ensure_caps(True)
        try:
            for ch in text.lower():
                if ch not in KEY:
                    LOGGER.warning("unsupported ascii char skipped: %r", ch)
                    continue
                mod, code = KEY[ch]
                await self._press_key(mod, code)
                await asyncio.sleep(0.006)
        finally:
            await self._ensure_caps(bool(old_caps) if old_caps is not None else False)

    async def paste_text_windows(self, text: str, x: int, y: int) -> None:
        command = self._powershell_clipboard_command(text)
        LOGGER.info("hid paste text len=%d", len(text))
        await self._press_key(0x08, 0x15)  # Win+R
        await asyncio.sleep(0.35)
        await self.select_all()
        await self.type_ascii_caps_guard(command)
        await self._press_key(0, 0x28)  # Enter
        await asyncio.sleep(self.config.powershell_wait_ms / 1000)
        await self.click(x, y)
        await asyncio.sleep(0.1)
        await self.select_all()
        await self._press_key(0x01, 0x19)  # Ctrl+V

    def _powershell_clipboard_command(self, text: str) -> str:
        parts = "+".join(f"[char]{ord(ch)}" for ch in text)
        return f"powershell -sta -nop -w hidden -c \"Set-Clipboard -Value ({parts})\""

    async def select_all(self) -> None:
        await self._press_key(0x01, 0x04)  # Ctrl+A
        await asyncio.sleep(0.05)

    async def _press_key(self, mod: int, code: int) -> None:
        await self._write_keyboard(bytes([mod, 0, code, 0, 0, 0, 0, 0]))
        await asyncio.sleep(0.008)
        await self._write_keyboard(bytes(8))

    def _start_led_reader(self) -> None:
        if self._led_task and not self._led_task.done():
            return
        self._led_task = asyncio.create_task(self._led_reader_loop())

    async def _led_reader_loop(self) -> None:
        if self.config.keyboard_backend == "ch9350":
            await self._ch9350_led_reader_loop()
            return
        while True:
            try:
                await self._wait_device(self.config.keyboard_device)
                fd = os.open(self.config.keyboard_device, os.O_RDONLY | os.O_NONBLOCK)
                LOGGER.info("keyboard led reader start")
                try:
                    while True:
                        try:
                            data = os.read(fd, 8)
                            if data:
                                self._led_state = data[0]
                                LOGGER.info(
                                    "keyboard led state=0x%02x caps=%s",
                                    data[0],
                                    "on" if data[0] & 2 else "off",
                                )
                        except BlockingIOError:
                            await asyncio.sleep(0.05)
                finally:
                    os.close(fd)
            except asyncio.CancelledError:
                raise
            except Exception:
                LOGGER.exception("keyboard led reader failed")
                await asyncio.sleep(1)

    async def _ch9350_led_reader_loop(self) -> None:
        while True:
            try:
                await self._ensure_ch9350()
                assert self._ch9350_fd is not None
                LOGGER.info("ch9350 serial reader start: %s", self.config.ch9350_serial_device)
                while True:
                    try:
                        data = os.read(self._ch9350_fd, 64)
                        if data:
                            self._parse_ch9350_rx(data)
                    except BlockingIOError:
                        await asyncio.sleep(0.02)
            except asyncio.CancelledError:
                raise
            except Exception:
                LOGGER.exception("ch9350 serial reader failed")
                await asyncio.sleep(1)

    def _parse_ch9350_rx(self, data: bytes) -> None:
        self._ch9350_rx.extend(data)
        while len(self._ch9350_rx) >= 4:
            if self._ch9350_rx[0] != 0x57 or self._ch9350_rx[1] != 0xAB:
                del self._ch9350_rx[0]
                continue
            frame_type = self._ch9350_rx[2]
            if frame_type == 0x80:
                status = self._ch9350_rx[3]
                self._led_state = status
                LOGGER.info(
                    "ch9350 status frame raw=0x%02x caps=%s",
                    status,
                    "on" if status & self.config.ch9350_caps_led_mask else "off",
                )
                del self._ch9350_rx[:4]
                continue
            if frame_type == 0x01 and len(self._ch9350_rx) >= 11:
                LOGGER.debug("ch9350 keyboard rx frame=%s", self._ch9350_rx[:11].hex(" "))
                del self._ch9350_rx[:11]
                continue
            if frame_type == 0x02 and len(self._ch9350_rx) >= 7:
                LOGGER.debug("ch9350 mouse rx frame=%s", self._ch9350_rx[:7].hex(" "))
                del self._ch9350_rx[:7]
                continue
            break

    def _get_caps(self) -> Optional[bool]:
        if self._led_state is None:
            return None
        if self.config.keyboard_backend == "ch9350":
            return bool(self._led_state & self.config.ch9350_caps_led_mask)
        return bool(self._led_state & 2)

    async def _wait_caps(self, timeout: float = 0.5) -> Optional[bool]:
        end = asyncio.get_running_loop().time() + timeout
        while asyncio.get_running_loop().time() < end:
            state = self._get_caps()
            if state is not None:
                return state
            await asyncio.sleep(0.05)
        return self._get_caps()

    async def _ensure_caps(self, target: bool) -> None:
        state = await self._wait_caps()
        if state is target:
            return
        await self._press_key(0, KEY_CAPSLOCK)
        await asyncio.sleep(0.2)
        state = await self._wait_caps()
        if state is not None and state is not target:
            await self._press_key(0, KEY_CAPSLOCK)
            await asyncio.sleep(0.2)

    async def _write_keyboard(self, report: bytes) -> None:
        if self.config.keyboard_backend == "ch9350":
            await self._write_ch9350_keyboard(report)
            return
        await to_thread(self._write_file, self.config.keyboard_device, report)

    async def _write_mouse(self, button: int, ax: int, ay: int) -> None:
        report = bytes([button, ax & 0xFF, (ax >> 8) & 0xFF, ay & 0xFF, (ay >> 8) & 0xFF])
        await to_thread(self._write_file, self.config.mouse_device, report)

    def _write_file(self, path: str, data: bytes) -> None:
        with open(path, "wb", buffering=0) as handle:
            handle.write(data)

    async def _ensure_ch9350(self) -> None:
        if self._ch9350_fd is not None:
            return
        await self._wait_device(self.config.ch9350_serial_device)
        await to_thread(self._configure_ch9350_serial)
        fd = os.open(self.config.ch9350_serial_device, os.O_RDWR | os.O_NOCTTY | os.O_NONBLOCK)
        self._ch9350_fd = fd
        LOGGER.info("ch9350 serial opened: %s baud=%d", self.config.ch9350_serial_device, self.config.ch9350_baudrate)
        if self.config.ch9350_set_state2 or self.config.ch9350_state:
            state = 2 if self.config.ch9350_set_state2 else self.config.ch9350_state
            os.write(fd, bytes([0x57, 0xAB, 0x40, state & 0xFF]))
            LOGGER.info("ch9350 set state%d frame sent", state)
            await asyncio.sleep(0.2)

    def _configure_ch9350_serial(self) -> None:
        subprocess.run(
            [
                "stty",
                "-F",
                self.config.ch9350_serial_device,
                str(self.config.ch9350_baudrate),
                "cs8",
                "-cstopb",
                "-parenb",
                "raw",
                "-echo",
            ],
            check=True,
        )

    async def _write_ch9350_keyboard(self, report: bytes) -> None:
        await self._ensure_ch9350()
        assert self._ch9350_fd is not None
        frame = CH9350_KEYBOARD_PREFIX + report
        await to_thread(os.write, self._ch9350_fd, frame)

    async def _write_ch9350_mouse(self, button: int, dx: int, dy: int, wheel: int = 0) -> None:
        await self._ensure_ch9350()
        assert self._ch9350_fd is not None
        if self.config.ch9350_mouse_frame != "relative4":
            LOGGER.warning("unsupported ch9350 mouse frame mode=%s, using relative4", self.config.ch9350_mouse_frame)
        report = bytes([button & 0x07, dx & 0xFF, dy & 0xFF, wheel & 0xFF])
        frame = CH9350_MOUSE_PREFIX + report
        await to_thread(os.write, self._ch9350_fd, frame)

    async def _write_ch9350_abs_mouse(self, button: int, x: int, y: int, wheel: int = 0) -> None:
        await self._ensure_ch9350()
        assert self._ch9350_fd is not None
        ax = max(0, min(0x3FF, int(x * 0x3FF / max(self.config.screen_width - 1, 1))))
        ay = max(0, min(0x3FF, int(y * 0x3FF / max(self.config.screen_height - 1, 1))))
        report = bytes([
            0x01,
            button & 0x07,
            ax & 0xFF,
            (ax >> 8) & 0xFF,
            ay & 0xFF,
            (ay >> 8) & 0xFF,
            wheel & 0xFF,
        ])
        frame = CH9350_ABS_MOUSE_PREFIX + report
        LOGGER.info("ch9350 abs mouse report x=%d y=%d ax=%d ay=%d button=%d", x, y, ax, ay, button)
        await to_thread(os.write, self._ch9350_fd, frame)

    async def _wait_device(self, path: str) -> None:
        for _ in range(60):
            if Path(path).exists():
                return
            LOGGER.warning("waiting for hid device: %s", path)
            await asyncio.sleep(1)
        raise FileNotFoundError(path)
