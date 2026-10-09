"""Bill Status Monitor - reads the JuteSmart "Bill Generation" records for each
mill login and saves them for the dashboard (bills.html).

Per run:
  1. log into smart.jutecomm.gov.in with each mill's JUTECOMM_USERNAME_n /
     JUTECOMM_PASSWORD_n secret (the same logins the order alerts use)
  2. list the mill's bills (name + last-modified time)
  3. fetch full details only for bills that are new or modified since last run
  4. write bill_snapshots/<mill>.json, <mill>.prev.json (when something changed)
     and bill_snapshots/status.json
No emails are sent; this feeds the dashboard only.
"""
import json
import os
import re
import sys
from datetime import datetime, timezone
from pathlib import Path
from urllib.parse import quote

import requests

PORTAL = os.environ.get("JUTESMART_PORTAL", "https://smart.jutecomm.gov.in")
DOCTYPE = "Invoice Generation By Mill"
SNAP_DIR = Path("bill_snapshots")
STATUS_FILE = SNAP_DIR / "status.json"
TIMEOUT = (20, 60)  # (connect, read) seconds

# Same mills, in the same order, as the order alerts (monitor.py)
MILL_NAMES = ["KCL", "KJPL", "Tepcon", "Kaliaganj", "GS"]


def load_accounts():
    accounts = []
    for n, name in enumerate(MILL_NAMES, start=1):
        user = os.environ.get(f"JUTECOMM_USERNAME_{n}")
        pw = os.environ.get(f"JUTECOMM_PASSWORD_{n}")
        if user and pw:
            accounts.append({"name": name, "user": user, "pass": pw})
        else:
            print(f"Skipping {name}: JUTECOMM_USERNAME_{n} / JUTECOMM_PASSWORD_{n} not set")
    if not accounts:
        raise SystemExit("No JuteSmart logins found in the environment")
    return accounts


# ---------------------------------------------------------------- portal
def login(user, pw):
    s = requests.Session()
    s.headers.update({
        "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36",
        "Accept": "application/json",
        "Expect": "",
    })
    r = s.post(f"{PORTAL}/api/method/login", data={"usr": user, "pwd": pw}, timeout=TIMEOUT)
    if r.status_code in (401, 403):
        raise RuntimeError("login failed - check this mill's username/password")
    r.raise_for_status()
    if r.json().get("message") != "Logged In":
        raise RuntimeError("login failed - check this mill's username/password")
    return s


def list_bills(s):
    """{bill name: last modified} for every bill visible to this login."""
    r = s.get(
        f"{PORTAL}/api/resource/{quote(DOCTYPE)}",
        params={"fields": json.dumps(["name", "modified"]), "limit_page_length": 0},
        timeout=TIMEOUT,
    )
    r.raise_for_status()
    return {row["name"]: row["modified"] for row in r.json().get("data", [])}


def fetch_bill(s, name):
    """Full bill record plus the display titles of linked records (docket no.)."""
    r = s.get(
        f"{PORTAL}/api/method/frappe.desk.form.load.getdoc",
        params={"doctype": DOCTYPE, "name": name},
        timeout=TIMEOUT,
    )
    r.raise_for_status()
    j = r.json()
    docs = j.get("docs") or []
    if not docs:
        raise RuntimeError(f"no data returned for bill {name}")
    return docs[0], j.get("_link_titles") or {}


# ---------------------------------------------------------------- record shaping
def s_(v):
    """Normalise a value to a trimmed string ('' for empty)."""
    if v is None:
        return ""
    if isinstance(v, float) and v.is_integer():
        v = int(v)
    return re.sub(r"\s+", " ", str(v)).strip()


def dt_min(v):
    """'2026-09-24 13:12:24.189221' -> '2026-09-24 13:12' (drop seconds noise)."""
    return s_(v)[:16]


