import os
import json
import requests
import smtplib
from email.mime.text import MIMEText
from email.mime.multipart import MIMEMultipart
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

# Separate snapshot — completely independent of the automated monitor
SNAPSHOT_FILE = "manual_last_seen.json"

FIELDS = [
    "name",
    "total_qty",
    "indentor",
    "inspection_agency",
    "modified",
    "creation",
]

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
    new_orders = []
    for order in orders:
        key = f"{mill_name}::{order['name']}"
        if key not in snapshot:
            new_orders.append(order)
    return new_orders

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

# ── Plain Text (WhatsApp friendly) ───────────────────────────────────────────
def build_plain_text(mills_data, failed_mills):
    now   = datetime.now().strftime("%d %b %Y, %I:%M %p")
    lines = []
    lines.append(f"New PCSO Orders - {now} IST")
    lines.append("")

    if not mills_data and not failed_mills:
        lines.append("No new orders found across all mills.")
        return "\n".join(lines)

    for mill_name, orders in mills_data:
        lines.append(f"{mill_name} ({len(orders)} new order(s))")
        lines.append("-" * 30)
        for o in orders:
            lines.append(f"Order:             {o['name']}")
            lines.append(f"Qty:               {fmt_qty(o.get('total_qty'))}")
            lines.append(f"Customer Group:    {fmt_val(o.get('indentor'))}")
            lines.append(f"Inspection Agency: {fmt_val(o.get('inspection_agency'))}")
            lines.append("")

    if failed_mills:
        for entry in failed_mills:
            lines.append(f"{entry['mill']['name']} - ERROR: Could not fetch data.")
            lines.append("")

    lines.append(f"Portal: {PORTAL_URL}/app/pcso")
    return "\n".join(lines)

