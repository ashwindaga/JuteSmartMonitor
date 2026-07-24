# 🌿 JuteSmart Order Monitor

Automatically monitors `smart.jutecomm.gov.in/app/pcso` every 15 minutes and sends an email alert when new or updated orders appear.

**Runs entirely on GitHub Actions — no server needed.**

---

## Setup (one-time, ~10 minutes)

### Step 1 — Create a private GitHub repo

1. Go to [github.com/new](https://github.com/new)
2. Name it `jute-monitor` (or anything you like)
3. Set visibility to **Private** ← important
4. Click **Create repository**

### Step 2 — Upload these files

Upload all files maintaining this structure:
```
jute-monitor/
├── .github/
│   └── workflows/
│       └── monitor.yml
├── monitor.py
├── last_seen_orders.json
└── README.md
```

You can drag-and-drop files on GitHub, but for `.github/workflows/monitor.yml`
you'll need to create the folders manually on GitHub's UI (type the path in the
filename box: `.github/workflows/monitor.yml`).

### Step 3 — Create a Gmail App Password

Your normal Gmail password won't work for automated scripts. You need an App Password:

1. Go to your Google Account → **Security**
2. Enable **2-Step Verification** (required)
3. Go to **Security → App Passwords**
4. Select App: **Mail**, Device: **Other** → type "JuteSmart Monitor"
5. Google gives you a 16-character password — **copy it**

### Step 4 — Add GitHub Secrets

In your GitHub repo → **Settings → Secrets and variables → Actions → New repository secret**

Add these 5 secrets:

| Secret Name          | Value                                      |
|----------------------|--------------------------------------------|
| `JUTECOMM_USERNAME`  | Your JuteSmart login email                 |
| `JUTECOMM_PASSWORD`  | Your JuteSmart password                    |
| `GMAIL_SENDER`       | Your Gmail address (e.g. you@gmail.com)    |
| `GMAIL_APP_PASSWORD` | The 16-char App Password from Step 3       |
| `ALERT_EMAIL`        | Email to receive alerts (can be same Gmail)|

### Step 5 — Test it manually

1. Go to your repo → **Actions** tab
2. Click **JuteSmart Order Monitor** → **Run workflow** → **Run workflow**
3. Watch the logs — you should see "Logged in successfully" and "Snapshot updated"

That's it! It will now run automatically every 15 minutes.

---

## How it works

```
Every 15 min:
  GitHub Actions wakes up
    → Logs into JuteSmart with your credentials
    → Calls the Frappe REST API to get current PCSO orders
    → Compares with last_seen_orders.json (saved in the repo)
    → If anything is new or changed → sends you an HTML email alert
    → Saves updated snapshot back to the repo
```

---

## Troubleshooting

**"Login failed" error**
→ Check `JUTECOMM_USERNAME` and `JUTECOMM_PASSWORD` secrets are correct

**"404" error on orders fetch**
→ The DocType name may differ. Check the URL in your browser after logging in
  and note the exact path. Update the `fetch_orders()` function in `monitor.py`.

**No email received**
→ Check spam folder. Verify `GMAIL_APP_PASSWORD` is correct (no spaces).

**GitHub Actions not running**
→ GitHub sometimes pauses scheduled workflows on repos with no recent activity.
  Push any small commit (e.g. edit README) to wake it up.

---

## Security notes

- Your credentials are stored in GitHub Secrets — encrypted at rest, never shown in logs
- The repo is private — only you can see the code and logs
- The script only reads data from the portal, never writes anything
- To stop monitoring: go to Actions → disable the workflow, or delete the repo
