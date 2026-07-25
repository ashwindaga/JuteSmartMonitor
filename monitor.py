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
    {
        "name":     "KCL",
        "username": os.environ["JUTECOMM_USERNAME_1"],
        "password": os.environ["JUTECOMM_PASSWORD_1"],
        "color":    "#1d4ed8",   # blue
    },
    {
        "name":     "KJPL",
        "username": os.environ["JUTECOMM_USERNAME_2"],
        "password": os.environ["JUTECOMM_PASSWORD_2"],
        "color":    "#7c3aed",   # purple
    },
    {
        "name":     "Tepcon",
        "username": os.environ["JUTECOMM_USERNAME_3"],
        "password": os.environ["JUTECOMM_PASSWORD_3"],
        "color":    "#b45309",   # amber
    },
    {
        "name":     "Kaliaganj",
        "username": os.environ["JUTECOMM_USERNAME_4"],
        "password": os.environ["JUTECOMM_PASSWORD_4"],
        "color":    "#0f766e",   # teal
    },
]

GMAIL_SENDER = os.environ["GMAIL_SENDER"]
GMAIL_PASS   = os.environ["GMAIL_APP_PASSWORD"]
ALERT_EMAILS = [e.strip() for e in os.environ["ALERT_EMAIL"].split(",") if e.strip()]

SNAPSHOT_FILE = "last_seen_orders.json"

FIELDS = [
    "name", "pcso_date", "total_qty",
    "indentor", "agency", "indentor_code",
    "status", "modified", "creation",
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
        "fields":           json.dumps(FIELDS),
        "order_by":         "modified desc",
        "limit_page_length": 50,
    }
    resp = session.get(
        f"{PORTAL_URL}/api/resource/PCSO",
        params=params,
        timeout=30,
    )
    resp.raise_for_status()
    return resp.json().get("data", [])

# ── Snapshot Helpers ──────────────────────────────────────────────────────────
def load_snapshot():
    if os.path.exists(SNAPSHOT_FILE):
        with open(SNAPSHOT_FILE, "r") as f:
            return json.load(f)
    return {}

def save_snapshot(all_orders_by_mill):
    """Flatten all mills into one snapshot dict keyed by mill+order_name."""
    snapshot = {}
    for mill_name, orders in all_orders_by_mill.items():
        for o in orders:
            key = f"{mill_name}::{o['name']}"
            snapshot[key] = o.get("modified", o.get("creation", ""))
    with open(SNAPSHOT_FILE, "w") as f:
        json.dump(snapshot, f, indent=2)

def find_new_orders(mill_name, orders, snapshot):
    new_or_changed = []
    for order in orders:
        key        = f"{mill_name}::{order['name']}"
        current_ts = order.get("modified", order.get("creation", ""))
        if key not in snapshot:
            order["_change_type"] = "NEW"
            new_or_changed.append(order)
        elif current_ts != snapshot[key]:
            order["_change_type"] = "UPDATED"
            new_or_changed.append(order)
    return new_or_changed

# ── Email ─────────────────────────────────────────────────────────────────────
def build_mill_section(mill, new_orders):
    """Build an HTML table block for one mill."""
    rows = ""
    for o in new_orders:
        is_new       = o["_change_type"] == "NEW"
        badge_color  = "#1a7a4a" if is_new else "#b45309"
        badge_label  = "NEW" if is_new else "UPDATED"
        qty          = o.get("total_qty")
        qty_str      = f"{int(qty):,} bales" if qty is not None else "—"
        order_url    = f"{PORTAL_URL}/app/pcso/{o['name']}"

        rows += f"""
        <tr>
          <td style="padding:10px;border-bottom:1px solid #e5e7eb;white-space:nowrap;">
            <span style="background:{badge_color};color:#fff;padding:2px 8px;
                         border-radius:3px;font-size:11px;font-weight:600;">{badge_label}</span>
          </td>
          <td style="padding:10px;border-bottom:1px solid #e5e7eb;">
            <a href="{order_url}"
               style="color:#1d4ed8;font-weight:600;text-decoration:none;
                      font-family:monospace;font-size:13px;">{o['name']}</a>
          </td>
          <td style="padding:10px;border-bottom:1px solid #e5e7eb;color:#374151;">
            {o.get('pcso_date') or '—'}
          </td>
          <td style="padding:10px;border-bottom:1px solid #e5e7eb;color:#374151;font-weight:600;">
            {qty_str}
          </td>
          <td style="padding:10px;border-bottom:1px solid #e5e7eb;color:#374151;">
            {o.get('indentor') or '—'}
          </td>
          <td style="padding:10px;border-bottom:1px solid #e5e7eb;color:#374151;">
            {o.get('agency') or '—'}
          </td>
          <td style="padding:10px;border-bottom:1px solid #e5e7eb;color:#374151;">
            {o.get('indentor_code') or '—'}
          </td>
        </tr>"""

    return f"""
    <div style="margin-bottom:24px;">
      <!-- Mill header -->
      <div style="background:{mill['color']};padding:10px 16px;border-radius:6px 6px 0 0;">
        <span style="color:#fff;font-weight:700;font-size:14px;">{mill['name']}</span>
        <span style="color:rgba(255,255,255,0.75);font-size:12px;margin-left:8px;">
          {len(new_orders)} order(s)
        </span>
      </div>
      <table style="width:100%;border-collapse:collapse;font-size:13px;
                    border:1px solid #e5e7eb;border-top:none;">
        <thead>
          <tr style="background:#f9fafb;">
            <th style="padding:8px 10px;text-align:left;color:#6b7280;
                       border-bottom:2px solid #e5e7eb;font-weight:600;"></th>
            <th style="padding:8px 10px;text-align:left;color:#6b7280;
                       border-bottom:2px solid #e5e7eb;font-weight:600;">Order ID</th>
            <th style="padding:8px 10px;text-align:left;color:#6b7280;
                       border-bottom:2px solid #e5e7eb;font-weight:600;">PCSO Date</th>
            <th style="padding:8px 10px;text-align:left;color:#6b7280;
                       border-bottom:2px solid #e5e7eb;font-weight:600;">Order Qty.</th>
            <th style="padding:8px 10px;text-align:left;color:#6b7280;
                       border-bottom:2px solid #e5e7eb;font-weight:600;">Customer Group</th>
            <th style="padding:8px 10px;text-align:left;color:#6b7280;
                       border-bottom:2px solid #e5e7eb;font-weight:600;">Agency</th>
            <th style="padding:8px 10px;text-align:left;color:#6b7280;
                       border-bottom:2px solid #e5e7eb;font-weight:600;">State Dept. Code</th>
          </tr>
        </thead>
        <tbody>{rows}</tbody>
      </table>
    </div>"""