# ── HTML Email ────────────────────────────────────────────────────────────────
def build_html(mills_data, failed_mills, plain_text):
    now = datetime.now().strftime("%d %b %Y, %I:%M %p")

    mill_sections = ""

    if not mills_data and not failed_mills:
        mill_sections = """
        <div style="background:#fff;border:1px solid #e5e7eb;border-radius:6px;
                    padding:24px;text-align:center;color:#6b7280;font-size:14px;">
          No new orders found across all mills.
        </div>"""
    else:
        for mill_name, orders in mills_data:
            order_rows = ""
            for o in orders:
                order_rows += f"""
                <tr>
                  <td style="padding:10px 12px;border-bottom:1px solid #e5e7eb;
                             font-family:monospace;font-size:13px;color:#1d4ed8;">
                    {o['name']}
                  </td>
                  <td style="padding:10px 12px;border-bottom:1px solid #e5e7eb;
                             font-size:13px;color:#374151;font-weight:600;">
                    {fmt_qty(o.get('total_qty'))}
                  </td>
                  <td style="padding:10px 12px;border-bottom:1px solid #e5e7eb;
                             font-size:13px;color:#374151;">
                    {fmt_val(o.get('indentor'))}
                  </td>
                  <td style="padding:10px 12px;border-bottom:1px solid #e5e7eb;
                             font-size:13px;color:#374151;">
                    {fmt_val(o.get('inspection_agency'))}
                  </td>
                </tr>"""

            mill_sections += f"""
            <div style="margin-bottom:24px;">
              <div style="background:#14532d;padding:10px 16px;border-radius:6px 6px 0 0;">
                <span style="color:#fff;font-weight:700;font-size:14px;">{mill_name}</span>
                <span style="color:rgba(255,255,255,0.75);font-size:12px;margin-left:8px;">
                  {len(orders)} new order(s)
                </span>
              </div>
              <table style="width:100%;border-collapse:collapse;font-size:13px;
                            border:1px solid #e5e7eb;border-top:none;background:#fff;">
                <thead>
                  <tr style="background:#f9fafb;">
                    <th style="padding:8px 12px;text-align:left;color:#6b7280;
                               border-bottom:2px solid #e5e7eb;font-weight:600;">Order ID</th>
                    <th style="padding:8px 12px;text-align:left;color:#6b7280;
                               border-bottom:2px solid #e5e7eb;font-weight:600;">Order Qty</th>
                    <th style="padding:8px 12px;text-align:left;color:#6b7280;
                               border-bottom:2px solid #e5e7eb;font-weight:600;">Customer Group</th>
                    <th style="padding:8px 12px;text-align:left;color:#6b7280;
                               border-bottom:2px solid #e5e7eb;font-weight:600;">Inspection Agency</th>
                  </tr>
                </thead>
                <tbody>{order_rows}</tbody>
              </table>
            </div>"""

        for entry in failed_mills:
            mill_sections += f"""
            <div style="margin-bottom:24px;">
              <div style="background:#6b7280;padding:10px 16px;border-radius:6px 6px 0 0;">
                <span style="color:#fff;font-weight:700;font-size:14px;">
                  {entry['mill']['name']}
                </span>
                <span style="color:rgba(255,255,255,0.75);font-size:12px;margin-left:8px;">
                  Data unavailable
                </span>
              </div>
              <div style="border:1px solid #e5e7eb;border-top:none;background:#fff;
                          border-radius:0 0 6px 6px;padding:14px 16px;">
                <span style="background:#fee2e2;color:#dc2626;padding:2px 8px;
                             border-radius:3px;font-size:11px;font-weight:600;">ERROR</span>
                <span style="color:#374151;font-size:13px;margin-left:8px;">
                  Could not fetch data — please check portal manually.
                </span>
              </div>
            </div>"""

    return f"""<!DOCTYPE html>
<html>
<body style="margin:0;padding:20px;background:#f3f4f6;font-family:Arial,sans-serif;">
  <div style="max-width:680px;margin:auto;">

    <div style="background:#14532d;padding:20px 24px;border-radius:8px 8px 0 0;">
      <h2 style="margin:0;color:#fff;font-size:18px;">New PCSO Orders Report</h2>
      <p style="margin:4px 0 0;color:#86efac;font-size:13px;">
        {now} IST &nbsp;|&nbsp; Manually triggered
      </p>
    </div>

    <div style="background:#f3f4f6;padding:16px 0;">
      {mill_sections}
    </div>

    <!-- WhatsApp copy box -->
    <div style="background:#fff;border:1px solid #e5e7eb;border-radius:6px;
                padding:14px 16px;margin-bottom:16px;">
      <p style="margin:0 0 8px;font-size:12px;font-weight:600;color:#374151;">
        Copy for WhatsApp:
      </p>
      <pre style="margin:0;font-size:12px;color:#374151;white-space:pre-wrap;
                  font-family:monospace;background:#f9fafb;padding:10px;
                  border-radius:4px;border:1px solid #e5e7eb;">{plain_text}</pre>
    </div>

    <div style="text-align:center;">
      <a href="{PORTAL_URL}/app/pcso"
         style="background:#14532d;color:#fff;padding:10px 24px;border-radius:5px;
                text-decoration:none;font-size:13px;font-weight:600;">
        Open JuteSmart Portal
      </a>
    </div>

    <p style="color:#9ca3af;font-size:11px;margin-top:16px;text-align:center;">
      New Orders Report — manually triggered
    </p>
  </div>
</body>
</html>"""

# ── Send Email ────────────────────────────────────────────────────────────────
def send_email(mills_data, failed_mills):
    total      = sum(len(orders) for _, orders in mills_data)
    mill_names = ", ".join(n for n, _ in mills_data)
    plain_text = build_plain_text(mills_data, failed_mills)

    if total > 0:
        subject = f"New PCSO Orders: {total} order(s) [{mill_names}] — {datetime.now().strftime('%d %b %Y')}"
    else:
        subject = f"New PCSO Orders: No new orders — {datetime.now().strftime('%d %b %Y')}"

    html = build_html(mills_data, failed_mills, plain_text)

    msg = MIMEMultipart("alternative")
    msg["Subject"] = subject
    msg["From"]    = GMAIL_SENDER
    msg["To"]      = ", ".join(ALERT_EMAILS)
    msg.attach(MIMEText(plain_text, "plain"))
    msg.attach(MIMEText(html, "html"))

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

    # Always send email regardless — new orders, no orders, or errors
    send_email(mills_data, failed_mills)

    # Only update snapshot if at least one mill succeeded
    if all_orders_by_mill:
        save_snapshot(all_orders_by_mill)
        print("\nSnapshot (manual_last_seen.json) updated.")
    else:
        print("\nNo mills succeeded — snapshot not updated.")

if __name__ == "__main__":
    main()
