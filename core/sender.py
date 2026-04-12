import random
import re
import time
from collections.abc import Iterator

import httpx

from core.logger import get_logger
from core.templates import render

WAHA_BASE = "http://localhost:3000"
SESSION = "default"

_log = get_logger(__name__)


def normalize_phone(raw: str) -> str:
    """Strip non-digits; prepend 55 if 11 digits (Brazilian DDD+number)."""
    digits = re.sub(r"\D", "", raw)
    if len(digits) == 11:
        digits = "55" + digits
    return digits


def check_waha_status() -> dict:
    """GET /api/sessions/{SESSION} — returns status dict.

    Always returns a dict with at least {"status": str, "connected": bool}.
    Never raises — WAHA being unreachable is a normal operational state.
    """
    try:
        resp = httpx.get(f"{WAHA_BASE}/api/sessions/{SESSION}", timeout=5)
        resp.raise_for_status()
        data = resp.json()
        status = data.get("status", "UNKNOWN")
        connected = status == "WORKING"
        return {"status": status, "connected": connected, **data}
    except httpx.ConnectError:
        return {"status": "UNREACHABLE", "connected": False}
    except httpx.HTTPStatusError as exc:
        return {"status": f"HTTP_{exc.response.status_code}", "connected": False}
    except Exception as exc:
        _log.warning("waha_status_error", error=str(exc))
        return {"status": "ERROR", "connected": False}


def send_message(phone: str, text: str) -> dict:
    """POST /api/sendText — send a WhatsApp message via WAHA.

    Raises httpx.HTTPStatusError on non-2xx response.
    """
    payload = {
        "session": SESSION,
        "chatId": f"{phone}@c.us",
        "text": text,
    }
    resp = httpx.post(f"{WAHA_BASE}/api/sendText", json=payload, timeout=30)
    resp.raise_for_status()
    return resp.json()


def get_qr_code() -> str | None:
    """GET /api/screenshot — returns base64 PNG bytes if session needs QR scan, else None."""
    try:
        status = check_waha_status()
        if status.get("status") not in ("SCAN_QR_CODE", "STARTING"):
            return None
        resp = httpx.get(f"{WAHA_BASE}/api/screenshot", timeout=10)
        resp.raise_for_status()
        # WAHA returns the image directly as binary PNG
        import base64
        return base64.b64encode(resp.content).decode("utf-8")
    except Exception as exc:
        _log.warning("qr_code_error", error=str(exc))
        return None


def send_bulk(
    recipients: list[dict],
    template: str,
    dry_run: bool = False,
) -> Iterator[dict]:
    """Send a message to multiple recipients with rate limiting.

    Yields progress dicts for each recipient:
      {"index": int, "total": int, "client": dict, "status": "ok"/"error"/"dry_run",
       "message": str, "error": str (only on error)}

    Sleeps random.uniform(3, 8) seconds between real sends to avoid WhatsApp spam detection.
    Note: time.sleep() blocks Streamlit's thread — acceptable for a single-user local tool.
    """
    total = len(recipients)
    for i, client in enumerate(recipients):
        msg = render(template, client)

        if dry_run:
            yield {
                "index": i,
                "total": total,
                "client": client,
                "status": "dry_run",
                "message": msg,
            }
            continue

        # Rate limiting delay (skip before first message)
        if i > 0:
            time.sleep(random.uniform(3, 8))

        try:
            send_message(client["whatsapp"], msg)
            _log.info("message_sent", client_id=client.get("id"), phone=client["whatsapp"])
            yield {
                "index": i,
                "total": total,
                "client": client,
                "status": "ok",
                "message": msg,
            }
        except Exception as exc:
            _log.error("message_failed", client_id=client.get("id"), error=str(exc))
            yield {
                "index": i,
                "total": total,
                "client": client,
                "status": "error",
                "message": msg,
                "error": str(exc),
            }
