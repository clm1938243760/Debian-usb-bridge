from __future__ import annotations

import asyncio
import base64
import json
import logging
import subprocess
import time
import urllib.error
import urllib.request
from pathlib import Path
from typing import Any

from .compat import to_thread, unlink_missing_ok


LOGGER = logging.getLogger(__name__)
DEFAULT_ICON_ENDPOINT = "http://192.168.20.163:5002/icon/locate"
DEFAULT_WINDOW_ENDPOINT = "http://192.168.20.163:5002/window/detect"
YES_BUTTON_TEXTS = ("是(Y)", "是（Y）")
CONFIRM_BUTTON_TEXTS = ("确认", "确定")
PDF_REPORT_PROMPT_TEXT = "是否生成PDF报告"
START_CHECK_TEXT = "开始检查"
CHECK_DONE_TEXT = "检查完成"
ANALYSIS_TEXT = "数据分析"
ANALYSIS_NO_RESPONSE_WAIT_SECONDS = 1.5
LOGIN_TEXT = "登录"
NEW_PATIENT_TEXT = "新建患者"
UNSELECTED_PATIENT_TEXT = "未选择患者"
READY_TEXT = "就绪"
PATIENT_ID_TEXT = "患者号"
REPORT_GENERATED_TEXT = "检查报告已生成"
LINEAR_POLL_SECONDS = 0.5


def capture_frame_pattern(output: Path) -> Path:
    return output.with_name(f".{output.stem}_%02d{output.suffix}")


def build_capture_command(
    device: str,
    output: Path,
    *,
    width: int,
    height: int,
    framerate: int,
    frames: int,
    io_mode: int,
    capture_format: str,
) -> list[str]:
    fmt = capture_format.lower()
    cmd = [
        "gst-launch-1.0",
        "-q",
        "-e",
        "v4l2src",
        f"device={device}",
        f"io-mode={io_mode}",
        f"num-buffers={frames}",
        "!",
    ]
    if fmt in {"mjpg", "mjpeg", "jpeg"}:
        cmd.extend(
            [
                f"image/jpeg,width={width},height={height},framerate={framerate}/1",
                "!",
                "multifilesink",
                f"location={capture_frame_pattern(output).as_posix()}",
            ]
        )
    elif fmt in {"yuyv", "yuy2"}:
        cmd.extend(
            [
                f"video/x-raw,format=YUY2,width={width},height={height},framerate={framerate}/1",
                "!",
                "videoconvert",
                "!",
                "jpegenc",
                "quality=90",
                "!",
                "multifilesink",
                f"location={capture_frame_pattern(output).as_posix()}",
            ]
        )
    elif fmt == "bgr":
        cmd.extend(
            [
                f"video/x-raw,format=BGR,width={width},height={height},framerate={framerate}/1",
                "!",
                "videoconvert",
                "!",
                "jpegenc",
                "quality=90",
                "!",
                "multifilesink",
                f"location={capture_frame_pattern(output).as_posix()}",
            ]
        )
    else:
        raise ValueError(f"unsupported vision capture_format: {capture_format}")
    return cmd


def capture_jpeg(
    device: str,
    output: Path,
    timeout: float,
    *,
    width: int = 1920,
    height: int = 1080,
    framerate: int = 30,
    frames: int = 30,
    io_mode: int = 2,
    capture_format: str = "mjpg",
) -> None:
    output.parent.mkdir(parents=True, exist_ok=True)
    for frame in output.parent.glob(f".{output.stem}_*{output.suffix}"):
        unlink_missing_ok(frame)
    unlink_missing_ok(output)
    cmd = build_capture_command(
        device,
        output,
        width=width,
        height=height,
        framerate=framerate,
        frames=frames,
        io_mode=io_mode,
        capture_format=capture_format,
    )
    subprocess.run(cmd, check=True, timeout=timeout)
    selected = output.with_name(f".{output.stem}_{max(frames - 1, 0):02d}{output.suffix}")
    if not selected.exists():
        frames_found = sorted(output.parent.glob(f".{output.stem}_*{output.suffix}"))
        if frames_found:
            selected = frames_found[-1]
    if selected.exists():
        selected.replace(output)
    for frame in output.parent.glob(f".{output.stem}_*{output.suffix}"):
        unlink_missing_ok(frame)
    if not output.exists() or output.stat().st_size == 0:
        raise RuntimeError(f"capture failed or empty image: {output}")


