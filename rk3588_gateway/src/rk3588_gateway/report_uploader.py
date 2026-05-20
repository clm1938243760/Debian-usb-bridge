from __future__ import annotations

import logging
import uuid
import urllib.error
import urllib.request
from pathlib import Path
from typing import Iterable, Tuple

from .config import ReportUploadConfig

LOGGER = logging.getLogger(__name__)


class ReportUploader:
    def __init__(self, config: ReportUploadConfig) -> None:
        self.config = config

    def upload_blocking(self, pdf_path: Path) -> bool:
        if not self.config.enabled:
            return False
        if not self.config.endpoint:
            LOGGER.warning("report upload endpoint is empty")
            return False

        report_path = Path(pdf_path)
        report_info_path = Path(self.config.report_info_path)
        if not report_path.exists():
            LOGGER.error("report upload pdf missing: %s", report_path)
            return False
        if not report_info_path.exists():
            LOGGER.error("report upload ReportInfo.xml missing: %s", report_info_path)
            return False

        boundary = "----rk3568-gateway-%s" % uuid.uuid4().hex
        body = _multipart_body(
            boundary,
            (
                ("Report", report_path, "application/pdf"),
                ("ReportInfo", report_info_path, "application/xml"),
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
            ok = 200 <= status < 300
            if ok:
                LOGGER.info("report upload submitted path=%s status=%s response=%s", report_path, status, text[:500])
            else:
                LOGGER.error("report upload failed path=%s status=%s response=%s", report_path, status, text[:500])
            return ok
        except urllib.error.HTTPError as exc:
            detail = exc.read().decode("utf-8", errors="replace")
            LOGGER.error(
                "report upload http error path=%s status=%s response=%s",
                report_path,
                exc.code,
                detail[:500],
            )
        except Exception:
            LOGGER.exception("report upload exception path=%s", report_path)
        return False


def _multipart_body(boundary: str, files: Iterable[Tuple[str, Path, str]]) -> bytes:
    chunks = []
    boundary_bytes = boundary.encode("ascii")
    for field_name, path, content_type in files:
        filename = path.name
        chunks.extend(
            [
                b"--" + boundary_bytes + b"\r\n",
                (
                    'Content-Disposition: form-data; name="%s"; filename="%s"\r\n'
                    % (field_name, filename)
                ).encode("utf-8"),
                ("Content-Type: %s\r\n\r\n" % content_type).encode("ascii"),
                path.read_bytes(),
                b"\r\n",
            ]
        )
    chunks.append(b"--" + boundary_bytes + b"--\r\n")
    return b"".join(chunks)
