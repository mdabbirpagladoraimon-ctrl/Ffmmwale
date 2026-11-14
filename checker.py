import logging
from datetime import datetime, timezone

import requests

from config import Config

log = logging.getLogger("checker")


class CheckerError(Exception):
    pass


def _fetch(url, timeout):
    try:
        r = requests.get(url, timeout=timeout)
    except requests.RequestException as e:
        raise CheckerError(f"network: {type(e).__name__}") from e
    if r.status_code == 404:
        raise CheckerError("404")
    if r.status_code >= 400:
        raise CheckerError(f"http {r.status_code}")
    try:
        return r.json()
    except ValueError:
        raise CheckerError("non-json")


def _parse(payload: dict, email: str) -> dict:
    out = {
        "ok": False,
        "live": False,
        "plan_name": None,
        "plan_category": "silver",
        "wallet": {"balance": 0.0, "currency": "USD"},
        "expiry": None,
        "days_left": None,
        "error": None,
        "raw": payload,
    }

    success = payload.get("success")
    if success is False:
        out["error"] = (
            payload.get("error") or payload.get("reason") or "check_failed"
        )
        return out

    account = payload.get("account") or {}
    wallet = payload.get("wallet") or {}
    plan = payload.get("plan") or {}

    out["ok"] = True
    out["plan_name"] = (
        plan.get("name") or account.get("planName") or payload.get("planName")
    )
    out["expiry"] = (
        account.get("endDate") or plan.get("endDate") or payload.get("endDate")
    )

    try:
        out["wallet"]["balance"] = float(wallet.get("balance") or 0)
    except (TypeError, ValueError):
        pass
    out["wallet"]["currency"] = wallet.get("currency") or "USD"

    if out["plan_name"]:
        low = out["plan_name"].lower()
        if any(k in low for k in (
            "gold", "golden", "royal", "premium", "diamond", "platinum"
        )):
            out["plan_category"] = "gold"
        else:
            out["plan_category"] = "silver"

    status = (account.get("status") or payload.get("status") or "").upper()
    if status in ("LIVE", "ACTIVE", "VALID"):
        out["live"] = True
    elif status in ("EXPIRED", "DEAD", "INVALID", "BANNED"):
        out["live"] = False
    elif out["expiry"]:
        try:
            end_dt = datetime.fromisoformat(
                str(out["expiry"]).replace("Z", "+00:00")
            )
            if end_dt.tzinfo is None:
                end_dt = end_dt.replace(tzinfo=timezone.utc)
            delta = (end_dt - datetime.now(timezone.utc)).days
            out["days_left"] = max(0, delta)
            out["live"] = delta > 0
        except Exception:
            out["live"] = True
    else:
        out["live"] = True

    if out["days_left"] is None and out["expiry"]:
        try:
            end_dt = datetime.fromisoformat(
                str(out["expiry"]).replace("Z", "+00:00")
            )
            if end_dt.tzinfo is None:
                end_dt = end_dt.replace(tzinfo=timezone.utc)
            out["days_left"] = max(
                0, (end_dt - datetime.now(timezone.utc)).days
            )
        except Exception:
            pass

    return out


def check_account(email: str, password: str, timeout: int = 30) -> dict:
    base = Config.CHECKER_API_BASE.rstrip("/")

    try:
        payload = _fetch(f"{base}/check/{email}:{password}", timeout)
        return _parse(payload, email)
    except CheckerError as e_a:
        log.info("shape A failed for %s: %s", email[:16], e_a)

    try:
        payload = _fetch(
            f"{base}/check?email={email}&password={password}", timeout
        )
        return _parse(payload, email)
    except CheckerError as e_b:
        log.warning("shape B failed for %s: %s", email[:16], e_b)

    return {
        "ok": False,
        "live": False,
        "plan_name": None,
        "plan_category": "silver",
        "wallet": {"balance": 0.0, "currency": "USD"},
        "expiry": None,
        "days_left": None,
        "error": "api_unreachable",
        "raw": {},
  }