def build_image_body(image_base64: str, extra: dict[str, Any] | None = None) -> bytes:
    payload: dict[str, Any] = {"image_base64": image_base64}
    if extra:
        payload.update(extra)
    return json.dumps(payload, ensure_ascii=False).encode("utf-8")


def post_image(
    endpoint: str,
    image_path: Path,
    timeout: float,
    extra: dict[str, Any] | None = None,
) -> dict[str, Any]:
    image_base64 = base64.b64encode(image_path.read_bytes()).decode("ascii")
    request = urllib.request.Request(
        endpoint,
        data=build_image_body(image_base64, extra),
        headers={"Content-Type": "application/json"},
        method="POST",
    )
    try:
        with urllib.request.urlopen(request, timeout=timeout) as response:
            raw = response.read().decode("utf-8", errors="replace")
    except urllib.error.HTTPError as exc:
        error_body = exc.read().decode("utf-8", errors="replace")
        raise RuntimeError(f"{endpoint} HTTP {exc.code}: {error_body}") from exc
    except urllib.error.URLError as exc:
        raise RuntimeError(f"{endpoint} request failed: {exc}") from exc

    try:
        parsed = json.loads(raw)
    except json.JSONDecodeError as exc:
        raise RuntimeError(f"{endpoint} returned non-json: {raw[:500]}") from exc
    if not isinstance(parsed, dict):
        raise RuntimeError(f"{endpoint} returned non-object json: {parsed!r}")
    return parsed


def extract_center(response: dict[str, Any], key: str) -> tuple[int, int] | None:
    center = response.get(key)
    if center is None and key == "center" and isinstance(response.get("box"), list):
        box = response["box"]
        if len(box) == 4 and all(isinstance(v, (int, float)) for v in box):
            center = [(box[0] + box[2]) / 2, (box[1] + box[3]) / 2]
    if not isinstance(center, list) or len(center) != 2:
        return None
    x, y = center
    if not isinstance(x, (int, float)) or not isinstance(y, (int, float)):
        return None
    return int(round(x)), int(round(y))


def response_windows(response: dict[str, Any]) -> list[dict[str, Any]]:
    windows = response.get("windows")
    if isinstance(windows, list):
        parsed = [item for item in windows if isinstance(item, dict)]
        if parsed:
            return parsed
    return [response]


def find_ocr_center(response: dict[str, Any], text: str) -> tuple[int, int] | None:
    for item in ocr_items(response):
        if str(item.get("text", "")).strip() != text:
            continue
        center = extract_center(item, "center")
        if center is not None:
            return center
    return None


def ocr_items(response: dict[str, Any]) -> list[dict[str, Any]]:
    items = []
    for window in response_windows(response):
        ocr = window.get("ocr")
        if not isinstance(ocr, list):
            continue
        for item in ocr:
            if isinstance(item, dict):
                items.append(item)
    return items


def ocr_texts(response: dict[str, Any]) -> list[str]:
    texts = []
    for item in ocr_items(response):
        texts.append(str(item.get("text", "")).strip())
    return texts


def compact_ocr_text(text: str) -> str:
    return "".join(str(text).split())


def ocr_contains(response: dict[str, Any], text: str) -> bool:
    compact_text = compact_ocr_text(text)
    return any(text in item or compact_text in compact_ocr_text(item) for item in ocr_texts(response))


def is_loading(response: dict[str, Any]) -> bool:
    loading_words = ("加载", "正在加载", "请稍候", "请稍等", "处理中", "等待")
    return any(any(word in item for word in loading_words) for item in ocr_texts(response))


def label_value(response: dict[str, Any]) -> str | None:
    label = response.get("label")
    if label is None:
        return None
    label = str(label).strip()
    if not label or label.lower() == "none":
        return None
    return label


def labeled_windows(response: dict[str, Any], *labels: str) -> list[dict[str, Any]]:
    wanted = set(labels)
    return [window for window in response_windows(response) if label_value(window) in wanted]


def has_label(response: dict[str, Any], label: str) -> bool:
    return bool(labeled_windows(response, label))


def has_no_detected_window(response: dict[str, Any]) -> bool:
    return not any(label_value(window) for window in response_windows(response))


def is_login_window(response: dict[str, Any]) -> bool:
    return has_label(response, "0") and ocr_contains(response, LOGIN_TEXT)


def is_new_patient_window(response: dict[str, Any]) -> bool:
    return has_label(response, "2")


