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

# ALERT_EMAIL supports multiple addresses separated by commas
# e.g. "alice@gmail.com, bob@company.com, carol@gmail.com"
ALERT_EMAILS  = [e.strip() for e in os.environ["ALERT_EMAIL"].split(",") if e.strip()]

SNAPSHOT_FILE = "last_seen_orders.json"

# ── Frappe Login ─────────────────────────────────────────────────────────────
def get_session():
    """Log in and return an authenticated session."""
    session = requests.Session()

    # Fix for 417 error: disable the Expect: 100-continue header that some
    # servers reject, and set a browser-like User-Agent
    session.headers.update({
        "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36",
        "Expect": "",          # disable Expect: 100-continue
        "X-Frappe-CSRF-Token": "fetch",
    })

    resp = session.post(
        f"{PORTAL_URL}/api/method/login",
        data={"usr": PORTAL_USER, "pwd": PORTAL_PASS},
        timeout=30,
    )
    resp.raise_for_status()
    result = resp.json()
    if result.get("message") != "Logged In":
        raise Exception(f"Login failed: {result}")

    # Grab the CSRF token that Frappe sets after login (needed for API calls)
    csrf_token = session.cookies.get("csrf_token") or result.get("home_page", "")
    if csrf_token:
        session.headers.update({"X-Frappe-CSRF-Token": csrf_token})

    print("✅ Logged in successfully")
    return session

# ── Fetch Orders via Frappe REST API ─────────────────────────────────────────
def fetch_orders(session):
    """
    Fetch PCSO orders from the Frappe API.
    We first try the minimal fields (just name + modified) to confirm the
    DocType exists, then fetch full details.
    """
    # Try these DocType names in order until one works
    candidate_doctypes = [
        "PCSO",
        "Purchase Order",
        "Jute Order",
        "PCSO Order",
        "Sales Order",
        "Supplier Order",
    ]

    # Minimal params first — avoids field-name mismatches on first probe
    probe_params = {
        "fields": '["name","modified"]',
        "limit_page_length": 5,
    }

    working_doctype = None
    for doctype in candidate_doctypes:
        url = f"{PORTAL_URL}/api/resource/{requests.utils.quote(doctype)}"
        resp = session.get(url, params=probe_params, timeout=30)
        print(f"   Trying DocType '{doctype}' → HTTP {resp.status_code}")
        if resp.status_code == 200:
            working_doctype = doctype
            print(f"✅ DocType confirmed: '{doctype}'")
            break

    if not working_doctype:
        # Print the last response body to help diagnose
        print(f"❌ Could not find a working DocType. Last response: {resp.text[:500]}")
        raise Exception("No valid DocType found. Check portal access and DocType name.")

    # Now fetch with full fields
    full_params = {
        "fields": '["name","status","modified","creation"]',
        "order_by": "modified desc",
        "limit_page_length": 50,
    }
    url = f"{PORTAL_URL}/api/resource/{requests.utils.quote(working_doctype)}"
    resp = session.get(url, params=full_params, timeout=30)

    if resp.status_code != 200:
        # Fall back to minimal fields if the server rejects some field names
        print(f"⚠️  Full fields failed ({resp.status_code}), falling back to minimal fields")
        resp = session.get(url, params=probe_params, timeout=30)

    resp.raise_for_status()
    orders = resp.json().get("data", [])
    print(f"📋 Fetched {len(orders)} orders from portal")

    # ── DEBUG: print raw fields from the first order ──────────────────────────
    # This helps us see exactly what field names the API returns.
    # Safe to remove after we've confirmed the field names.
    if orders:
        print("\n--- DEBUG: Fields in first order ---")
        for key, value in orders[0].items():
            print(f"   {key}: {value}")
        print("--- END DEBUG ---\n")
    # ─────────────────────────────────────────────────────────────────────────

    return orders

# ── Compare with Snapshot ────────────────────────────────────────────────────
def load_snapshot():
    if os.path.exists(SNAPSHOT_FILE):
        with open(SNAPSHOT_FILE, "r") as f:
            return json.load(f)
    return {}

def save_snapshot(orders):
    # Store name → modified timestamp for change detection
    snapshot = {o["name"]: o.get("modified", o.get("creation", "")) for o in orders}
    with open(SNAPSHOT_FILE, "w") as f:
        json.dump(snapshot, f, indent=2)

def find_new_orders(orders, snapshot):
    """Returns list of orders that are new or have changed since last run."""
    new_or_changed = []
    for order in orders:
        name = order["name"]
        current_ts = order.get("modified", order.get("creation", ""))
        if name not in snapshot:
            order["_change_type"] = "NEW"
            new_or_changed.append(order)
        elif current_ts != snapshot[name]:
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
        # Safely format amount — field may not exist depending on API response
        amount = o.get("grand_total") or o.get("total") or o.get("amount")
        amount_str = f"₹{float(amount):,.2f}" if amount is not None else "—"
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
            <td style="padding:8px;border-bottom:1px solid #eee;">{amount_str}</td>
            <td style="padding:8px;border-bottom:1px solid #eee;">{o.get('transaction_date', o.get('creation','—'))}</td>
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
    msg["To"]      = ", ".join(ALERT_EMAILS)   # shows all recipients in the To: header
    msg.attach(MIMEText(html, "html"))

    with smtplib.SMTP_SSL("smtp.gmail.com", 465) as smtp:
        smtp.login(GMAIL_SENDER, GMAIL_PASS)
        # sendmail() accepts a list — delivers to every address individually
        smtp.sendmail(GMAIL_SENDER, ALERT_EMAILS, msg.as_string())

    print(f"📧 Alert email sent to {len(ALERT_EMAILS)} recipient(s): {', '.join(ALERT_EMAILS)}")

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
