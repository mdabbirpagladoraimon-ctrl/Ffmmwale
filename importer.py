import csv
import io
from datetime import datetime, timezone

from config import Config
from models import enc


def norm(k: str) -> str:
    return (k or "").replace("\ufeff", "").strip().lower()


REQUIRED_HEADER = {"email", "password"}


def _prices_for(category: str) -> dict:
    return Config.PLAN_PRICES.get(category, Config.PLAN_PRICES["silver"])


# ======================================================================
#              format A — colon (email:password)
# ======================================================================
def parse_colon_text(text: str) -> list[dict]:
    out = []
    for raw in text.splitlines():
        line = raw.strip()
        if not line or line.startswith("#"):
            continue
        if ":" not in line:
            continue
        email, _, password = line.rpartition(":")
        email = email.strip()
        password = password.strip()
        if not email or not password:
            continue
        if "@" not in email and len(email) < 3:
            continue
        out.append({"email": email, "password": password})
    return out


def import_colon_rows(conn, rows, default_category="silver"):
    added = 0
    failed = []
    prices = _prices_for(default_category)
    for i, r in enumerate(rows, start=1):
        try:
            conn.execute(
                """INSERT INTO accounts
                   (email_enc, password_enc, plan_name, plan_category,
                    original_price, original_currency, selling_price, cost,
                    status, expiry_date, last_sync, notes, fw_balance, fw_currency)
                   VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?)""",
                (enc(r["email"]), enc(r["password"]),
                 f"{default_category.title()} Plan", default_category,
                 prices["original_price"], prices["original_currency"],
                 prices["selling_price"], 0,
                 "LIVE", None,
                 datetime.utcnow().isoformat(timespec="seconds"),
                 "", 0.0, "USD"),
            )
            added += 1
        except Exception as e:
            failed.append(f"row {i} ({r['email'][:20]}): {e}")
    return {"added": added, "skipped": 0, "failed": failed}


# ======================================================================
#              format B — CSV
# ======================================================================
def parse_csv_bytes(raw_bytes: bytes):
    raw = raw_bytes.decode("utf-8-sig", errors="replace")
    raw = raw.replace("\r\n", "\n").replace("\r", "\n")
    reader = csv.DictReader(io.StringIO(raw))
    fieldnames = list(reader.fieldnames or [])
    rows = list(reader)
    normalized = {norm(k) for k in fieldnames}
    if not REQUIRED_HEADER.issubset(normalized):
        return fieldnames, None
    return fieldnames, rows


def import_csv_rows(conn, rows):
    added = 0
    skipped = 0
    failed = []
    for i, raw in enumerate(rows, start=2):
        clean = {norm(k): (v or "").strip() for k, v in raw.items()}
        email = clean.get("email")
        password = clean.get("password")
        if not email or not password:
            skipped += 1
            continue
        cat = (clean.get("plan_category") or "silver").lower()
        if cat not in ("silver", "gold"):
            cat = "silver"
        prices = _prices_for(cat)
        try:
            conn.execute(
                """INSERT INTO accounts
                   (email_enc, password_enc, plan_name, plan_category,
                    original_price, original_currency, selling_price, cost,
                    status, expiry_date, last_sync, notes, fw_balance, fw_currency)
                   VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?)""",
                (enc(email), enc(password),
                 f"{cat.title()} Plan", cat,
                 prices["original_price"], prices["original_currency"],
                 prices["selling_price"], float(clean.get("cost") or 0),
                 "LIVE", None,
                 datetime.utcnow().isoformat(timespec="seconds"),
                 clean.get("notes") or "", 0.0, "USD"),
            )
            added += 1
        except Exception as e:
            failed.append(f"row {i} ({email[:20]}): {e}")
    return {"added": added, "skipped": skipped, "failed": failed}


# ======================================================================
#              format C — pipe (Free Fire style)
# ======================================================================
PIPE_FIELDS = [
    "uid", "access_token", "username", "region",
    "external_id", "md5", "jwt", "account_id", "client_url",
]


def is_pipe_line(line: str) -> bool:
    return "|" in line and len(line.split("|")) >= 4


def parse_pipe_line(line: str) -> dict | None:
    parts = line.strip().split("|")
    if len(parts) < 4:
        return None
    parts = (parts + [""] * len(PIPE_FIELDS))[:len(PIPE_FIELDS)]
    d = dict(zip(PIPE_FIELDS, parts))

    uid = d["uid"].strip()
    token = d["access_token"].strip()
    if not uid or not token:
        return None

    username = d["username"].strip()
    region = d["region"].strip()

    notes_bits = []
    if username:
        notes_bits.append(f"user={username}")
    if region:
        notes_bits.append(f"region={region}")

    cat = "gold" if username.lower().startswith("zyeleo_") else "silver"

    return {
        "email": uid,
        "password": token,
        "plan_category": cat,
        "cost": 0,
        "notes": " | ".join(notes_bits),
    }


def parse_pipe_text(text: str) -> list[dict]:
    out = []
    for raw in text.splitlines():
        line = raw.strip()
        if not line or line.startswith("#"):
            continue
        row = parse_pipe_line(line)
        if row:
            out.append(row)
    return out


def import_pipe_rows(conn, rows, default_category="silver"):
    added = 0
    failed = []
    for i, r in enumerate(rows, start=1):
        cat = (r.get("plan_category") or default_category).lower()
        if cat not in ("silver", "gold"):
            cat = default_category
        prices = _prices_for(cat)
        try:
            conn.execute(
                """INSERT INTO accounts
                   (email_enc, password_enc, plan_name, plan_category,
                    original_price, original_currency, selling_price, cost,
                    status, expiry_date, last_sync, notes, fw_balance, fw_currency)
                   VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?)""",
                (enc(r["email"]), enc(r["password"]),
                 f"{cat.title()} Plan", cat,
                 prices["original_price"], prices["original_currency"],
                 prices["selling_price"], r.get("cost", 0),
                 "LIVE", None,
                 datetime.utcnow().isoformat(timespec="seconds"),
                 r.get("notes") or "", 0.0, "USD"),
            )
            added += 1
        except Exception as e:
            failed.append(f"row {i} ({r['email'][:20]}): {e}")
    return {"added": added, "skipped": 0, "failed": failed}