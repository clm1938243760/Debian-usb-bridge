from __future__ import annotations

import asyncio
import logging
import time
from datetime import datetime
from typing import Any

from .config import AppConfig
from .events import GatewayEvent
from .form import build_form_task
from .hid_output import HidOutput
from .patient_api import PatientApiClient
from .queue import EventQueue

LOGGER = logging.getLogger(__name__)
HID_FORM_MIN_TIMEOUT_SECONDS = 30.0
HID_FORM_MAX_TIMEOUT_SECONDS = 120.0


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

    async def handle_scan(self, scan: str) -> None:
        scan = scan.strip().upper()
        if len(scan) < 8:
            LOGGER.warning("ignore short scan code=%s", scan)
            self._set_display("wait_scan", "invalid scan", "scan again", scan=scan, items=[], selected_index=0)
            return
        if not self._interactive_lock.locked():
            self._set_display("querying", "querying order", "please wait", scan=scan, items=[], selected_index=0)
        try:
            records = _group_records_by_exam_item(await self.patient_api.query_records(scan))
        except Exception:
            LOGGER.exception("scan query failed code=%s", scan)
            records = []
        if not records:
            if not self._interactive_lock.locked():
                self._show_not_found(scan)
            self.queue.put(
                GatewayEvent(
                    type="patient.query_failed",
                    device_id=self.config.device.id,
                    payload={"code": scan},
                )
            )
            return

        async with self._interactive_lock:
            items = [_record_item(record) for record in records]
            if len(records) == 1:
                index = 0
            else:
                self._set_display(
                    "select_item",
                    "select exam item",
                    "DOWN selects, OK confirms",
                    scan=scan,
                    items=items,
                    selected_index=0,
                )
                index = await self._wait_selection()
                if index < 0 or index >= len(records):
                    LOGGER.warning("selection index out of range index=%s records=%d", index, len(records))
                    index = 0
            record = records[index]

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
            self._set_display(
                "inputting",
                "auto input",
                "do not touch keyboard or mouse",
                scan=scan,
                items=items,
                selected_index=index,
            )
            self._hid_input_active = True
            input_ok = False
            try:
                timeout = self._hid_form_timeout(task)
                await asyncio.wait_for(self.hid_output.execute_form(task), timeout=timeout)
                input_ok = True
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
                self._hid_input_active = False
            if not input_ok:
                self._set_display(
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
            self._set_display(
                "upload_done",
                "input done",
                "ready for next scan",
                scan=scan,
                items=items,
                selected_index=index,
                done_at=time.time(),
            )

    def is_hid_input_active(self) -> bool:
        return self._hid_input_active

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
        if self.display_state.get("screen") == "upload_done":
            done_at = float(self.display_state.get("done_at", 0) or 0)
            if done_at and time.time() - done_at >= 3:
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

    def _show_not_found(self, scan: str) -> None:
        self._set_display("not_found", "no order found", "scan again", scan=scan, items=[], selected_index=0)


def _safe_record(record: dict[str, Any]) -> dict[str, Any]:
    return {
        str(key): value
        for key, value in record.items()
        if isinstance(value, (str, int, float, bool)) or value is None
    }


def _record_item(record: dict[str, Any]) -> dict[str, str]:
    return {
        "exam_item": str(record.get("exam_item", "") or "unnamed item"),
        "patient_name": str(record.get("patient_name", "") or ""),
        "patient_id": str(record.get("patient_id", "") or ""),
        "report_no": str(record.get("report_no", "") or ""),
    }


def _group_records_by_exam_item(records: list[dict[str, Any]]) -> list[dict[str, Any]]:
    seen = set()
    result = []
    for record in records:
        items = _split_exam_items(str(record.get("exam_item", "") or ""))
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
    normalized = value.replace("；", ",").replace("、", ",").replace("，", ",").replace(";", ",")
    return [part.strip() for part in normalized.split(",") if part.strip()]


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
