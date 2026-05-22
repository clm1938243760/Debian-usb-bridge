from __future__ import annotations

import asyncio
import logging
import time
from datetime import datetime
from typing import Any, Optional

from .config import AppConfig
from .events import GatewayEvent
from .form import build_form_task
from .hid_output import HidOutput
from .patient_api import PatientApiClient
from .queue import EventQueue

LOGGER = logging.getLogger(__name__)
HID_FORM_MIN_TIMEOUT_SECONDS = 30.0
HID_FORM_MAX_TIMEOUT_SECONDS = 120.0
EXAM_ITEM_KEYS = ("exam_item", "exam_item_name", "examItemName", "examItem")


class GatewayWorkflow:
    def __init__(self, config: AppConfig, queue: EventQueue) -> None:
        self.config = config
        self.queue = queue
        self.patient_api = PatientApiClient(config.patient_api)
        self.hid_output = HidOutput(config.hid_input)
        self._interactive_lock = asyncio.Lock()
        self._hid_input_active = False
        self._started_at = time.time()
        self._handled_report_events = set()
        self.display_state = {
            "screen": "wait_scan",
            "title": "waiting for scan",
            "message": "scan patient barcode",
            "items": [],
            "selected_index": 0,
            "scan": "",
            "popup": None,
        }
        self._selection_event = None
        self._active_scan_task = None
        self._scan_generation = 0
        self._hid_input_generation = 0

    def start_scan(self, scan: str) -> Optional[asyncio.Task]:
        if self._hid_input_active:
            LOGGER.info("ignore scan during hid input code=%s", scan)
            return None
        self._scan_generation += 1
        generation = self._scan_generation
        current = self._active_scan_task
        if current and not current.done():
            LOGGER.info("cancel previous scan workflow for new code=%s", scan)
            current.cancel()
        task = asyncio.create_task(self._run_scan(scan, generation))
        self._active_scan_task = task
        task.add_done_callback(self._scan_task_done)
        return task

    async def handle_scan(self, scan: str) -> None:
        task = self.start_scan(scan)
        if task:
            await task

    async def _run_scan(self, scan: str, generation: int) -> None:
        scan = scan.strip().upper()
        if len(scan) < 8:
            LOGGER.warning("ignore short scan code=%s", scan)
            self._set_scan_display(generation, "wait_scan", "invalid scan", "scan again", scan=scan, items=[], selected_index=0)
            return
        self._set_scan_display(generation, "querying", "querying order", "please wait", scan=scan, items=[], selected_index=0)
        try:
            raw_records = await self.patient_api.query_records(scan)
            records = _group_records_by_exam_item(raw_records)
            LOGGER.info(
                "scan query result code=%s api_records=%d grouped_items=%d",
                scan,
                len(raw_records),
                len(records),
            )
        except asyncio.CancelledError:
            LOGGER.info("scan workflow cancelled during query code=%s", scan)
            raise
        except Exception:
            LOGGER.exception("scan query failed code=%s", scan)
            raw_records = []
            records = []
        self._raise_if_stale(generation)
        if not records:
            self._show_not_found(scan, generation)
            self.queue.put(
                GatewayEvent(
                    type="patient.query_failed",
                    device_id=self.config.device.id,
                    payload={"code": scan},
                )
            )
            return

        async with self._interactive_lock:
            self._raise_if_stale(generation)
            items = [_record_item(record) for record in records]
            device_type = self.config.device.type.strip()
            selected_index = _matching_exam_index(records, device_type)
            patient_exam_items = _exam_item_names(records)
            auto_input = selected_index is not None
            LOGGER.info(
                "scan workflow decision code=%s device_type=%s auto_input=%s api_records=%d grouped_items=%d patient_items=%s",
                scan,
                device_type,
                auto_input,
                len(raw_records),
                len(records),
                patient_exam_items,
            )
            if selected_index is None:
                self._show_exam_mismatch(scan, generation, patient_exam_items, device_type)
                self.queue.put(
                    GatewayEvent(
                        type="patient.exam_mismatch",
                        device_id=self.config.device.id,
                        payload={"code": scan, "device_type": device_type, "exam_items": patient_exam_items},
                    )
                )
                return
            index = selected_index
            record = records[index]
            selected_exam_item = _exam_item_name(record)
            other_exam_items = [
                item
                for item_index, item in enumerate(patient_exam_items)
                if item_index != index and item
            ]

            self.queue.put(
                GatewayEvent(
                    type="patient.selected",
                    device_id=self.config.device.id,
                    payload={"code": scan, "record": _safe_record(record)},
                )
            )

            task = build_form_task(scan, record, self.config.hid_input.template_path)
            self.queue.put(
                GatewayEvent(
                    type="hid.form_task",
                    device_id=self.config.device.id,
                    payload={"code": scan, "task": task},
                )
            )
            self._set_scan_display(
                generation,
                "inputting",
                "auto input",
                "do not touch keyboard or mouse",
                scan=scan,
                items=items,
                selected_index=index,
                exam_item=selected_exam_item,
                other_exam_items=other_exam_items,
                patient_exam_items=patient_exam_items,
                device_type=device_type,
            )
            self._hid_input_active = True
            self._hid_input_generation = generation
            input_ok = False
            try:
                timeout = self._hid_form_timeout(task)
                await asyncio.wait_for(self.hid_output.execute_form(task), timeout=timeout)
                self._raise_if_stale(generation)
                input_ok = True
            except asyncio.CancelledError:
                LOGGER.info("scan workflow cancelled during hid input code=%s", scan)
                raise
            except asyncio.TimeoutError:
                LOGGER.error("hid form timeout code=%s timeout=%.1fs", scan, timeout)
                self.queue.put(
                    GatewayEvent(
                        type="hid.form_failed",
                        device_id=self.config.device.id,
                        payload={"code": scan, "error": "hid input timeout", "timeout_seconds": timeout},
                    )
                )
            except Exception as exc:
                LOGGER.exception("hid form failed code=%s", scan)
                self.queue.put(
                    GatewayEvent(
                        type="hid.form_failed",
                        device_id=self.config.device.id,
                        payload={"code": scan, "error": str(exc)},
                    )
                )
            finally:
                if self._hid_input_generation == generation:
                    self._hid_input_active = False
                    self._hid_input_generation = 0
            if not input_ok:
                self._set_scan_display(
                    generation,
                    "wait_scan",
                    "input failed",
                    "scan again",
                    scan="",
                    items=[],
                    selected_index=0,
                    popup={
                        "title": "录入失败",
                        "message": "请重新扫码",
                        "source": "hid.form_failed",
                        "expires_at": time.time() + 2.0,
                    },
                )
                return
            self.queue.put(
                GatewayEvent(
                    type="hid.form_done",
                    device_id=self.config.device.id,
                    payload={"code": scan, "patient": task.get("patient", {})},
                )
            )
            self._set_scan_display(
                generation,
                "upload_done",
                "input done",
                "ready for next scan",
                scan=scan,
                items=items,
                selected_index=index,
                exam_item=selected_exam_item,
                other_exam_items=other_exam_items,
                patient_exam_items=patient_exam_items,
                device_type=device_type,
                return_after_seconds=4,
                done_at=time.time(),
            )

    def is_hid_input_active(self) -> bool:
        return self._hid_input_active

    def _scan_task_done(self, task: asyncio.Task) -> None:
        if self._active_scan_task is task:
            self._active_scan_task = None
        try:
            task.result()
        except asyncio.CancelledError:
            pass
        except Exception:
            LOGGER.exception("scan workflow task failed")

    def _is_current_scan(self, generation: int) -> bool:
        return generation == self._scan_generation

    def _raise_if_stale(self, generation: int) -> None:
        if not self._is_current_scan(generation):
            raise asyncio.CancelledError()

    def _hid_form_timeout(self, task: dict[str, Any]) -> float:
        events = task.get("eventClassList", [])
        event_count = len(events) if isinstance(events, list) else 0
        start_delay = self.config.hid_input.start_delay_ms / 1000
        action_delay = self.config.hid_input.action_delay_ms / 1000
        paste_wait = self.config.hid_input.powershell_wait_ms / 1000
        timeout = start_delay + event_count * max(action_delay + paste_wait + 0.8, 2.0) + 8.0
        return max(HID_FORM_MIN_TIMEOUT_SECONDS, min(HID_FORM_MAX_TIMEOUT_SECONDS, timeout))

    def handle_key(self, key: str) -> None:
        if self.display_state.get("screen") != "select_item":
            return
        items = self.display_state.get("items") or []
        if not items:
            return
        index = int(self.display_state.get("selected_index", 0))
        if key == "up":
            index = (index - 1) % len(items)
        elif key == "down":
            index = (index + 1) % len(items)
        elif key == "ok":
            event = self._selection_event
            if event and not event.is_set():
                event.set()
            return
        self.display_state["selected_index"] = index

    def handle_report_received(self, source: str, path: str = "", created_at: str = "", event_id: str = "") -> bool:
        event_key = event_id or f"{source}|{path}|{created_at}"
        if event_key in self._handled_report_events:
            return False
        if created_at and _event_time(created_at) and _event_time(created_at) < self._started_at:
            self._handled_report_events.add(event_key)
            return False
        if len(self._handled_report_events) > 500:
            self._handled_report_events.clear()
        self._handled_report_events.add(event_key)

        name = path.rsplit("/", 1)[-1] if path else ""
        if source == "msc.file_copied":
            title = "U盘文件已接收"
        elif source == "print.captured":
            title = "模拟打印已接收"
        else:
            title = "文件已接收"
        self.display_state["popup"] = {
            "title": title,
            "message": name or "正在转换并打印",
            "source": source,
            "path": path,
            "expires_at": time.time() + 2.0,
            "event_key": event_key,
        }
        return True

    def handle_report_upload(
        self,
        source: str,
        path: str = "",
        error: str = "",
        printed: bool = False,
        created_at: str = "",
        event_id: str = "",
    ) -> bool:
        event_key = event_id or f"{source}|{path}|{created_at}"
        if event_key in self._handled_report_events:
            return False
        if created_at and _event_time(created_at) and _event_time(created_at) < self._started_at:
            self._handled_report_events.add(event_key)
            return False
        if len(self._handled_report_events) > 500:
            self._handled_report_events.clear()
        self._handled_report_events.add(event_key)

        if source == "report.uploaded":
            title = "报告上传成功"
            message = "已提交实体打印" if printed else "上传成功，未打印"
        else:
            title = "报告上传失败"
            message = _short_error(error) or (path.rsplit("/", 1)[-1] if path else "未提交打印")
        self.display_state["popup"] = {
            "title": title,
            "message": message,
            "source": source,
            "path": path,
            "expires_at": time.time() + 2.0,
            "event_key": event_key,
        }
        return True

    def get_display_state(self) -> dict[str, Any]:
        if self.display_state.get("screen") in ("upload_done", "exam_mismatch"):
            done_at = float(self.display_state.get("done_at", 0) or 0)
            return_after = float(self.display_state.get("return_after_seconds", 3) or 3)
            if done_at and time.time() - done_at >= return_after:
                self._set_display("wait_scan", "waiting for scan", "scan patient barcode", items=[], selected_index=0, scan="")
        popup = self.display_state.get("popup")
        if isinstance(popup, dict) and float(popup.get("expires_at", 0) or 0) <= time.time():
            self.display_state["popup"] = None
        state = dict(self.display_state)
        state["updated_at"] = time.time()
        return state

    async def _wait_selection(self) -> int:
        self._selection_event = asyncio.Event()
        await self._selection_event.wait()
        return int(self.display_state.get("selected_index", 0))

    def _set_display(self, screen: str, title: str, message: str, **extra: Any) -> None:
        self.display_state.update({"screen": screen, "title": title, "message": message, **extra})

    def _set_scan_display(self, generation: int, screen: str, title: str, message: str, **extra: Any) -> None:
        if self._is_current_scan(generation):
            self._set_display(screen, title, message, **extra)

    def _show_not_found(self, scan: str, generation: int) -> None:
        self._set_scan_display(generation, "not_found", "未找到申请单", "请核对条码后重试", scan=scan, items=[], selected_index=0)

    def _show_exam_mismatch(self, scan: str, generation: int, patient_exam_items: list[str], device_type: str) -> None:
        self._set_scan_display(
            generation,
            "exam_mismatch",
            "患者检查项目与设备不符",
            "未执行自动录入",
            scan=scan,
            items=[],
            selected_index=0,
            patient_exam_items=patient_exam_items,
            device_type=device_type,
            done_at=time.time(),
            return_after_seconds=4,
        )


