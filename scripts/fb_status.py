#!/usr/bin/env python3
from __future__ import annotations

import argparse
import os
import json
import struct
import subprocess
import time
import traceback
import urllib.request
from pathlib import Path
from typing import Any, Optional

from PIL import Image, ImageDraw, ImageFont

FBIOGET_VSCREENINFO = 0x4600
CANVAS_W = 480
CANVAS_H = 320
DESIGN_W = 357
DESIGN_H = 140
CARD_W = CANVAS_W
CARD_H = round(CARD_W * DESIGN_H / DESIGN_W)
CARD_X = (CANVAS_W - CARD_W) // 2
CARD_Y = (CANVAS_H - CARD_H) // 2


def resample_bilinear() -> int:
    return getattr(getattr(Image, "Resampling", Image), "BILINEAR")


def resample_lanczos() -> int:
    return getattr(getattr(Image, "Resampling", Image), "LANCZOS")


def rounded_rectangle(
    draw: ImageDraw.ImageDraw,
    xy: tuple[int, int, int, int],
    radius: int,
    fill: Optional[tuple[int, int, int, int]] = None,
    outline: Optional[tuple[int, int, int, int]] = None,
    width: int = 1,
) -> None:
    x0, y0, x1, y1 = (int(value) for value in xy)
    if x1 < x0:
        x0, x1 = x1, x0
    if y1 < y0:
        y0, y1 = y1, y0
    radius = max(0, min(int(radius), (x1 - x0) // 2, (y1 - y0) // 2))
    if radius <= 0:
        draw.rectangle((x0, y0, x1, y1), fill=fill, outline=outline)
        return

    if fill is not None:
        draw.rectangle((x0 + radius, y0, x1 - radius, y1), fill=fill)
        draw.rectangle((x0, y0 + radius, x1, y1 - radius), fill=fill)
        draw.pieslice((x0, y0, x0 + radius * 2, y0 + radius * 2), 180, 270, fill=fill)
        draw.pieslice((x1 - radius * 2, y0, x1, y0 + radius * 2), 270, 360, fill=fill)
        draw.pieslice((x1 - radius * 2, y1 - radius * 2, x1, y1), 0, 90, fill=fill)
        draw.pieslice((x0, y1 - radius * 2, x0 + radius * 2, y1), 90, 180, fill=fill)

    if outline is None or width <= 0:
        return
    for offset in range(width):
        ox0, oy0, ox1, oy1 = x0 + offset, y0 + offset, x1 - offset, y1 - offset
        rr = max(1, radius - offset)
        draw.line((ox0 + rr, oy0, ox1 - rr, oy0), fill=outline)
        draw.line((ox1, oy0 + rr, ox1, oy1 - rr), fill=outline)
        draw.line((ox0 + rr, oy1, ox1 - rr, oy1), fill=outline)
        draw.line((ox0, oy0 + rr, ox0, oy1 - rr), fill=outline)
        draw.arc((ox0, oy0, ox0 + rr * 2, oy0 + rr * 2), 180, 270, fill=outline)
        draw.arc((ox1 - rr * 2, oy0, ox1, oy0 + rr * 2), 270, 360, fill=outline)
        draw.arc((ox1 - rr * 2, oy1 - rr * 2, ox1, oy1), 0, 90, fill=outline)
        draw.arc((ox0, oy1 - rr * 2, ox0 + rr * 2, oy1), 90, 180, fill=outline)


def text_bbox(draw: ImageDraw.ImageDraw, text: str, font: ImageFont.ImageFont) -> tuple[int, int, int, int]:
    native = getattr(draw, "textbbox", None)
    if native is not None:
        return native((0, 0), text, font=font)
    text_size = getattr(draw, "textsize", None)
    if text_size is not None:
        width, height = text_size(text, font=font)
        return (0, 0, int(width), int(height))
    width, height = font.getsize(text)
    return (0, 0, int(width), int(height))


def text_length(draw: ImageDraw.ImageDraw, text: str, font: ImageFont.ImageFont) -> int:
    bbox = text_bbox(draw, text, font)
    return bbox[2] - bbox[0]


TEXT = {
    "wait_scan": "\u5019\u8bca",
    "select_item": "\u60a3\u8005ID\u626b\u7801",
    "inputting": "\u81ea\u52a8\u5f55\u5165",
    "upload_done": "\u62a5\u544a\u4e0a\u4f20\u6210\u529f",
    "not_found": "\u626b\u7801\u672a\u627e\u5230\u7533\u8bf7\u5355",
    "connecting": "\u6b63\u5728\u8fde\u63a5",
    "connected": "\u667a\u80fd\u4f53\u5df2\u7ecf\u8fde\u63a5",
    "connection_failed": "\u8fde\u63a5\u5931\u8d25",
    "printer_error": "\u672c\u5730\u9700\u8981\u6253\u5370\u673a",
    "service_connecting": "\u670d\u52a1\u8fde\u63a5\u4e2d",
    "querying_order": "\u6b63\u5728\u67e5\u8be2\u7533\u8bf7\u5355",
    "input_done": "\u5f55\u5165\u5b8c\u6210",
    "wait_report": "\u6b63\u5728\u68c0\u67e5",
    "order": "\u7533\u8bf7\u5355",
    "no_selectable_item": "\u672a\u67e5\u8be2\u5230\u53ef\u9009\u62e9\u9879\u76ee",
    "unnamed_item": "\u672a\u547d\u540d\u9879\u76ee",
    "select_hint": "UP/DOWN \u9009\u62e9    OK \u786e\u8ba4",
    "self_check_starting": "\u81ea\u68c0\u542f\u52a8\u4e2d",
    "network": "\u7f51\u7edc",
    "service": "\u670d\u52a1",
    "waiting": "\u7b49\u5f85",
    "service_started": "\u670d\u52a1\u542f\u52a8",
    "assets_dir": "\u667a\u80fd\u4f53UI",
    "brand": "\u7279\u68c0\u667a\u80fd\u4f53",
    "scan_prompt": "\u7b49\u5f85\u60a3\u8005\u62a5\u5230",
    "scan_subtitle": "\u8bf7\u8fdb\u884c\u7533\u8bf7\u5355\u626b\u7801",
    "checking": "\u6b63\u5728\u68c0\u67e5",
    "checking_subtitle": "\u8bf7\u7b49\u5f85\u68c0\u6d4b\u7ed3\u679c",
    "auto_input": "\u6b63\u5728\u81ea\u52a8\u5f55\u5165",
    "do_not_touch": "\u8bf7\u52ff\u64cd\u4f5c\u9f20\u6807\u952e\u76d8",
    "upload_done_title": "\u62a5\u544a\u4e0a\u4f20\u5b8c\u6210",
    "ready_scan": "\u53ef\u4ee5\u7ee7\u7eed\u626b\u7801",
    "file_received": "\u6587\u4ef6\u5df2\u63a5\u6536",
    "no_order_title": "\u672a\u627e\u5230\u7533\u8bf7\u5355",
    "no_order_subtitle": "\u8bf7\u6838\u5bf9\u6761\u7801\u540e\u91cd\u8bd5",
    "connecting_title": "\u6b63\u5728\u542f\u52a8",
    "connecting_subtitle": "\u6b63\u5728\u68c0\u67e5\u7f51\u7edc\u4e0e\u8bbe\u5907",
    "connected_title": "\u667a\u80fd\u4f53\u5df2\u7ecf\u8fde\u63a5",
    "connected_subtitle": "\u6b63\u5728\u8fdb\u5165\u5019\u8bca",
    "failed_title": "\u542f\u52a8\u5f02\u5e38",
    "failed_subtitle": "\u8bf7\u68c0\u67e5\u8fde\u63a5\u72b6\u6001",
    "querying_title": "\u6b63\u5728\u67e5\u8be2",
    "querying_subtitle": "\u8bf7\u7a0d\u5019",
    "device_type": "\u8bbe\u5907\u9879\u76ee",
    "patient_items": "\u60a3\u8005\u9879\u76ee",
    "current_input": "\u6b63\u5728\u5f55\u5165",
    "other_items": "\u5176\u4ed6\u9879\u76ee",
    "exam_mismatch": "\u9879\u76ee\u4e0d\u7b26",
    "exam_mismatch_title": "\u60a3\u8005\u68c0\u67e5\u9879\u76ee\u4e0e\u8bbe\u5907\u4e0d\u7b26",
    "exam_mismatch_subtitle": "\u672a\u6267\u884c\u81ea\u52a8\u5f55\u5165",
}

ASSET_PATTERNS = {
    "wait_scan": TEXT["wait_scan"],
    "select_item": TEXT["select_item"],
    "inputting": TEXT["inputting"],
    "upload_done": TEXT["upload_done"],
    "not_found": TEXT["not_found"],
    "connecting": TEXT["connecting"],
    "connected": TEXT["connected"],
    "connection_failed": TEXT["connection_failed"],
    "printer_error": TEXT["printer_error"],
}


def read_state(url: str) -> dict[str, Any]:
    with urllib.request.urlopen(url, timeout=2) as response:
        return json.loads(response.read().decode("utf-8"))


def load_font(size: int, bold: bool = False):
    candidates = (
        "/usr/share/fonts/opentype/noto/NotoSansCJK-Bold.ttc" if bold else "",
        "/usr/share/fonts/opentype/noto/NotoSansCJK-Regular.ttc",
        "/usr/share/fonts/truetype/wqy/wqy-microhei.ttc",
        "/usr/share/fonts/truetype/wqy/wqy-zenhei.ttc",
        "/usr/share/fonts/truetype/dejavu/DejaVuSans-Bold.ttf" if bold else "",
        "/usr/share/fonts/truetype/dejavu/DejaVuSans.ttf",
    )
    for path in candidates:
        if path and Path(path).exists():
            return ImageFont.truetype(path, size)
    return ImageFont.load_default()


class Framebuffer:
    def __init__(self, path: str, width: int, height: int, bpp: int, rotate: int = 0) -> None:
        self.path = path
        self.width = width
        self.height = height
        self.bpp = bpp
        self.rotate = rotate

    @classmethod
    def open(cls, path: str, width: int, height: int, bpp: int, rotate: int = 0) -> "Framebuffer":
        try:
            import fcntl

            with Path(path).open("rb", buffering=0) as handle:
                data = fcntl.ioctl(handle.fileno(), FBIOGET_VSCREENINFO, bytes(160))
            values = struct.unpack_from("8I", data)
            width = int(values[0]) or width
            height = int(values[1]) or height
            bpp = int(values[6]) or bpp
        except Exception:
            pass
        return cls(path, width, height, bpp, rotate)

    def write(self, image: Image.Image) -> None:
        frame = Image.new("RGB", (self.width, self.height), "black")
        canvas = image.convert("RGB").resize((CANVAS_W, CANVAS_H), resample_bilinear())
        if self.rotate:
            canvas = canvas.rotate(self.rotate, expand=True)
        canvas = canvas.resize((self.width, self.height), resample_bilinear())
        frame.paste(canvas, (0, 0))
        payload = self._rgb565(frame) if self.bpp == 16 else frame.convert("RGBA").tobytes("raw", "BGRA")
        with Path(self.path).open("r+b", buffering=0) as handle:
            handle.seek(0)
            handle.write(payload)

    def _rgb565(self, image: Image.Image) -> bytes:
        out = bytearray()
        for r, g, b in image.convert("RGB").getdata():
            out.extend(struct.pack("<H", ((r & 0xF8) << 8) | ((g & 0xFC) << 3) | (b >> 3)))
        return bytes(out)


class SysfsGpio:
    def __init__(self, number: int) -> None:
        self.number = number
        self.base = Path(f"/sys/class/gpio/gpio{number}")
        self._ensure_exported()

    def _ensure_exported(self) -> None:
        if not self.base.exists():
            Path("/sys/class/gpio/export").write_text(str(self.number), encoding="ascii")
            deadline = time.monotonic() + 1.0
            while not self.base.exists() and time.monotonic() < deadline:
                time.sleep(0.02)
        direction = self.base / "direction"
        if direction.exists():
            direction.write_text("out", encoding="ascii")

    def set(self, value: bool) -> None:
        (self.base / "value").write_text("1" if value else "0", encoding="ascii")


class RawSpi:
    SPI_IOC_WR_MODE = 0x40016B01
    SPI_IOC_WR_BITS_PER_WORD = 0x40016B03
    SPI_IOC_WR_MAX_SPEED_HZ = 0x40046B04

    def __init__(self, path: str, speed_hz: int) -> None:
        import fcntl

        self.fd = os.open(path, os.O_RDWR)
        fcntl.ioctl(self.fd, self.SPI_IOC_WR_MODE, struct.pack("B", 0))
        fcntl.ioctl(self.fd, self.SPI_IOC_WR_BITS_PER_WORD, struct.pack("B", 8))
        fcntl.ioctl(self.fd, self.SPI_IOC_WR_MAX_SPEED_HZ, struct.pack("I", speed_hz))

    def write(self, data: bytes) -> None:
        view = memoryview(data)
        while view:
            written = os.write(self.fd, view[:4096])
            view = view[written:]


class Ili9488Display:
    def __init__(
        self,
        spidev: str,
        dc_gpio: int,
        reset_gpio: Optional[int],
        bl_gpio: Optional[int],
        speed_hz: int,
        rotate: int,
        color_order: str,
        pixel_format: int,
        invert: bool,
    ) -> None:
        self.spi = RawSpi(spidev, speed_hz)
        self.dc = SysfsGpio(dc_gpio)
        self.reset = SysfsGpio(reset_gpio) if reset_gpio is not None else None
        self.bl = SysfsGpio(bl_gpio) if bl_gpio is not None else None
        self.rotate = rotate
        self.color_order = color_order
        self.pixel_format = pixel_format
        self.invert = invert
        self.width = CANVAS_W
        self.height = CANVAS_H
        self._init_panel()

    def _init_panel(self) -> None:
        if self.reset:
            self.reset.set(True)
            time.sleep(0.05)
            self.reset.set(False)
            time.sleep(0.08)
            self.reset.set(True)
            time.sleep(0.12)
        if self.bl:
            self.bl.set(True)
        self.command(0x01)
        time.sleep(0.12)
        self.command(0x11)
        time.sleep(0.12)
        self.command(0x3A, bytes([0x55 if self.pixel_format == 16 else 0x66]))
        self.command(0x36, bytes([self._madctl(self.rotate)]))
        self.command(0x21 if self.invert else 0x20)
        self.command(0x29)
        time.sleep(0.05)

    def _madctl(self, rotate: int) -> int:
        values = {
            0: 0x40,
            90: 0x20,
            180: 0x80,
            270: 0xE0,
        }
        value = values.get(rotate, 0x20)
        if self.color_order == "bgr":
            value |= 0x08
        return value

    def command(self, command: int, data: bytes = b"") -> None:
        self.dc.set(False)
        self.spi.write(bytes([command]))
        if data:
            self.dc.set(True)
            self.spi.write(data)

    def write(self, image: Image.Image) -> None:
        frame = image.convert("RGB").resize((self.width, self.height), Image.Resampling.BILINEAR)
        self._set_window(0, 0, self.width - 1, self.height - 1)
        self.dc.set(True)
        self.spi.write(self._pixels(frame))

    def _pixels(self, image: Image.Image) -> bytes:
        if self.pixel_format == 16:
            out = bytearray()
            for r, g, b in image.getdata():
                if self.color_order == "bgr":
                    r, b = b, r
                value = ((r & 0xF8) << 8) | ((g & 0xFC) << 3) | (b >> 3)
                out.extend(struct.pack(">H", value))
            return bytes(out)
        if self.color_order == "bgr":
            data = bytearray()
            for r, g, b in image.getdata():
                data.extend((b, g, r))
            return bytes(data)
        return image.tobytes()

    def _set_window(self, x0: int, y0: int, x1: int, y1: int) -> None:
        self.command(0x2A, struct.pack(">HH", x0, x1))
        self.command(0x2B, struct.pack(">HH", y0, y1))
        self.command(0x2C)


class AssetRenderer:
    def __init__(self, assets_dir: Path) -> None:
        self.assets_dir = assets_dir
        self.assets = self._load_assets()
        self.font_tiny = load_font(13)
        self.font_small = load_font(16)
        self.font_mid = load_font(20)
        self.font_big = load_font(25, bold=True)

    def _load_assets(self) -> dict[str, Image.Image]:
        loaded: dict[str, Image.Image] = {}
        if not self.assets_dir.exists():
            return loaded
        files = [path for path in self.assets_dir.glob("*.png") if not path.name.startswith("_")]
        for key, pattern in ASSET_PATTERNS.items():
            candidates = [path for path in files if pattern in path.name]
            if not candidates:
                continue
            path = self._best_candidate(candidates)
            try:
                loaded[key] = Image.open(path).convert("RGBA")
            except Exception:
                continue
        return loaded

    def _best_candidate(self, candidates: list[Path]) -> Path:
        for scale in ("@1x", "@2x", "@3x"):
            for path in candidates:
                if scale in path.name:
                    return path
        return candidates[0]

    def render_boot(self, status: str, detail: str = "") -> Image.Image:
        if status == "ok":
            return self._asset_or_status("connected", TEXT["connected_title"], detail or TEXT["connected_subtitle"], TEXT["connected"], (35, 196, 123))
        elif status == "fail":
            return self._asset_or_status("connection_failed", TEXT["failed_title"], detail or TEXT["failed_subtitle"], TEXT["connection_failed"], (245, 92, 92))
        else:
            return self._asset_or_status("connecting", TEXT["connecting_title"], detail or TEXT["connecting_subtitle"], TEXT["connecting"], (70, 143, 255))

    def render(self, state: dict[str, Any], error: str = "") -> Image.Image:
        display = state.get("display") if isinstance(state.get("display"), dict) else {}
        screen = str(display.get("screen", "wait_scan"))
        if error and not state:
            image = self.render_boot("checking", TEXT["service_connecting"])
            return self._with_popup(image, display.get("popup"))
        if screen == "select_item":
            image = self._select(display)
        elif screen == "inputting":
            image = self._inputting(display)
        elif screen == "upload_done":
            image = self._asset_or_status("upload_done", TEXT["input_done"], TEXT["ready_scan"], TEXT["input_done"], (35, 196, 123))
        elif screen == "exam_mismatch":
            image = self._exam_mismatch(display)
        elif screen in ("not_found", "query_not_found"):
            image = self._asset_or_status("not_found", TEXT["no_order_title"], TEXT["no_order_subtitle"], TEXT["not_found"], (245, 92, 92))
        elif screen in ("querying", "api_querying"):
            image = self._status_page(TEXT["querying_title"], TEXT["querying_subtitle"], TEXT["querying_order"], (70, 143, 255))
        elif screen in ("wait_report", "report_waiting"):
            title = str(display.get("title", "") or TEXT["checking"])
            message = str(display.get("message", "") or TEXT["checking_subtitle"])
            image = self._status_page(title, message, TEXT["wait_report"], (70, 143, 255))
        elif screen in ("printer_error", "gadget_error"):
            image = self._asset_or_status("printer_error", TEXT["printer_error"], TEXT["failed_subtitle"], TEXT["connection_failed"], (245, 92, 92))
        else:
            image = self._asset_or_status("wait_scan", TEXT["scan_prompt"], TEXT["scan_subtitle"], TEXT["wait_scan"], (35, 196, 123))
        return self._with_popup(image, display.get("popup"))

    def _with_popup(self, image: Image.Image, popup: Any) -> Image.Image:
        if not isinstance(popup, dict):
            return image
        title = str(popup.get("title", "") or TEXT["file_received"])
        message = str(popup.get("message", "") or "")
        rgba = image.convert("RGBA")
        draw = ImageDraw.Draw(rgba, "RGBA")
        draw.rectangle((0, 0, CANVAS_W, CANVAS_H), fill=(0, 0, 0, 96))
        accent = (35, 196, 123)
        x0 = CARD_X + 46
        y0 = CARD_Y + 28
        x1 = CARD_X + CARD_W - 46
        y1 = CARD_Y + CARD_H - 28
        rounded_rectangle(draw, (x0, y0, x1, y1), radius=18, fill=(255, 255, 255, 248), outline=(*accent, 210), width=2)
        draw.rectangle((x0, y0 + 31, x0 + 6, y1 - 20), fill=(*accent, 255))
        self._center_text(draw, self._clip(draw, title, self.font_mid, x1 - x0 - 32), y0 + 17, self.font_mid, (22, 34, 46, 255))
        if message:
            message = self._clip(draw, message, self.font_small, x1 - x0 - 36)
        else:
            message = "\u6b63\u5728\u8f6c\u6362\u5e76\u6253\u5370"
        self._center_text(draw, message, y0 + 50, self.font_small, (73, 88, 105, 255))
        return rgba.convert("RGB")

    def _asset_or_status(
        self,
        key: str,
        title: str,
        subtitle: str,
        tag: str,
        accent: tuple[int, int, int],
    ) -> Image.Image:
        if key in self.assets:
            return self._asset_canvas(key)
        return self._status_page(title, subtitle, tag, accent)

    def _asset_canvas(self, key: str) -> Image.Image:
        asset = self.assets.get(key)
        if asset is None:
            return self._background().convert("RGB")
        image = self._background()
        fitted, x, y = self._fit_asset(asset)
        image.alpha_composite(fitted, (x, y))
        return image.convert("RGB")

    def _fit_asset(self, asset: Image.Image) -> tuple[Image.Image, int, int]:
        scale = min(CARD_W / asset.width, CARD_H / asset.height)
        w = max(1, int(asset.width * scale))
        h = max(1, int(asset.height * scale))
        fitted = asset if (w, h) == asset.size else asset.resize((w, h), resample_lanczos())
        return fitted, CARD_X + (CARD_W - w) // 2, CARD_Y + (CARD_H - h) // 2

    def _background(self) -> Image.Image:
        return Image.new("RGBA", (CANVAS_W, CANVAS_H), "#000000")

    def _paste_card(self, card: Image.Image) -> Image.Image:
        image = self._background()
        if card.mode != "RGBA":
            card = card.convert("RGBA")
        if card.size != (CARD_W, CARD_H):
            card = card.resize((CARD_W, CARD_H), resample_lanczos())
        image.alpha_composite(card, (CARD_X, CARD_Y))
        return image.convert("RGB")

    def _card_base(self, accent: tuple[int, int, int], show_robot: bool = True) -> tuple[Image.Image, ImageDraw.ImageDraw]:
        card = Image.new("RGBA", (DESIGN_W, DESIGN_H), (0, 0, 0, 0))
        draw = ImageDraw.Draw(card, "RGBA")
        rounded_rectangle(draw, (0, 0, DESIGN_W - 1, DESIGN_H - 1), radius=24, fill=(26, 36, 47, 255))
        rounded_rectangle(draw, (9, 9, DESIGN_W - 10, DESIGN_H - 10), radius=18, fill=(220, 237, 249, 255))
        rounded_rectangle(draw, (16, 17, DESIGN_W - 17, DESIGN_H - 18), radius=13, outline=(189, 214, 237, 150), width=1)
        if show_robot:
            rounded_rectangle(draw, (263, 15, DESIGN_W - 20, DESIGN_H - 16), radius=16, fill=(232, 244, 253, 255), outline=(183, 209, 235, 255), width=1)
            self._draw_robot(draw, 305, 72, accent)
        return card, draw

    def _draw_robot(self, draw: ImageDraw.ImageDraw, cx: int, cy: int, accent: tuple[int, int, int]) -> None:
        line = (*accent, 255)
        soft = (*accent, 42)
        draw.ellipse((cx - 34, cy - 34, cx + 34, cy + 34), fill=soft)
        rounded_rectangle(draw, (cx - 25, cy - 15, cx + 25, cy + 19), radius=9, outline=line, width=4)
        draw.line((cx - 15, cy - 18, cx - 25, cy - 31), fill=line, width=3)
        draw.line((cx + 15, cy - 18, cx + 25, cy - 31), fill=line, width=3)
        draw.ellipse((cx - 29, cy - 35, cx - 22, cy - 28), fill=line)
        draw.ellipse((cx + 22, cy - 35, cx + 29, cy - 28), fill=line)
        draw.ellipse((cx - 12, cy - 3, cx - 5, cy + 4), fill=line)
        draw.ellipse((cx + 5, cy - 3, cx + 12, cy + 4), fill=line)
        draw.arc((cx - 10, cy + 4, cx + 10, cy + 16), 0, 180, fill=line, width=2)

    def _status_page(self, title: str, subtitle: str, tag: str, accent: tuple[int, int, int]) -> Image.Image:
        card, draw = self._card_base(accent)
        title_font = self.font_big if text_length(draw, title, self.font_big) <= 220 else self.font_mid
        draw.text((25, 27), self._clip(draw, title, title_font, 218), font=title_font, fill=(22, 34, 46, 255))
        draw.text((25, 64), self._clip(draw, subtitle, self.font_small, 218), font=self.font_small, fill=(73, 88, 105, 255))
        badge_w = min(max(int(text_length(draw, tag, self.font_tiny)) + 24, 74), 174)
        rounded_rectangle(draw, (25, 99, 25 + badge_w, 121), radius=11, fill=(*accent, 235))
        draw.text((37, 102), self._clip(draw, tag, self.font_tiny, badge_w - 24), font=self.font_tiny, fill=(255, 255, 255, 255))
        return self._paste_card(card)

    def _select(self, display: dict[str, Any]) -> Image.Image:
        accent = (70, 143, 255)
        card, draw = self._card_base(accent, show_robot=False)
        items = display.get("items") if isinstance(display.get("items"), list) else []
        selected = int(display.get("selected_index", 0) or 0)
        scan = str(display.get("scan", "") or "")
        heading = f"{TEXT['select_item']}  {scan.upper()}" if scan else TEXT["select_item"]
        draw.text((24, 18), self._clip(draw, heading, self.font_small, 292), font=self.font_small, fill=(33, 48, 64, 255))

        y = 42
        visible_items = []
        if items:
            selected = selected % len(items)
            visible_items = [
                items[(selected + offset) % len(items)]
                for offset in range(min(3, len(items)))
            ]
        if not visible_items:
            rounded_rectangle(draw, (26, 54, 331, 88), radius=10, fill=(245, 250, 255, 255), outline=(213, 226, 238, 255), width=1)
            draw.text((42, 61), TEXT["no_selectable_item"], font=self.font_small, fill=(86, 104, 122, 255))
        for index, item in enumerate(visible_items):
            if not isinstance(item, dict):
                continue
            active = index == 0
            row_fill = (*accent, 255) if active else (244, 248, 252, 255)
            text_fill = (255, 255, 255, 255) if active else (35, 50, 65, 255)
            outline = (*accent, 255) if active else (218, 228, 238, 255)
            rounded_rectangle(draw, (26, y, 331, y + 20), radius=8, fill=row_fill, outline=outline, width=1)
            text = str(item.get("exam_item", "") or item.get("title", "") or TEXT["unnamed_item"])
            prefix = ">" if active else " "
            draw.text((39, y + 2), self._clip(draw, f"{prefix} {text}", self.font_tiny, 274), font=self.font_tiny, fill=text_fill)
            y += 24
        rounded_rectangle(draw, (25, 114, 203, 130), radius=8, fill=(230, 239, 248, 255))
        draw.text((34, 115), self._clip(draw, TEXT["select_hint"], self.font_tiny, 150), font=self.font_tiny, fill=(83, 99, 116, 255))
        if len(items) > 3:
            rounded_rectangle(draw, (336, 43, 342, 108), radius=3, fill=(204, 219, 233, 255))
            knob_h = max(14, int(65 * min(3, len(items)) / len(items)))
            knob_y = 43 + int((65 - knob_h) * selected / max(len(items) - 1, 1))
            rounded_rectangle(draw, (336, knob_y, 342, knob_y + knob_h), radius=3, fill=(*accent, 255))
        return self._paste_card(card)

    def _inputting(self, display: dict[str, Any]) -> Image.Image:
        exam_item = str(display.get("exam_item", "") or "")
        lines = []
        if exam_item:
            lines.append(f"{TEXT['current_input']}: {exam_item}")
        return self._detail_status_page(TEXT["checking"], TEXT["auto_input"], TEXT["inputting"], (255, 183, 77), lines)

    def _exam_mismatch(self, display: dict[str, Any]) -> Image.Image:
        device_type = str(display.get("device_type", "") or "")
        patient_items = display.get("patient_exam_items") if isinstance(display.get("patient_exam_items"), list) else []
        lines = []
        if device_type:
            lines.append(f"{TEXT['device_type']}: {device_type}")
        if patient_items:
            lines.append(f"{TEXT['patient_items']}: {', '.join(str(item) for item in patient_items if item)}")
        return self._detail_status_page(
            TEXT["exam_mismatch_title"],
            TEXT["exam_mismatch_subtitle"],
            TEXT["exam_mismatch"],
            (245, 92, 92),
            lines,
        )

    def _detail_status_page(
        self,
        title: str,
        subtitle: str,
        tag: str,
        accent: tuple[int, int, int],
        lines: list[str],
    ) -> Image.Image:
        card, draw = self._card_base(accent)
        title_font = self.font_mid if text_length(draw, title, self.font_big) > 218 else self.font_big
        draw.text((25, 22), self._clip(draw, title, title_font, 218), font=title_font, fill=(22, 34, 46, 255))
        draw.text((25, 55), self._clip(draw, subtitle, self.font_small, 218), font=self.font_small, fill=(73, 88, 105, 255))
        y = 82
        for line in lines[:2]:
            clipped = self._clip(draw, str(line), self.font_tiny, 218)
            draw.text((25, y), clipped, font=self.font_tiny, fill=(35, 50, 65, 255))
            y += 20
        badge_w = min(max(int(text_length(draw, tag, self.font_tiny)) + 24, 74), 174)
        rounded_rectangle(draw, (25, 111, 25 + badge_w, 128), radius=8, fill=(*accent, 235))
        draw.text((37, 112), self._clip(draw, tag, self.font_tiny, badge_w - 24), font=self.font_tiny, fill=(255, 255, 255, 255))
        return self._paste_card(card)

    def _draw_center_overlay(self, image: Image.Image, title: str, subtitle: str) -> None:
        rgba = image.convert("RGBA")
        draw = ImageDraw.Draw(rgba, "RGBA")
        rounded_rectangle(draw, (42, 98, 438, 220), radius=24, fill=(255, 255, 255, 238), outline=(204, 222, 238, 255), width=1)
        self._center_text(draw, title, 126, self.font_big, (30, 46, 63, 255))
        self._center_text(draw, subtitle, 166, self.font_mid, (71, 91, 111, 255))
        image.paste(rgba.convert("RGB"))

    def _draw_footer(self, image: Image.Image, text: str) -> None:
        rgba = image.convert("RGBA")
        draw = ImageDraw.Draw(rgba, "RGBA")
        y = CANVAS_H - 44
        rounded_rectangle(draw, (70, y, 410, y + 30), radius=13, fill=(255, 255, 255, 220))
        self._center_text(draw, text, y + 5, self.font_small, (61, 79, 97, 255))
        image.paste(rgba.convert("RGB"))

    def _center_text(self, draw: ImageDraw.ImageDraw, text: str, y: int, font: ImageFont.ImageFont, fill: tuple[int, int, int, int]) -> None:
        bbox = text_bbox(draw, text, font)
        x = max((CANVAS_W - (bbox[2] - bbox[0])) // 2, 0)
        draw.text((x, y), text, font=font, fill=fill)

    def _clip(self, draw: ImageDraw.ImageDraw, text: str, font: ImageFont.ImageFont, max_width: int) -> str:
        if text_length(draw, text, font) <= max_width:
            return text
        out = text
        while out and text_length(draw, out + "...", font) > max_width:
            out = out[:-1]
        return out + "..." if out else "..."


def hide_console() -> None:
    for tty in ("/dev/tty0", "/dev/tty1"):
        try:
            with Path(tty).open("w", encoding="ascii", errors="ignore") as handle:
                handle.write("\033[2J\033[H\033[?25l")
        except Exception:
            pass


def quiet_kernel_console() -> None:
    subprocess.run(["dmesg", "-n", "1"], stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL, check=False)


def network_ready() -> bool:
    route = Path("/proc/net/route")
    try:
        for line in route.read_text(encoding="ascii", errors="ignore").splitlines()[1:]:
            parts = line.split()
            if len(parts) > 2 and parts[1] == "00000000" and int(parts[3], 16) & 0x2:
                return True
    except Exception:
        pass
    return False


def gadget_ready() -> bool:
    if Path("/dev/g_printer0").exists():
        return True
    udc = Path("/sys/kernel/config/usb_gadget/rockchip/UDC")
    try:
        return bool(udc.read_text(encoding="ascii", errors="ignore").strip())
    except Exception:
        return False


def api_ready(url: str) -> bool:
    try:
        read_state(url)
        return True
    except Exception:
        return False


def render_state_key(state: dict[str, Any], error: str) -> str:
    display = state.get("display") if isinstance(state.get("display"), dict) else {}
    stable_display = dict(display)
    stable_display.pop("updated_at", None)
    return json.dumps(stable_display, ensure_ascii=False, sort_keys=True, default=str) + "\n" + error


def system_uptime_seconds() -> float:
    try:
        return float(Path("/proc/uptime").read_text(encoding="ascii", errors="ignore").split()[0])
    except Exception:
        return 0.0


def run_boot_check(display: Any, renderer: AssetRenderer, url: str, timeout: float) -> None:
    deadline = time.monotonic() + timeout
    last_detail = TEXT["self_check_starting"]
    while time.monotonic() < deadline:
        checks = {
            TEXT["network"]: network_ready(),
            "Gadget": gadget_ready(),
            TEXT["service"]: api_ready(url),
        }
        bad = [name for name, ok in checks.items() if not ok]
        if not bad:
            display.write(renderer.render_boot("ok", TEXT["service_started"]))
            time.sleep(1.6)
            return
        last_detail = TEXT["waiting"] + " " + " / ".join(bad)
        display.write(renderer.render_boot("checking", last_detail))
        time.sleep(0.8)
    display.write(renderer.render_boot("fail", last_detail))
    time.sleep(1.8)


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--output", choices=("fb", "ili9488"), default="fb")
    parser.add_argument("--fb", default="/dev/fb0")
    parser.add_argument("--fb-rotate", type=int, choices=(0, 90, 180, 270), default=0)
    parser.add_argument("--spidev", default="/dev/spidev1.0")
    parser.add_argument("--dc-gpio", type=int, default=139)
    parser.add_argument("--reset-gpio", type=int, default=142)
    parser.add_argument("--bl-gpio", type=int, default=-1)
    parser.add_argument("--spi-speed", type=int, default=16000000)
    parser.add_argument("--rotate", type=int, choices=(0, 90, 180, 270), default=90)
    parser.add_argument("--color-order", choices=("rgb", "bgr"), default="rgb")
    parser.add_argument("--pixel-format", type=int, choices=(16, 18), default=18)
    parser.add_argument("--invert", choices=("on", "off"), default="off")
    parser.add_argument("--url", default="http://127.0.0.1:8080/display/state")
    parser.add_argument("--interval", type=float, default=0.5)
    parser.add_argument("--width", type=int, default=CANVAS_W)
    parser.add_argument("--height", type=int, default=CANVAS_H)
    parser.add_argument("--bpp", type=int, default=32)
    parser.add_argument("--assets-dir", default="/opt/rk3568_gateway/" + TEXT["assets_dir"])
    parser.add_argument("--boot-check-timeout", type=float, default=18)
    parser.add_argument("--boot-animation-max-uptime", type=float, default=240)
    parser.add_argument("--max-static-refresh-seconds", type=float, default=30)
    args = parser.parse_args()

    quiet_kernel_console()
    hide_console()
    if args.output == "ili9488":
        display = Ili9488Display(
            spidev=args.spidev,
            dc_gpio=args.dc_gpio,
            reset_gpio=args.reset_gpio if args.reset_gpio >= 0 else None,
            bl_gpio=args.bl_gpio if args.bl_gpio >= 0 else None,
            speed_hz=args.spi_speed,
            rotate=args.rotate,
            color_order=args.color_order,
            pixel_format=args.pixel_format,
            invert=args.invert == "on",
        )
    else:
        display = Framebuffer.open(args.fb, args.width, args.height, args.bpp, args.fb_rotate)
    renderer = AssetRenderer(Path(args.assets_dir))

    if args.boot_check_timeout > 0 and system_uptime_seconds() <= args.boot_animation_max_uptime:
        run_boot_check(display, renderer, args.url, args.boot_check_timeout)

    last_state: dict[str, Any] = {}
    last_render_key = ""
    last_frame_key: bytes | None = None
    last_write_at = 0.0
    while True:
        error = ""
        try:
            last_state = read_state(args.url)
        except Exception as exc:
            error = str(exc) if not last_state else ""
        render_key = render_state_key(last_state, error)
        now = time.monotonic()
        force_refresh = args.max_static_refresh_seconds > 0 and now - last_write_at >= args.max_static_refresh_seconds
        if render_key == last_render_key and not force_refresh:
            time.sleep(args.interval)
            continue
        try:
            image = renderer.render(last_state, error)
        except Exception:
            traceback.print_exc()
            time.sleep(args.interval)
            continue
        frame_key = image.tobytes()
        if frame_key != last_frame_key or force_refresh:
            display.write(image)
            last_frame_key = frame_key
            last_write_at = now
        last_render_key = render_key
        time.sleep(args.interval)


if __name__ == "__main__":
    raise SystemExit(main())
