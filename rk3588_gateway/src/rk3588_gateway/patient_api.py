from __future__ import annotations

import base64
import asyncio
import json
import logging
from datetime import datetime
from pathlib import Path
from typing import Any, Optional

from aiohttp import ClientSession, ClientTimeout

from .config import PatientApiConfig

LOGGER = logging.getLogger(__name__)


def build_patient_sql(scan: str) -> str:
    kw = scan.replace("'", "''")
    return f"""select
  t.exam_item,
  t.his_exam_no,
  z.report_no,
  t.patient_id,
  t.patient_name,
  q.name_phonetic,
  substr(t.patient_name, 0, 2) as xing,
  substr(t.patient_name, 2, 8) as ming,
  t.sex,
  t.age,
  to_char(t.birthday,'yyyy') as nian,
  to_char(t.birthday,'mm') as yue,
  to_char(t.birthday,'dd') as ri,
  t.birthday
from exam_master t
left join exam_item z on t.his_exam_no=z.his_exam_no
left join patient_info q on t.patient_id=q.patient_id
where
  (
    z.report_no like '%{kw}%'
    or t.patient_id like '%{kw}%'
    or t.patient_name like '%{kw}%'
  )
  and z.exam_state='20'
  and req_date>= CURRENT_DATE - INTERVAL '180 days'
order by t.req_date desc
limit 20"""


def records_from_payload(payload: Any) -> list[dict[str, Any]]:
    if isinstance(payload, dict):
        data = payload.get("data")
        if isinstance(data, list):
            return [item for item in data if isinstance(item, dict)]
        if isinstance(data, dict):
            return [data]
        if payload.get("patient_id") or payload.get("patient_name"):
            return [payload]
    if isinstance(payload, list):
        return [item for item in payload if isinstance(item, dict)]
    return []


def first_record(payload: Any) -> Optional[dict[str, Any]]:
    records = records_from_payload(payload)
    if records:
        return records[0]
    return None


class PatientApiClient:
    def __init__(self, config: PatientApiConfig) -> None:
        self.config = config
        self.raw_dir = Path(config.raw_dir)
        self.raw_dir.mkdir(parents=True, exist_ok=True)

    async def query_first(self, scan: str) -> Optional[dict[str, Any]]:
        records = await self.query_records(scan)
        return records[0] if records else None

    async def query_records(self, scan: str) -> list[dict[str, Any]]:
        if not self.config.enabled:
            LOGGER.info("patient api disabled")
            return []
        if not self.config.endpoint:
            LOGGER.warning("patient api endpoint is empty")
            return []

        sql = build_patient_sql(scan)
        request_body = {"sqlStr": base64.b64encode(sql.encode("utf-8")).decode("ascii")}
        headers = {
            "Content-Type": "application/json;charset=UTF-8",
            "Accept": "application/json",
            "User-Agent": self.config.user_agent,
        }
        timeout = ClientTimeout(total=self.config.timeout_seconds)
        LOGGER.info("patient api request scan=%s endpoint=%s", scan, self.config.endpoint)

        try:
            async with ClientSession(timeout=timeout, headers=headers) as session:
                async with session.post(self.config.endpoint, json=request_body) as resp:
                    text = await resp.text()
                    self._save_raw(scan, resp.status, text)
                    if not 200 <= resp.status < 300:
                        LOGGER.warning("patient api http %s: %.300s", resp.status, text)
                        return []
        except asyncio.TimeoutError:
            LOGGER.warning("patient api timeout scan=%s endpoint=%s", scan, self.config.endpoint)
            return []
        except Exception:
            LOGGER.exception("patient api request failed scan=%s endpoint=%s", scan, self.config.endpoint)
            return []

        try:
            payload = json.loads(text)
        except json.JSONDecodeError:
            LOGGER.warning("patient api returned non-json: %.300s", text)
            return []

        records = filter_exact_records(records_from_payload(payload), scan)
        if records:
            record = records[0]
            LOGGER.info(
                "patient api selected first record patient_id=%s patient_name=%s exam_item=%s",
                record.get("patient_id", ""),
                record.get("patient_name", ""),
                record.get("exam_item", ""),
            )
        else:
            LOGGER.warning("patient api returned no usable record")
        return records

    def _save_raw(self, scan: str, status: int, text: str) -> None:
        stamp = datetime.now().strftime("%Y%m%d_%H%M%S_%f")
        safe_scan = "".join(ch if ch.isalnum() else "_" for ch in scan)[:80]
        path = self.raw_dir / f"api_{stamp}_{status}_{safe_scan}.json"
        path.write_text(text, encoding="utf-8")


def filter_exact_records(records: list[dict[str, Any]], scan: str) -> list[dict[str, Any]]:
    key = scan.upper().strip()
    result: list[dict[str, Any]] = []
    for record in records:
        values = (
            str(record.get("patient_id", "") or "").upper(),
            str(record.get("his_exam_no", "") or "").upper(),
            str(record.get("report_no", "") or "").upper(),
        )
        if key in values:
            result.append(record)
    return result
