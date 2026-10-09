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
    {"name": "KCL",       "username": os.environ["JUTECOMM_USERNAME_1"], "password": os.environ["JUTECOMM_PASSWORD_1"], "color": "#1d4ed8"},
    {"name": "KJPL",      "username": os.environ["JUTECOMM_USERNAME_2"], "password": os.environ["JUTECOMM_PASSWORD_2"], "color": "#7c3aed"},
    {"name": "Tepcon",    "username": os.environ["JUTECOMM_USERNAME_3"], "password": os.environ["JUTECOMM_PASSWORD_3"], "color": "#b45309"},
    {"name": "Kaliaganj", "username": os.environ["JUTECOMM_USERNAME_4"], "password": os.environ["JUTECOMM_PASSWORD_4"], "color": "#0f766e"},
    {"name": "GS",        "username": os.environ["JUTECOMM_USERNAME_5"], "password": os.environ["JUTECOMM_PASSWORD_5"], "color": "#be185d"},
]

GMAIL_SENDER = os.environ["GMAIL_SENDER"]
GMAIL_PASS   = os.environ["GMAIL_APP_PASSWORD"]
ALERT_EMAILS = [e.strip() for e in os.environ["ALERT_EMAIL"].split(",") if e.strip()]

SNAPSHOT_FILE = "last_seen_orders.json"

# ── All 39 tracked fields ─────────────────────────────────────────────────────
FIELD_META = {
    # Order Identity
    "status":                           {"label": "Status",                          "unit": ""},
    "pcso_date":                        {"label": "PCSO Date",                       "unit": ""},
    "pcso_month":                       {"label": "PCSO Month",                      "unit": ""},
    "crop_year":                        {"label": "Crop Year",                       "unit": ""},
    "marketing_season":                 {"label": "Marketing Season",                "unit": ""},
    "amendment_no":                     {"label": "Amendment No",                    "unit": ""},
    "amended_from":                     {"label": "Amended From",                    "unit": ""},
    # Agency & Indent
    "indentor":                         {"label": "Customer Group",                  "unit": ""},
    "indentor_code":                    {"label": "State Dept. Code",                "unit": ""},
    "agency":                           {"label": "Agency",                          "unit": ""},
    "agency_code":                      {"label": "Agency Code",                     "unit": ""},
    "indent_num":                       {"label": "Indent No.",                      "unit": ""},
    "indent_date":                      {"label": "Indent Date",                     "unit": ""},
    "bag_color":                        {"label": "Color Code",                      "unit": ""},
    "month":                            {"label": "Indent Month",                    "unit": ""},
    # Quantities
    "total_qty":                        {"label": "Order Qty",                       "unit": "bales"},
    "total_pcso_quantity":              {"label": "Total PCSO Qty",                  "unit": "bales"},
    "total_icall_quantity_in_bales":    {"label": "I-CALL Qty",                      "unit": "bales"},
    "total_reallocated_qty_in_bales":   {"label": "Reallocated Qty",                 "unit": "bales"},
    "inspected_qty":                    {"label": "Accepted Qty",                    "unit": "bales"},
    "rem_insp_qty":                     {"label": "Rejected Qty",                    "unit": "bales"},
    "total_pcso_remaining_quantiy":     {"label": "Pending Inspection Qty",          "unit": "bales"},
    "total_delivered_quantity":         {"label": "Total Accepted Qty",              "unit": "bales"},
    "total_rejected_quantity":          {"label": "Total Rejected Qty",              "unit": "bales"},
    "total_withdraw_qty":               {"label": "Withdrawn Qty",                   "unit": "bales"},
    "total_qty_dispatch_by_mill":       {"label": "Qty Dispatched by Mill",          "unit": "bales"},
    "total_qty_received_by_consignee":  {"label": "Qty Received by Consignee",       "unit": "bales"},
    "total_billed_qty_in_bales":        {"label": "Billed Qty",                      "unit": "bales"},
    "lot_size":                         {"label": "Container Capacity",              "unit": "bales"},
    # Dates
    "last_date_of_inspection":          {"label": "Last Date of Inspection",         "unit": ""},
    "last_date_of_despatch":            {"label": "Last Date of Dispatch",           "unit": ""},
    "pcso_last_date_of_inspection":     {"label": "PCSO Last Date of Inspection",    "unit": ""},
    "pcso_last_date_of_dispatch":       {"label": "PCSO Last Date of Dispatch",      "unit": ""},
    "tax_amendment_date":               {"label": "Amendment Date",                  "unit": ""},
    "submission_time":                  {"label": "Document Submission Time",        "unit": ""},
    # Financial
    "exfactory_price":                  {"label": "Ex-Factory Price (Per 100 Bags)", "unit": "Rs"},
    "total_billed_amount_in_rs":        {"label": "Billed Amount",                   "unit": "Rs"},
    "total_paid_amount_in_rs":          {"label": "Paid Amount",                     "unit": "Rs"},
    # Linkage & Logistics
    "jci_linkage":                      {"label": "JCI Linkage",                     "unit": ""},
    "jci_linkage_percentage":           {"label": "JCI Linkage %",                   "unit": ""},
    "pcso_allocation":                  {"label": "PCSO Allocation ID",              "unit": ""},
    "reallocated_pcso":                 {"label": "Reallocated PCSO",                "unit": ""},
    "pcso_reallocation_id":             {"label": "PCSO Reallocation ID",            "unit": ""},
    "reallocated_from":                 {"label": "Reallocated From PCSO",           "unit": ""},
    "mode_of_transport":                {"label": "Mode of Transport",               "unit": ""},
    "transport_mode":                   {"label": "Transport Mode",                  "unit": ""},
    "inspection_agency":                {"label": "Inspection Agency",               "unit": ""},
}