def shape(doc, titles):
    """Flatten one bill into the fields shown on the dashboard."""
    docket_raw = s_(doc.get("docket_no"))
    rec = {
        "BILL": s_(doc.get("name")),
        "STATUS": s_(doc.get("new_status")),
        "BILL_DATE": s_(doc.get("posting_date")),
        "PCSO_NO": s_(doc.get("pcso_no")),
        "PCSO_MONTH": s_(doc.get("pcso_month")),
        "CUSTOMER": s_(doc.get("state_food_department")),
        "AGENCY": s_(doc.get("agency")),
        "INOTE": s_(doc.get("inote")),
        "QTY": s_(doc.get("billed_qty")),
        "DOCKET_NO": s_(titles.get(f"Bill Docketing::{docket_raw}") or docket_raw),
        "DOCKET_DATE": s_(doc.get("bill_rec_date")),
        "REJ_REASON": s_(doc.get("rejection_reason")),
    }

    # Approval levels: keep the latest row for each level (a bill can pass a
    # level more than once after rejection and resubmission)
    levels = {}
    for row in doc.get("status_of_the_bill_and_remarks") or []:
        m = re.search(r"(\d)", s_(row.get("bill_status")))
        if m:
            levels[m.group(1)] = row
    for n in ("1", "2", "3"):
        row = levels.get(n, {})
        rec[f"L{n}_RECEIVED"] = s_(row.get("bill_received_date"))
        rec[f"L{n}_VERIFIED"] = dt_min(row.get("bill_approved_date"))
        rec[f"L{n}_REMARKS"] = s_(row.get("remarks"))
        rec[f"L{n}_REJ_REASON"] = s_(doc.get(f"rejection_reason_level_{n}"))

    # Payment phases
    phases = {}
    for row in doc.get("payment_details") or []:
        m = re.search(r"(\d)", s_(row.get("payment_phase")))
        if m:
            phases[m.group(1)] = row
    for n in ("1", "2"):
        row = phases.get(n, {})
        rec[f"P{n}_PCT"] = s_(row.get("payment_percentage"))
        rec[f"P{n}_AMT"] = s_(row.get("payable_amount"))
        rec[f"P{n}_PAID"] = s_(row.get("paid_amount"))
        rec[f"P{n}_STATUS"] = s_(row.get("status_of_payment"))
        rec[f"P{n}_HOLD"] = s_(row.get("bill_holding_status"))
        rec[f"P{n}_DATE"] = s_(row.get("date_of_payment"))
        rec[f"P{n}_UTR"] = s_(row.get("utr_no"))

    rec["_modified"] = s_(doc.get("modified"))  # bookkeeping, not compared
    return rec


def visible(rec):
    return {k: v for k, v in rec.items() if not k.startswith("_")}


# ---------------------------------------------------------------- per mill
def check_mill(user, pw, old):
    """Return the mill's current {bill: record}, re-fetching only what changed."""
    s = login(user, pw)
    listing = list_bills(s)
    new = {}
    for name, modified in listing.items():
        prev = old.get(name)
        if prev and prev.get("_modified") == s_(modified):
            new[name] = prev  # unchanged since last run
            continue
        doc, titles = fetch_bill(s, name)
        new[name] = shape(doc, titles)
    return new


def safe_name(name):
    return re.sub(r"[^A-Za-z0-9_-]+", "_", name)


def read_json(path, default):
    try:
        return json.loads(path.read_text())
    except Exception:
        return default


def write_json(path, data):
    path.write_text(json.dumps(data, indent=1, sort_keys=True, ensure_ascii=False))


def main():
    accounts = load_accounts()
    SNAP_DIR.mkdir(exist_ok=True)
    now = datetime.now(timezone.utc).isoformat(timespec="seconds")

    status = read_json(STATUS_FILE, {"last_checked": None, "mills": {}})
    status["last_checked"] = now
    mills = status.setdefault("mills", {})
    for gone in set(mills) - {a["name"] for a in accounts}:
        del mills[gone]

    failures = []
    for acc in accounts:
        name = acc["name"]
        snap = SNAP_DIR / f"{safe_name(name)}.json"
        ms = mills.setdefault(name, {})
        ms["file"] = safe_name(name)
        old = read_json(snap, {})
        print(f"-- {name} --")
        try:
            new = check_mill(acc["user"], acc["pass"], old)
        except Exception as e:
            msg = str(e)
            if "timed out" in msg.lower() or "max retries" in msg.lower():
                msg = "Portal did not respond (connection timed out)"
            print(f"   ERROR: {msg}")
            failures.append(name)
            ms["ok"] = False
            ms["error"] = msg[:300]
            continue

        ms.update(ok=True, error=None, bills=len(new), last_success=now)
        changed = {k: visible(v) for k, v in old.items()} != {k: visible(v) for k, v in new.items()}
        if not snap.exists():
            print(f"   baseline: {len(new)} bills")
        elif changed:
            write_json(SNAP_DIR / f"{safe_name(name)}.prev.json", old)
            ms["last_changed"] = now
            print(f"   changes saved ({len(new)} bills)")
        else:
            print(f"   no changes ({len(new)} bills)")
        write_json(snap, new)

    write_json(STATUS_FILE, status)
    if failures:
        print("Failed: " + ", ".join(failures), file=sys.stderr)
        # only fail the run if every mill failed; partial data is still saved
        if len(failures) == len(accounts):
            sys.exit(1)


if __name__ == "__main__":
    main()
