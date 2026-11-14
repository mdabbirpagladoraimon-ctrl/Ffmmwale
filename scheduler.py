import logging
import threading
import time
from datetime import datetime

from config import Config
from models import get_db, dec
from checker import check_account, CheckerError

log = logging.getLogger("scheduler")

_started = False
_lock = threading.Lock()


def _run_once():
    c = get_db()
    try:
        rows = c.execute(
            "SELECT id, email_enc, password_enc FROM accounts "
            "WHERE sold=0 ORDER BY id ASC"
        ).fetchall()
    finally:
        c.close()

    if not rows:
        log.info("scheduler: no unsold accounts, skipping")
        return

    log.info("scheduler: checking %d accounts", len(rows))
    ok = offline = err = 0

    for r in rows:
        try:
            email = dec(r["email_enc"])
            password = dec(r["password_enc"])
        except Exception as e:
            log.warning("decrypt failed for id=%s: %s", r["id"], e)
            continue

        try:
            res = check_account(email, password)
        except CheckerError as e:
            res = {"ok": False, "error": str(e), "raw": {}}
        except Exception:
            log.exception("check crashed for id=%s", r["id"])
            res = {"ok": False, "error": "exception", "raw": {}}

        now = datetime.utcnow().isoformat(timespec="seconds")

        c = get_db()
        try:
            if res["ok"]:
                new_status = "LIVE" if res["live"] else "EXPIRED"
                c.execute(
                    """UPDATE accounts SET
                         status=?,
                         plan_name=COALESCE(?, plan_name),
                         plan_category=COALESCE(?, plan_category),
                         fw_balance=?,
                         fw_currency=?,
                         expiry_date=COALESCE(?, expiry_date),
                         days_left=?,
                         last_checked=?,
                         check_error=NULL
                       WHERE id=?""",
                    (
                        new_status,
                        res.get("plan_name"),
                        res.get("plan_category"),
                        res["wallet"]["balance"],
                        res["wallet"]["currency"],
                        res.get("expiry"),
                        res.get("days_left"),
                        now,
                        r["id"],
                    ),
                )
                ok += 1
            else:
                c.execute(
                    "UPDATE accounts SET status='OFFLINE', "
                    "last_checked=?, check_error=? WHERE id=?",
                    (now, (res.get("error") or "unknown")[:120], r["id"]),
                )
                offline += 1
        except Exception:
            log.exception("db update failed for id=%s", r["id"])
            err += 1
        finally:
            c.close()

    log.info("scheduler done — ok=%d offline=%d err=%d", ok, offline, err)


def _loop():
    time.sleep(15)
    while True:
        try:
            _run_once()
        except Exception:
            log.exception("scheduler cycle crashed")
        time.sleep(Config.CHECK_INTERVAL_SECONDS)


def start():
    global _started
    with _lock:
        if _started:
            return
        _started = True
    t = threading.Thread(target=_loop, daemon=True, name="checker-scheduler")
    t.start()
    log.info(
        "scheduler started — every %ss", Config.CHECK_INTERVAL_SECONDS,
  )
