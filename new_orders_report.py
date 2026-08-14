import os
import json
import requests
import smtplib
from email.mime.text import MIMEText
from datetime import datetime

# ── Config ───────────────────────────────────────────────────────────────────
PORTAL_URL = "https://smart.jutecomm.gov.in"

MILLS = [
    {"name": "KCL",       "username": os.environ["JUTECOMM_USERNAME_1"], "password": os.environ["JUTECOMM_PASSWORD_1"]},
    {"name": "KJPL",      "username": os.environ["JUTECOMM_USERNAME_2"], "password": os.environ["JUTECOMM_PASSWORD_2"]},
    {"name": "Tepcon",    "username": os.environ["JUTECOMM_USERNAME_3"], "password": os.environ["JUTECOMM_PASSWORD_3"]},
    {"name": "Kaliaganj", "username": os.environ["JUTECOMM_USERNAME_4"], "password": os.environ["JUTECOMM_PASSWORD_4"]},
]

GMAIL_SENDER = os.environ["GMAIL_SENDER"]
GMAIL_PASS   = os.environ["GMAIL_APP_PASSWORD"]
ALERT_EMAILS = [e.strip() for e in os.environ["ALERT_EMAIL"].split(",") if e.strip()]

SNAPSHOT_FILE = "manual_last_seen.json"

FIELDS = ["name", "total_qty", "indentor", "inspection_agency", "creation"]

# ── Frappe Login ──────────────────────────────────────────────────────────────
def get_session(username, password):
    session = requests.Session()
    session.headers.update({
        "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36",
        "Expect":     "",
        "X-Frappe-CSRF-Token": "fetch",
    })
    resp = session.post(
        f"{PORTAL_URL}/api/method/login",
        data={"usr": username, "pwd": password},
        timeout=30,
    )
    resp.raise_for_status()
    result = resp.json()
    if result.get("message") != "Logged In":
        raise Exception(f"Login failed: {result}")
    csrf_token = session.cookies.get("csrf_token")
    if csrf_token:
        session.headers.update({"X-Frappe-CSRF-Token": csrf_token})
    return session

# ── Fetch Orders ──────────────────────────────────────────────────────────────
def fetch_orders(session):
    params = {
        "fields":            json.dumps(FIELDS),
        "order_by":          "creation desc",
        "limit_page_length": 50,
    }
    resp = session.get(
        f"{PORTAL_URL}/api/resource/PCSO",
        params=params,
        timeout=30,
    )
    resp.raise_for_status()
    return resp.json().get("data", [])

# ── Snapshot ──────────────────────────────────────────────────────────────────
def load_snapshot():
    if os.path.exists(SNAPSHOT_FILE):
        with open(SNAPSHOT_FILE, "r") as f:
            return json.load(f)
    return {}

def save_snapshot(all_orders_by_mill):
    snapshot = {}
    for mill_name, orders in all_orders_by_mill.items():
        for o in orders:
            key = f"{mill_name}::{o['name']}"
            snapshot[key] = o.get("creation", "")
    with open(SNAPSHOT_FILE, "w") as f:
        json.dump(snapshot, f, indent=2)

# ── Find New Orders ───────────────────────────────────────────────────────────
def find_new_orders(mill_name, orders, snapshot):
    return [o for o in orders if f"{mill_name}::{o['name']}" not in snapshot]

# ── Format Helpers ────────────────────────────────────────────────────────────
def fmt_qty(value):
    if value is None or value == "":
        return "-"
    try:
        return f"{int(float(value)):,} bales"
    except (ValueError, TypeError):
        return str(value)

def fmt_val(value):
    if value is None or value == "" or value == "None":
        return "-"
    return str(value)

# ── Build plain text ──────────────────────────────────────────────────────────
def build_plain_text(mills_data, failed_mills):
    today = datetime.now().strftime("%d %b %Y")
    lines = []
    lines.append(f"New PCSO Orders - {today}")
    lines.append("")

    if not mills_data and not failed_mills:
        lines.append("No new orders found across all mills.")
        return "\n".join(lines)

    for mill_name, orders in mills_data:
        lines.append(f"{mill_name} ({len(orders)} new order(s))")
        lines.append("-" * 30)
        for o in orders:
            lines.append(f"Customer: {fmt_val(o.get('indentor'))}")
            lines.append(f"Qty: {fmt_qty(o.get('total_qty'))}")
            lines.append(f"Inspection Agency: {fmt_val(o.get('inspection_agency'))}")
            lines.append("")

    if failed_mills:
        for entry in failed_mills:
            lines.append(f"{entry['mill']['name']} - ERROR: Could not fetch data.")
            lines.append("")

    return "\n".join(lines)

# ── Send Email ────────────────────────────────────────────────────────────────
def send_email(mills_data, failed_mills):
    total      = sum(len(orders) for _, orders in mills_data)
    mill_names = ", ".join(n for n, _ in mills_data)
    plain_text = build_plain_text(mills_data, failed_mills)

    if total > 0:
        subject = f"New PCSO Orders: {total} order(s) [{mill_names}] — {datetime.now().strftime('%d %b %Y')}"
    else:
        subject = f"New PCSO Orders: No new orders — {datetime.now().strftime('%d %b %Y')}"

    msg = MIMEText(plain_text, "plain")
    msg["Subject"] = subject
    msg["From"]    = GMAIL_SENDER
    msg["To"]      = ", ".join(ALERT_EMAILS)

    with smtplib.SMTP_SSL("smtp.gmail.com", 465) as smtp:
        smtp.login(GMAIL_SENDER, GMAIL_PASS)
        smtp.sendmail(GMAIL_SENDER, ALERT_EMAILS, msg.as_string())

    print(f"Report sent to {len(ALERT_EMAILS)} recipient(s): {', '.join(ALERT_EMAILS)}")

# ── Main ──────────────────────────────────────────────────────────────────────
def main():
    print(f"\n{'='*50}")
    print(f"New Orders Report — {datetime.now().strftime('%d %b %Y %H:%M:%S')}")
    print(f"{'='*50}")

    snapshot           = load_snapshot()
    all_orders_by_mill = {}
    mills_data         = []
    failed_mills       = []

    for mill in MILLS:
        print(f"\n-- {mill['name']} --")
        try:
            session    = get_session(mill["username"], mill["password"])
            orders     = fetch_orders(session)
            print(f"Fetched {len(orders)} orders")
            all_orders_by_mill[mill["name"]] = orders
            new_orders = find_new_orders(mill["name"], orders, snapshot)
            print(f"{len(new_orders)} new order(s) found")
            if new_orders:
                mills_data.append((mill["name"], new_orders))
        except Exception as e:
            error_msg = str(e)
            print(f"ERROR for {mill['name']}: {error_msg}")
            failed_mills.append({"mill": mill, "error": error_msg})

    send_email(mills_data, failed_mills)

    if all_orders_by_mill:
        save_snapshot(all_orders_by_mill)
        print("\nSnapshot updated.")
    else:
        print("\nNo mills succeeded — snapshot not updated.")

if __name__ == "__main__":
    main()