def is_ready_to_create_patient(response: dict[str, Any]) -> bool:
    return (
        has_label(response, "1")
        and ocr_contains(response, UNSELECTED_PATIENT_TEXT)
        and ocr_contains(response, READY_TEXT)
        and find_ocr_center(response, NEW_PATIENT_TEXT) is not None
    )


def is_ready_to_start_check(response: dict[str, Any]) -> bool:
    return (
        has_label(response, "1")
        and ocr_contains(response, PATIENT_ID_TEXT)
        and ocr_contains(response, READY_TEXT)
        and find_ocr_center(response, START_CHECK_TEXT) is not None
    )


def is_check_complete(response: dict[str, Any]) -> bool:
    return (
        ocr_contains(response, CHECK_DONE_TEXT)
        and find_ocr_center(response, ANALYSIS_TEXT) is not None
    )


def is_pdf_report_prompt(response: dict[str, Any]) -> bool:
    return has_label(response, "4") and ocr_contains(response, PDF_REPORT_PROMPT_TEXT)


def confirm_button_target(response: dict[str, Any]) -> str | None:
    return next((text for text in CONFIRM_BUTTON_TEXTS if find_ocr_center(response, text) is not None), None)


def is_report_generated(response: dict[str, Any]) -> bool:
    return ocr_contains(response, REPORT_GENERATED_TEXT) and confirm_button_target(response) is not None


def pdf_report_yes_target(response: dict[str, Any]) -> str | None:
    if not ocr_contains(response, PDF_REPORT_PROMPT_TEXT):
        return None
    best_text = None
    best_y = None
    for item in ocr_items(response):
        text = str(item.get("text", "")).strip()
        center = extract_center(item, "center")
        if "是" not in text or center is None:
            continue
        if best_y is None or center[1] > best_y:
            best_text = text
            best_y = center[1]
    return best_text


def dialog_button_target(response: dict[str, Any], *labels: str) -> str | None:
    for window in labeled_windows(response, *labels):
        if label_value(window) == "4":
            pdf_target = pdf_report_yes_target(window)
            if pdf_target:
                return pdf_target
        for text in YES_BUTTON_TEXTS + CONFIRM_BUTTON_TEXTS:
            if find_ocr_center(window, text) is not None:
                return text
    return None


def decide_action(response: dict[str, Any]) -> tuple[str, str | None]:
    label4_target = dialog_button_target(response, "4")
    if label4_target:
        return "click_text", label4_target
    if labeled_windows(response, "4"):
        return "wait", None
    if labeled_windows(response, "5"):
        return "click_text", dialog_button_target(response, "5") or "确定"
    if labeled_windows(response, "2"):
        return "form_input", None
    if labeled_windows(response, "0"):
        return "click_text", LOGIN_TEXT

    if ocr_contains(response, UNSELECTED_PATIENT_TEXT):
        return "click_text", NEW_PATIENT_TEXT
    if ocr_contains(response, CHECK_DONE_TEXT) and find_ocr_center(response, ANALYSIS_TEXT) is not None:
        return "analysis", None
    if find_ocr_center(response, START_CHECK_TEXT) is not None:
        return "click_text", START_CHECK_TEXT

    if not any(label_value(window) for window in response_windows(response)):
        if is_loading(response):
            return "wait", None
        return "open", None

    for window in labeled_windows(response, "1"):
        if ocr_contains(window, "就绪"):
            return "wait", None
        return "wait", None
    return "wait", None


def decide_after_analysis(response: dict[str, Any]) -> tuple[str, str | None]:
    label4_target = dialog_button_target(response, "4")
    if label4_target:
        return "click_text", label4_target
    if labeled_windows(response, "4"):
        return "wait", None
    if labeled_windows(response, "5"):
        return "click_text", dialog_button_target(response, "5") or "确定"
    if ocr_contains(response, CHECK_DONE_TEXT):
        return "finish", None
    return "wait", None