def send_email(mills_with_orders):
    total = sum(len(orders) for _, orders in mills_with_orders)
    mill_names = ", ".join(m for m, _ in mills_with_orders)
    subject = (
        f"JuteSmart Alert: {total} New/Updated Order(s) "
        f"[{mill_names}] — {datetime.now().strftime('%d %b %Y')}"
    )

    sections = ""
    for mill, new_orders in mills_with_orders:
        sections += build_mill_section(mill, new_orders)

    html = f"""<!DOCTYPE html>
<html>
<body style="margin:0;padding:20px;background:#f3f4f6;font-family:Arial,sans-serif;">
  <div style="max-width:820px;margin:auto;">

    <!-- Header -->
    <div style="background:#14532d;padding:20px 24px;border-radius:8px 8px 0 0;">
      <h2 style="margin:0;color:#fff;font-size:18px;">JuteSmart PCSO Order Alert</h2>
      <p style="margin:4px 0 0;color:#86efac;font-size:13px;">
        {datetime.now().strftime('%d %b %Y, %I:%M %p')} IST
        &nbsp;|&nbsp; {total} order(s) across {len(mills_with_orders)} mill(s)
      </p>
    </div>

    <!-- Mill sections -->
    <div style="background:#f3f4f6;padding:16px 0;">
      {sections}
    </div>

    <!-- Open portal button -->
    <div style="text-align:center;margin-top:8px;">
      <a href="{PORTAL_URL}/app/pcso"
         style="background:#14532d;color:#fff;padding:10px 24px;border-radius:5px;
                text-decoration:none;font-size:13px;font-weight:600;">
        Open JuteSmart Portal
      </a>
    </div>

    <p style="color:#9ca3af;font-size:11px;margin-top:16px;text-align:center;">
      Automated alert — runs every 15 min (9am–9pm IST, Mon–Sat)
    </p>
  </div>
</body>
</html>"""

    msg = MIMEMultipart("alternative")
    msg["Subject"] = subject
    msg["From"]    = GMAIL_SENDER
    msg["To"]      = ", ".join(ALERT_EMAILS)
    msg.attach(MIMEText(html, "html"))

    with smtplib.SMTP_SSL("smtp.gmail.com", 465) as smtp:
        smtp.login(GMAIL_SENDER, GMAIL_PASS)
        smtp.sendmail(GMAIL_SENDER, ALERT_EMAILS, msg.as_string())

    print(f"Alert sent to {len(ALERT_EMAILS)} recipient(s): {', '.join(ALERT_EMAILS)}")

# ── Main ──────────────────────────────────────────────────────────────────────
def main():
    print(f"\n{'='*50}")
    print(f"JuteSmart Monitor — {datetime.now().strftime('%d %b %Y %H:%M:%S')}")
    print(f"{'='*50}")

    snapshot          = load_snapshot()
    all_orders_by_mill = {}
    mills_with_orders  = []   # (mill_dict, [new_orders]) — only mills that have changes

    for mill in MILLS:
        print(f"\n-- {mill['name']} --")
        try:
            session = get_session(mill["username"], mill["password"])
            orders  = fetch_orders(session)
            print(f"Fetched {len(orders)} orders")
            all_orders_by_mill[mill["name"]] = orders
            new_orders = find_new_orders(mill["name"], orders, snapshot)
            if new_orders:
                print(f"{len(new_orders)} new/updated order(s) found")
                mills_with_orders.append((mill, new_orders))
            else:
                print("No new orders")
        except Exception as e:
            print(f"ERROR for {mill['name']}: {e}")

    if mills_with_orders:
        send_email(mills_with_orders)
    else:
        print("\nNo new orders across any mill. Nothing to alert.")

    save_snapshot(all_orders_by_mill)
    print("\nSnapshot updated.")

if __name__ == "__main__":
    main()