def _safe_record(record: dict[str, Any]) -> dict[str, Any]:
    return {
        str(key): value
        for key, value in record.items()
        if isinstance(value, (str, int, float, bool)) or value is None
    }


def _record_item(record: dict[str, Any]) -> dict[str, str]:
    return {
        "exam_item": _exam_item_name(record) or "unnamed item",
        "patient_name": str(record.get("patient_name", "") or ""),
        "patient_id": str(record.get("patient_id", "") or ""),
        "report_no": str(record.get("report_no", "") or ""),
    }


def _exam_item_name(record: dict[str, Any]) -> str:
    for key in EXAM_ITEM_KEYS:
        value = str(record.get(key, "") or "").strip()
        if value:
            return value
    return ""


def _exam_item_names(records: list[dict[str, Any]]) -> list[str]:
    names = []
    for record in records:
        name = _exam_item_name(record)
        if name:
            names.append(name)
    return names


def _matching_exam_index(records: list[dict[str, Any]], device_type: str) -> Optional[int]:
    target = _normalize_exam_item(device_type)
    if not target:
        return None
    for index, record in enumerate(records):
        candidate = _normalize_exam_item(_exam_item_name(record))
        if candidate == target or target in candidate:
            return index
    return None


def _group_records_by_exam_item(records: list[dict[str, Any]]) -> list[dict[str, Any]]:
    seen = set()
    result = []
    for record in records:
        items = _split_exam_items(_exam_item_name(record))
        if not items:
            items = [""]
        for item in items:
            key = item or f"{record.get('patient_id', '')}|{record.get('his_exam_no', '')}|{record.get('report_no', '')}"
            if key in seen:
                continue
            seen.add(key)
            grouped = dict(record)
            if item:
                grouped["exam_item"] = item
            result.append(grouped)
    return result


def _split_exam_items(value: str) -> list[str]:
    normalized = value
    for separator in ("；", "、", "，", ";", "\n", "\r", "\t", "|", "｜", "/", "／", "\\", "+", "＋"):
        normalized = normalized.replace(separator, ",")
    return [part.strip() for part in normalized.split(",") if part.strip()]


def _normalize_exam_item(value: str) -> str:
    return "".join(str(value or "").split()).strip()


def _event_time(created_at: str) -> float:
    try:
        return datetime.fromisoformat(created_at.replace("Z", "+00:00")).timestamp()
    except Exception:
        return 0.0


def _short_error(error: str) -> str:
    text = str(error or "").strip()
    if not text:
        return ""
    for marker in ('"msg":"', '"msg": "'):
        if marker in text:
            start = text.find(marker) + len(marker)
            end = text.find('"', start)
            if end > start:
                return text[start:end][:28]
    return text[:28]