class VisionFlow:
    def __init__(self, config: Any, hid_output: Any) -> None:
        self.config = config
        self.hid_output = hid_output
        self.workdir = Path(config.workdir)
        self.capture_index = 0

    async def run_until_form_done(self, task: dict[str, Any]) -> str:
        if not self.config.enabled:
            await self.hid_output.execute_form(task)
            return "form_done"

        started_at = time.monotonic()
        await self.prepare_new_patient_window(started_at)
        LOGGER.info("vision label=2; start hid form input")
        await self.hid_output.execute_form(task)
        await self.sleep(self.config.wait_after_action)

        await self.wait_and_click_start_check(started_at)
        await self.wait_and_click_data_analysis(started_at)
        await self.wait_and_click_pdf_yes(started_at)
        await self.wait_and_confirm_report_generated(started_at)
        await self.wait_and_click_new_patient_to_finish(started_at)
        return "analysis_finished"

    def check_runtime(self, started_at: float) -> None:
        if self.config.max_runtime > 0 and time.monotonic() - started_at > self.config.max_runtime:
            raise asyncio.TimeoutError(f"vision flow max runtime reached: {self.config.max_runtime:.1f}s")

    def capture_options(self) -> dict[str, Any]:
        return {
            "width": int(getattr(self.config, "capture_width", 1920)),
            "height": int(getattr(self.config, "capture_height", 1080)),
            "framerate": int(getattr(self.config, "capture_framerate", 30)),
            "frames": int(getattr(self.config, "capture_frames", 30)),
            "io_mode": int(getattr(self.config, "capture_io_mode", 2)),
            "capture_format": str(getattr(self.config, "capture_format", "mjpg")),
        }

    async def prepare_new_patient_window(self, started_at: float) -> dict[str, Any]:
        open_requested = False
        while True:
            self.check_runtime(started_at)
            response = await self.detect_window(f"window_{self.capture_index + 1}.jpg")

            if is_new_patient_window(response):
                LOGGER.info("vision linear action=form_input target=None")
                return response

            if is_login_window(response):
                LOGGER.info("vision linear action=click_text target=%s", LOGIN_TEXT)
                await self.click_ocr_text_required(response, LOGIN_TEXT)
                await self.sleep(self.config.wait_after_action)
                continue

            if is_ready_to_create_patient(response):
                LOGGER.info("vision linear action=click_text target=%s", NEW_PATIENT_TEXT)
                await self.click_ocr_text_required(response, NEW_PATIENT_TEXT)
                await self.sleep(self.config.wait_after_action)
                continue

            if has_no_detected_window(response):
                if open_requested:
                    LOGGER.info("vision no window after successful open; wait instead of opening again")
                    await self.sleep(self.config.wait_after_action)
                    continue
                ok = await self.open_app(f"open_{self.capture_index + 1}.jpg")
                if not ok:
                    LOGGER.info("vision icon not found; wait %.1fs before retry", self.config.wait_after_no_detection)
                    await self.sleep(self.config.wait_after_no_detection)
                    continue
                open_requested = True
                await self.sleep(self.config.wait_after_open)
                continue

            LOGGER.info("vision linear action=wait target=None stage=prepare")
            await self.sleep(self.config.wait_after_action)

    async def wait_and_click_start_check(self, started_at: float) -> None:
        while True:
            self.check_runtime(started_at)
            response = await self.detect_window(f"start_{self.capture_index + 1}.jpg")
            if is_ready_to_start_check(response):
                LOGGER.info("vision linear action=click_text target=%s", START_CHECK_TEXT)
                await self.click_ocr_text_required(response, START_CHECK_TEXT)
                await self.sleep(LINEAR_POLL_SECONDS)
                return
            LOGGER.info("vision linear action=wait target=None stage=start_check")
            await self.sleep(LINEAR_POLL_SECONDS)

    async def wait_and_click_data_analysis(self, started_at: float) -> None:
        while True:
            self.check_runtime(started_at)
            response = await self.detect_window(f"complete_{self.capture_index + 1}.jpg")
            if is_check_complete(response):
                LOGGER.info("vision linear action=click_text target=%s", ANALYSIS_TEXT)
                await self.click_ocr_text_required(response, ANALYSIS_TEXT)
                await self.sleep(max(self.config.analysis_wait, LINEAR_POLL_SECONDS))
                return
            LOGGER.info("vision linear action=wait target=None stage=check_complete")
            await self.sleep(LINEAR_POLL_SECONDS)

    async def wait_and_click_pdf_yes(self, started_at: float) -> None:
        while True:
            self.check_runtime(started_at)
            response = await self.detect_window(f"pdf_prompt_{self.capture_index + 1}.jpg")
            if is_pdf_report_prompt(response):
                target = pdf_report_yes_target(response)
                if target is None:
                    raise RuntimeError("vision PDF report prompt found but no yes OCR target")
                LOGGER.info("vision linear action=click_text target=%s", target)
                await self.click_ocr_text_required(response, target)
                await self.sleep(self.config.wait_after_action)
                return
            LOGGER.info("vision linear action=wait target=None stage=pdf_prompt")
            await self.sleep(LINEAR_POLL_SECONDS)

    async def wait_and_confirm_report_generated(self, started_at: float) -> None:
        while True:
            self.check_runtime(started_at)
            response = await self.detect_window(f"report_done_{self.capture_index + 1}.jpg")
            if is_report_generated(response):
                target = confirm_button_target(response) or "确定"
                LOGGER.info("vision linear action=click_text target=%s", target)
                await self.click_ocr_text_required(response, target)
                await self.sleep(self.config.wait_after_action)
                return
            LOGGER.info("vision linear action=wait target=None stage=report_generated")
            await self.sleep(LINEAR_POLL_SECONDS)

    async def wait_and_click_new_patient_to_finish(self, started_at: float) -> None:
        while True:
            self.check_runtime(started_at)
            response = await self.detect_window(f"finish_{self.capture_index + 1}.jpg")
            if has_label(response, "1") and find_ocr_center(response, NEW_PATIENT_TEXT) is not None:
                LOGGER.info("vision linear action=click_text target=%s", NEW_PATIENT_TEXT)
                await self.click_ocr_text_required(response, NEW_PATIENT_TEXT)
                await self.sleep(self.config.wait_after_action)
                return
            LOGGER.info("vision linear action=wait target=None stage=finish")
            await self.sleep(LINEAR_POLL_SECONDS)

    async def detect_window(self, image_name: str) -> dict[str, Any]:
        self.capture_index += 1
        image_path = self.workdir / image_name
        LOGGER.info("vision capture window image: %s", image_path)
        await to_thread(capture_jpeg, self.config.device, image_path, self.config.timeout_seconds, **self.capture_options())
        response = await to_thread(post_image, self.config.window_endpoint, image_path, self.config.timeout_seconds)
        LOGGER.info("vision window response: %s", json.dumps(response, ensure_ascii=False))
        return response

    async def open_app(self, image_name: str) -> bool:
        image_path = self.workdir / image_name
        LOGGER.info("vision capture open image: %s", image_path)
        await to_thread(capture_jpeg, self.config.device, image_path, self.config.timeout_seconds, **self.capture_options())
        response = await to_thread(
            post_image,
            self.config.icon_endpoint,
            image_path,
            self.config.timeout_seconds,
            {"software": self.config.software},
        )
        LOGGER.info("vision icon response: %s", json.dumps(response, ensure_ascii=False))
        center = extract_center(response, "center")
        if center is None:
            return False
        await self.hid_output.click(center[0], center[1])
        await self.sleep(0.12)
        await self.hid_output.click(center[0], center[1])
        return True

    async def click_ocr_text(self, response: dict[str, Any], text: str) -> bool:
        center = find_ocr_center(response, text)
        if center is None:
            return False
        await self.hid_output.click(center[0], center[1])
        return True

    async def click_ocr_text_required(self, response: dict[str, Any], text: str) -> None:
        if not await self.click_ocr_text(response, text):
            raise RuntimeError(f"vision OCR text not found: {text}")

    async def handle_analysis(self, response: dict[str, Any]) -> str | None:
        if not await self.click_ocr_text(response, ANALYSIS_TEXT):
            raise RuntimeError(f"vision OCR text not found: {ANALYSIS_TEXT}")
        await self.sleep(max(self.config.analysis_wait, ANALYSIS_NO_RESPONSE_WAIT_SECONDS))
        next_response = await self.detect_window(f"after_analysis_{self.capture_index + 1}.jpg")
        action, target = decide_after_analysis(next_response)
        LOGGER.info("vision after_analysis_action=%s target=%s", action, target)
        if action == "click_text" and target:
            if not await self.click_ocr_text(next_response, target):
                raise RuntimeError(f"vision OCR text not found: {target}")
            return "dialog_confirmed"
        if action == "finish":
            return "analysis_finished"
        LOGGER.info("vision analysis no response after %.1fs; finish flow", ANALYSIS_NO_RESPONSE_WAIT_SECONDS)
        return "analysis_finished"

    async def sleep(self, seconds: float) -> None:
        await asyncio.sleep(max(seconds, 0.0))