TRACKED_FIELDS = list(FIELD_META.keys()) + ["name", "modified", "creation"]
IGNORE_FOR_DETECTION = {"name", "modified", "creation"}

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
        "fields":            json.dumps(TRACKED_FIELDS),
        "order_by":          "modified desc",
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
    # Load existing snapshot first — preserve data for any mills that failed
    existing = load_snapshot()
    # Only update entries for mills that succeeded this run
    for mill_name, orders in all_orders_by_mill.items():
        for o in orders:
            key = f"{mill_name}::{o['name']}"
            existing[key] = {f: o.get(f) for f in TRACKED_FIELDS}
    with open(SNAPSHOT_FILE, "w") as f:
        json.dump(existing, f, indent=2)

# ── Change Detection ──────────────────────────────────────────────────────────
def find_changes(mill_name, orders, snapshot):
    results = []
    for order in orders:
        key      = f"{mill_name}::{order['name']}"
        old_data = snapshot.get(key)

        if old_data is None:
            order["_change_type"] = "NEW"
            order["_changes"]     = {}
            results.append(order)
            continue

        changes = {}
        for field in TRACKED_FIELDS:
            if field in IGNORE_FOR_DETECTION:
                continue
            old_val = old_data.get(field)
            new_val = order.get(field)
            if str(old_val) != str(new_val):
                changes[field] = {"old": old_val, "new": new_val}

        if changes:
            order["_change_type"] = "UPDATED"
            order["_changes"]     = changes
            results.append(order)

    return results

# ── Format Values ─────────────────────────────────────────────────────────────
def fmt(field, value):
    if value is None or value == "" or value == "None":
        return "—"
    meta = FIELD_META.get(field, {})
    unit = meta.get("unit", "")
    if unit == "bales":
        try:
            return f"{int(float(value)):,} bales"
        except (ValueError, TypeError):
            return str(value)
    if unit == "Rs":
        try:
            return f"Rs {float(value):,.2f}"
        except (ValueError, TypeError):
            return str(value)
    return str(value)

