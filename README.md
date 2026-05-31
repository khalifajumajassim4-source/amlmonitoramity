# AML Monitoring System

A web-based **Anti-Money-Laundering (AML) monitoring and alerting system** built with Flask.
It lets a compliance analyst load transactions, run a rule-based AML scan over them, and
review the alerts it raises. Suspicious activity is scored, classified by severity, and can
be escalated — which automatically emails the bank's AML/forensic team. A simple rule-based
"AI agent" can review all open alerts at once, and an analytics dashboard visualises what the
rules are catching. There is **no external AI API** — all detection is transparent, rule-based logic.

---

## Features

- **Dashboard** — at-a-glance counts of transactions, total/open/escalated/critical alerts, and a one-click "Run AML Scan".
- **Transactions** — add transactions manually or bulk-import them from a CSV file, and browse the most recent activity.
- **AML rule-based scan** — analyses transactions per account and raises alerts (with duplicate protection).
- **Alerts & Cases** — review every alert, see its severity and explanation, and change its status (review / escalate / dismiss / close).
- **Automatic escalation emails** — escalating an alert emails the AML team once (with duplicate-email protection).
- **AI Alert Agent** — reviews all open alerts, classifies severity, writes a summary, and escalates them automatically.
- **Analytics** — charts for risk-score distribution, alert status, and rule frequency, plus a rules reference table.
- **Settings** — read-only view of configuration and system status (never exposes secrets).

---

## AML Rules and Risk Weights

Each rule that fires adds its weight to an account's risk score. An alert is raised when the
score reaches the alert threshold (default **30**); a score of **60+** is treated as CRITICAL.

| Rule            | Weight | What it detects                                                                 |
|-----------------|:------:|---------------------------------------------------------------------------------|
| STRUCTURING     |  +35   | Several smaller cash deposits just under the reporting limit, to avoid detection. |
| RAPID_FLOW      |  +25   | Money comes in and is quickly sent back out (pass-through / layering).            |
| CTR_CASH        |  +20   | Total cash activity in a single day is above the currency-reporting threshold.   |
| VELOCITY        |  +20   | An unusually high number or value of transactions in a short time window.        |
| HIGH_RISK_GEO   |  +20   | Transactions involve countries flagged as high-risk.                             |
| DORMANT_BURST   |  +20   | A long-inactive account suddenly bursts into heavy activity.                     |
| FAN_OUT         |  +15   | Funds are sent out to many different counterparties in a short period.           |
| ROUND_AMOUNTS   |  +10   | Repeated suspiciously round amounts (exact multiples), unusual for real spending. |
| UNUSUAL_HOURS   |  +10   | Higher-value transactions happening during unusual overnight hours.              |

---

## Run It Locally

```bash
# 1. Go into the project folder
cd AML-moniter

# 2. Install the dependencies
pip install -r requirements.txt

# 3. Create a .env file (see the template below) in the project root

# 4. Start the app
python app.py

# 5. Open it in your browser
#    http://localhost:5000
```

The app uses a local SQLite database (`test.db`) by default, so it runs out of the box — you
only need `DATABASE_URL` if you want to use PostgreSQL. Email features are optional: without
SMTP settings the app still works and simply skips sending emails.

---

## Sample `.env` Template

Create a file named `.env` in the project root with these values (replace the placeholders):

```ini
# Flask
SECRET_KEY=change-me-to-a-long-random-string

# Database (omit to use the default local SQLite file)
DATABASE_URL=sqlite:///test.db

# Email / SMTP (optional — needed only for escalation emails)
SMTP_HOST=smtp.gmail.com
SMTP_PORT=587
SMTP_USER=youraddress@gmail.com
SMTP_PASS=your-app-password
ALERT_SENDER=youraddress@gmail.com
ALERT_RECIPIENT=banksamityforensic@gmail.com

# Risk-scoring thresholds
RISK_ALERT_THRESHOLD=30
RISK_CRITICAL_THRESHOLD=60
```

> **Gmail note:** Gmail will not accept your normal password over SMTP. You must enable
> **2-Step Verification** on the Google account and then create an **App Password**, and use
> that 16-character App Password as `SMTP_PASS`.

> The `.env` file is listed in `.gitignore` and must **never** be committed.

---

## Deploy on Render

This repo includes a `render.yaml` so Render can configure the service automatically.

1. Push the project to a **GitHub** repository.
2. In the [Render dashboard](https://dashboard.render.com), click **New → Web Service** and
   connect your GitHub repo.
3. Render reads `render.yaml` and sets up a free Python web service:
   - Build command: `pip install -r requirements.txt`
   - Start command: `gunicorn app:app`
   - `SECRET_KEY` is generated automatically.
4. Open the service's **Environment** tab and fill in the values marked `sync: false`
   (`DATABASE_URL`, the `SMTP_*` settings, `ALERT_SENDER`, `ALERT_RECIPIENT`,
   `RISK_ALERT_THRESHOLD`, `RISK_CRITICAL_THRESHOLD`). These are intentionally **not** stored
   in the repo so secrets stay private.
5. Save — Render redeploys and your app goes live.

> For a persistent database on Render, create a **PostgreSQL** instance and paste its
> Internal Database URL into `DATABASE_URL`.

---

## Demo Flow

A ready-made `sample_transactions.csv` is included and is designed to trigger several rules.

1. Start the app and open **http://localhost:5000**.
2. Go to **Transactions → Import from CSV** and upload `sample_transactions.csv`.
3. Go to the **Dashboard** and click **Run AML Scan** — alerts are created.
4. Open **Alerts & Cases** to review the alerts, their severity, and explanations.
5. Click **Escalate** on an alert — if SMTP is configured, an escalation email is sent
   (only once per alert).
6. Click **Run AI Alert Agent** to have the agent review and escalate all remaining open alerts.
7. Open **Analytics** to see the charts (risk distribution, status, rule frequency).
8. Open **Settings** to confirm system status and configuration (no secrets are shown).

The sample data raises alerts for several accounts (e.g. structuring + cash reporting,
high-risk geography, and rapid in-then-out fund flow), while one account stays low-risk so the
data looks realistic.
