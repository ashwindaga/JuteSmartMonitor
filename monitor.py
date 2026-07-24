import os
import json
import requests
import smtplib
from email.mime.text import MIMEText
from email.mime.multipart import MIMEMultipart
from datetime import datetime

# ── Config from GitHub Secrets ──────────────────────────────────────────────
PORTAL_URL    = "https://smart.jutecomm.gov.in"
PORTAL_USER   = os.environ["JUTECOMM_USERNAME"]
PORTAL_PASS   = os.environ["JUTECOMM_PASSWORD"]

GMAIL_SENDER  = os.environ["GMAIL_SENDER"]       # your Gmail address
GMAIL_PASS    = os.environ["GMAIL_APP_PASSWORD"]  # Gmail App Password (not your login password)
ALERT_EMAIL   = os.environ["ALERT_EMAIL"]         # where to send alerts (can be same as sender)

SNAPSHOT_FILE = "last_seen_orders.json"

# ── Frappe Login ─────────────────────────────────────────────────────────────
def get_session():
    """Log in and return an authenticated session."""
    session = requests.Session()
    resp = session.post(
        f"{PORTAL_URL}/api/method/login",
        data={"usr": PORTAL_USER, "pwd": PORTAL_PASS},
        timeout=30,
    )
    resp.raise_for_status()
    result = resp.json()
    if result.get("message") != "Logged In":
        raise Exception(f"Login failed: {result}")
    print("✅ Logged in successfully")
    return session

# ── Fetch Orders via Frappe REST API ─────────────────────────────────────────
def fetch_orders(session):
    """
    Fetch PCSO (Purchase / Supply Orders) from the Frappe API.
    Frappe's list API: /api/resource/<DocType>
    Adjust fields and filters below based on what your portal shows.
    """
    params = {
        "fields": '["name","status","supplier","grand_total","transaction_date","modified"]',
        "order_by": "modified desc",
        "limit_page_length": 50,
        # Uncomment and adjust if you only want specific statuses:
        # "filters": '[["status","in",["To Receive and Bill","To Bill","Submitted"]]]',
    }
    resp = session.get(
        f"{PORTAL_URL}/api/resource/PCSO",
        params=params,
        timeout=30,
    )

    # If 'PCSO' isn't the exact DocType name, try these fallbacks:
    if resp.status_code == 404:
        for doctype in ["Purchase Order", "Sales Order", "Jute Order", "PCSO Order"]:
            resp = session.get(
                f"{PORTAL_URL}/api/resource/{doctype.replace(' ', '%20')}",
                params=params,
                timeout=30,
            )
            if resp.status_code == 200:
                print(f"ℹ️  Found orders under DocType: '{doctype}'")
                break

    resp.raise_for_status()
    orders = resp.json().get("data", [])
    print(f"📋 Fetched {len(orders)} orders from portal")
    return orders

# ── Compare with Snapshot ────────────────────────────────────────────────────
def load_snapshot():
    if os.path.exists(SNAPSHOT_FILE):
        with open(SNAPSHOT_FILE, "r") as f:
            return json.load(f)
    return {}

def save_snapshot(orders):
    snapshot = {o["name"]: o.get("modified", "") for o in orders}
    with open(SNAPSHOT_FILE, "w") as f:
        json.dump(snapshot, f, indent=2)

def find_new_orders(orders, snapshot):
    """Returns list of orders that are new or have changed status since last run."""
    new_or_changed = []
    for order in orders:
        name = order["name"]
        if name not in snapshot:
            order["_change_type"] = "NEW"
            new_or_changed.append(order)
        elif order.get("modified", "") != snapshot[name]:
            order["_change_type"] = "UPDATED"
            new_or_changed.append(order)
    return new_or_changed