# ── Build Order Block ─────────────────────────────────────────────────────────
def build_order_block(order, mill_color):
    is_new      = order["_change_type"] == "NEW"
    changes     = order["_changes"]
    badge_color = "#1a7a4a" if is_new else "#b45309"
    badge_label = "NEW" if is_new else "UPDATED"
    order_url   = f"{PORTAL_URL}/app/pcso/{order['name']}"

    header = f"""
    <tr>
      <td colspan="2" style="padding:10px 12px 6px;background:#f9fafb;
                              border-top:2px solid {mill_color};">
        <span style="background:{badge_color};color:#fff;padding:2px 8px;
                     border-radius:3px;font-size:11px;font-weight:600;
                     margin-right:8px;">{badge_label}</span>
        <a href="{order_url}"
           style="color:#1d4ed8;font-weight:600;text-decoration:none;
                  font-family:monospace;font-size:13px;">{order['name']}</a>
        {"<span style='color:#6b7280;font-size:12px;margin-left:8px;'>" + str(len(changes)) + " field(s) changed</span>" if not is_new else ""}
      </td>
    </tr>"""

    field_rows = ""
    for field, meta in FIELD_META.items():
        new_val    = order.get(field)
        is_changed = field in changes
        old_val    = changes[field]["old"] if is_changed else None
        row_bg     = "#fffbeb" if is_changed else "#ffffff"
        label_color = "#92400e" if is_changed else "#6b7280"

        if is_changed:
            value_cell = f"""
              <span style="color:#dc2626;text-decoration:line-through;
                           font-size:12px;">{fmt(field, old_val)}</span>
              <span style="margin:0 6px;color:#9ca3af;">&#8594;</span>
              <span style="color:#15803d;font-weight:600;">{fmt(field, new_val)}</span>"""
        else:
            value_cell = f'<span style="color:#374151;">{fmt(field, new_val)}</span>'

        changed_icon = '<span style="color:#f59e0b;font-size:11px;margin-left:4px;">&#9650;</span>' if is_changed else ""

        field_rows += f"""
        <tr style="background:{row_bg};">
          <td style="padding:7px 12px;border-bottom:0.5px solid #e5e7eb;
                     width:35%;color:{label_color};font-size:12px;font-weight:500;">
            {meta['label']}{changed_icon}
          </td>
          <td style="padding:7px 12px;border-bottom:0.5px solid #e5e7eb;font-size:13px;">
            {value_cell}
          </td>
        </tr>"""

    return header + field_rows

# ── Build Mill Section ────────────────────────────────────────────────────────
def build_mill_section(mill, new_orders):
    order_blocks = ""
    for order in new_orders:
        order_blocks += build_order_block(order, mill["color"])

    return f"""
    <div style="margin-bottom:28px;">
      <div style="background:{mill['color']};padding:10px 16px;border-radius:6px 6px 0 0;">
        <span style="color:#fff;font-weight:700;font-size:14px;">{mill['name']}</span>
        <span style="color:rgba(255,255,255,0.75);font-size:12px;margin-left:8px;">
          {len(new_orders)} order(s)
        </span>
      </div>
      <table style="width:100%;border-collapse:collapse;font-size:13px;
                    border:1px solid #e5e7eb;border-top:none;background:#fff;">
        {order_blocks}
      </table>
    </div>"""

# ── Send Email ────────────────────────────────────────────────────────────────
def send_email(mills_with_orders, failed_mills=[]):
    total        = sum(len(orders) for _, orders in mills_with_orders)
    mill_names   = ", ".join(m["name"] for m, _ in mills_with_orders)
    failed_names = ", ".join(e["mill"]["name"] for e in failed_mills)

    subject_parts = []
    if total > 0:
        subject_parts.append(f"{total} Order(s) [{mill_names}]")
    if failed_mills:
        subject_parts.append(f"ERROR [{failed_names}]")
    subject = f"JuteSmart Alert: {' | '.join(subject_parts)} — {datetime.now().strftime('%d %b %Y')}"

    sections = ""
    for mill, new_orders in mills_with_orders:
        sections += build_mill_section(mill, new_orders)

    for entry in failed_mills:
        mill  = entry["mill"]
        error = entry["error"]
        if "timed out" in error.lower():
            friendly = "Connection timed out — portal may be temporarily unreachable."
        elif "max retries" in error.lower():
            friendly = "Could not reach portal after multiple attempts."
        elif "login failed" in error.lower():
            friendly = "Login failed — please check credentials."
        else:
            friendly = error[:200]
        sections += f"""
        <div style="margin-bottom:28px;">
          <div style="background:{mill['color']};padding:10px 16px;border-radius:6px 6px 0 0;">
            <span style="color:#fff;font-weight:700;font-size:14px;">{mill['name']}</span>
            <span style="color:rgba(255,255,255,0.75);font-size:12px;margin-left:8px;">
              Data unavailable
            </span>
          </div>
          <div style="border:1px solid #e5e7eb;border-top:none;background:#fff;
                      border-radius:0 0 6px 6px;padding:16px;">
            <span style="display:inline-block;background:#fee2e2;color:#dc2626;
                         padding:2px 8px;border-radius:3px;font-size:11px;
                         font-weight:600;margin-right:8px;">ERROR</span>
            <span style="color:#374151;font-size:13px;">{friendly}</span>
            <p style="color:#6b7280;font-size:12px;margin:8px 0 0;">
              Please check the portal manually for {mill['name']} or re-run the workflow.
            </p>
          </div>
        </div>"""

    html = f"""<!DOCTYPE html>
<html>
<body style="margin:0;padding:20px;background:#f3f4f6;font-family:Arial,sans-serif;">
  <div style="max-width:680px;margin:auto;">

    <div style="background:#14532d;padding:20px 24px;border-radius:8px 8px 0 0;">
      <h2 style="margin:0;color:#fff;font-size:18px;">JuteSmart PCSO Order Alert</h2>
      <p style="margin:4px 0 0;color:#86efac;font-size:13px;">
        {datetime.now().strftime('%d %b %Y')}
        &nbsp;|&nbsp; {total} order(s) across {len(mills_with_orders)} mill(s)
      </p>
    </div>

    <div style="background:#f3f4f6;padding:16px 0;">
      {sections}
    </div>

    <div style="background:#fff;border:1px solid #e5e7eb;border-radius:6px;
                padding:12px 16px;margin-bottom:16px;font-size:12px;color:#6b7280;">
      <strong style="color:#374151;">How to read this email:</strong>
      &nbsp;&nbsp;
      <span style="color:#dc2626;text-decoration:line-through;">Old value</span>
      &nbsp;&#8594;&nbsp;
      <span style="color:#15803d;font-weight:600;">New value</span>
      &nbsp;&nbsp;|&nbsp;&nbsp;
      <span style="background:#fffbeb;padding:1px 6px;border-radius:3px;">
        Yellow rows = changed fields
      </span>
    </div>

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

    snapshot           = load_snapshot()
    all_orders_by_mill = {}
    mills_with_orders  = []
    failed_mills       = []

    for mill in MILLS:
        print(f"\n-- {mill['name']} --")
        try:
            session    = get_session(mill["username"], mill["password"])
            orders     = fetch_orders(session)
            print(f"Fetched {len(orders)} orders")
            all_orders_by_mill[mill["name"]] = orders
            changed = find_changes(mill["name"], orders, snapshot)
            if changed:
                new_count     = sum(1 for o in changed if o["_change_type"] == "NEW")
                updated_count = sum(1 for o in changed if o["_change_type"] == "UPDATED")
                print(f"{new_count} new, {updated_count} updated")
                mills_with_orders.append((mill, changed))
            else:
                print("No changes detected")
        except Exception as e:
            error_msg = str(e)
            print(f"ERROR for {mill['name']}: {error_msg}")
            failed_mills.append({"mill": mill, "error": error_msg})

    if mills_with_orders or failed_mills:
        send_email(mills_with_orders, failed_mills)
    else:
        print("\nNo changes across any mill. Nothing to alert.")

    # Merge into existing snapshot — failed mills' data is preserved
    save_snapshot(all_orders_by_mill)
    print("\nSnapshot updated.")

if __name__ == "__main__":
    main()