# ── Send Email Alert ─────────────────────────────────────────────────────────
def send_email(new_orders):
    subject = f"🚨 JuteSmart Alert: {len(new_orders)} New/Updated PCSO Order(s)"

    # Build HTML email body
    rows = ""
    for o in new_orders:
        badge_color = "#2ecc71" if o["_change_type"] == "NEW" else "#f39c12"
        rows += f"""
        <tr>
            <td style="padding:8px;border-bottom:1px solid #eee;">
                <span style="background:{badge_color};color:white;padding:2px 8px;
                             border-radius:4px;font-size:12px;">{o['_change_type']}</span>
            </td>
            <td style="padding:8px;border-bottom:1px solid #eee;">
                <a href="{PORTAL_URL}/app/pcso/{o['name']}" style="color:#2c3e50;">
                    {o['name']}
                </a>
            </td>
            <td style="padding:8px;border-bottom:1px solid #eee;">{o.get('status','—')}</td>
            <td style="padding:8px;border-bottom:1px solid #eee;">{o.get('supplier','—')}</td>
            <td style="padding:8px;border-bottom:1px solid #eee;">
                ₹{float(o.get('grand_total',0)):,.2f}
            </td>
            <td style="padding:8px;border-bottom:1px solid #eee;">{o.get('transaction_date','—')}</td>
        </tr>"""

    html = f"""
    <html><body style="font-family:Arial,sans-serif;color:#333;max-width:700px;margin:auto;">
        <div style="background:#1a5276;padding:16px;border-radius:6px 6px 0 0;">
            <h2 style="color:white;margin:0;">🌿 JuteSmart Order Alert</h2>
            <p style="color:#aed6f1;margin:4px 0 0;">{datetime.now().strftime('%d %b %Y, %I:%M %p')}</p>
        </div>
        <div style="border:1px solid #ddd;border-top:none;padding:16px;border-radius:0 0 6px 6px;">
            <p>{len(new_orders)} order(s) detected as <strong>new or updated</strong> on the JuteSmart portal.</p>
            <table style="width:100%;border-collapse:collapse;">
                <thead>
                    <tr style="background:#f2f3f4;">
                        <th style="padding:8px;text-align:left;">Change</th>
                        <th style="padding:8px;text-align:left;">Order ID</th>
                        <th style="padding:8px;text-align:left;">Status</th>
                        <th style="padding:8px;text-align:left;">Supplier</th>
                        <th style="padding:8px;text-align:left;">Amount</th>
                        <th style="padding:8px;text-align:left;">Date</th>
                    </tr>
                </thead>
                <tbody>{rows}</tbody>
            </table>
            <br>
            <a href="{PORTAL_URL}/app/pcso"
               style="background:#1a5276;color:white;padding:10px 20px;
                      border-radius:4px;text-decoration:none;display:inline-block;">
                Open JuteSmart Portal →
            </a>
        </div>
        <p style="color:#aaa;font-size:11px;margin-top:12px;">
            This is an automated alert. Monitoring runs every 15 minutes via GitHub Actions.
        </p>
    </body></html>"""

    msg = MIMEMultipart("alternative")
    msg["Subject"] = subject
    msg["From"]    = GMAIL_SENDER
    msg["To"]      = ALERT_EMAIL
    msg.attach(MIMEText(html, "html"))

    with smtplib.SMTP_SSL("smtp.gmail.com", 465) as smtp:
        smtp.login(GMAIL_SENDER, GMAIL_PASS)
        smtp.sendmail(GMAIL_SENDER, ALERT_EMAIL, msg.as_string())

    print(f"📧 Alert email sent to {ALERT_EMAIL}")

# ── Main ─────────────────────────────────────────────────────────────────────
def main():
    print(f"\n{'='*50}")
    print(f"JuteSmart Monitor — {datetime.now().strftime('%d %b %Y %H:%M:%S')}")
    print(f"{'='*50}")

    session     = get_session()
    orders      = fetch_orders(session)
    snapshot    = load_snapshot()
    new_orders  = find_new_orders(orders, snapshot)

    if new_orders:
        print(f"🔔 {len(new_orders)} new/updated order(s) found!")
        send_email(new_orders)
    else:
        print("✅ No new orders. Nothing to alert.")

    save_snapshot(orders)
    print("💾 Snapshot updated.\n")

if __name__ == "__main__":
    main()
