"""
AML Monitoring System - Flask web app (Step 1)

This first step only sets up the Flask app, configuration, and a Dashboard
page that shows placeholder zeros. Real data will be wired in later.
"""

import os
import csv
import io
import json
from collections import Counter
from datetime import datetime
from decimal import Decimal, InvalidOperation
from functools import wraps

from dotenv import load_dotenv
from flask import Flask, render_template, request, redirect, url_for, flash, session
from sqlalchemy import text

# Reuse the database setup and models from db.py (don't create a second connection).
from db import SessionLocal, init_db, Transaction, AMLAlert
# Reuse the exact AML detection logic the command-line monitor already uses.
from aml_rules import apply_rules

# Load variables from a local .env file (if present) into os.environ.
load_dotenv()

# Create the Flask application.
app = Flask(__name__)

# ---------------------------------------------------------------------------
# Configuration - everything is read from environment variables so we never
# hard-code secrets and can change behaviour per environment (local/Render).
# ---------------------------------------------------------------------------
app.config["SECRET_KEY"] = os.getenv("SECRET_KEY", "dev-secret-change-me")

# Risk-score thresholds used to decide how serious an alert is.
app.config["RISK_ALERT_THRESHOLD"] = int(os.getenv("RISK_ALERT_THRESHOLD", "30"))
app.config["RISK_CRITICAL_THRESHOLD"] = int(os.getenv("RISK_CRITICAL_THRESHOLD", "60"))

# Where alert emails are sent.
app.config["ALERT_RECIPIENT"] = os.getenv("ALERT_RECIPIENT", "banksamityforensic@gmail.com")

# SendGrid settings for sending escalation emails.
# Uses the SendGrid HTTPS API instead of SMTP, which avoids Render free-tier SMTP timeouts.
app.config["SENDGRID_API_KEY"] = os.getenv("SENDGRID_API_KEY")
app.config["SMTP_SENDER"] = os.getenv("ALERT_SENDER")

# Demo login credentials. These default to admin/admin123 for local testing —
# CHANGE THEM in production by setting APP_USERNAME and APP_PASSWORD env vars.
app.config["APP_USERNAME"] = os.getenv("APP_USERNAME", "admin")
app.config["APP_PASSWORD"] = os.getenv("APP_PASSWORD", "admin123")

# Make sure the database tables exist before we serve any requests.
init_db()


# ---------------------------------------------------------------------------
# Authentication helpers
# ---------------------------------------------------------------------------
def login_required(view_func):
    """Decorator that blocks a route unless the user is logged in.

    If there's no logged-in session, redirect to the login page. We use
    functools.wraps so Flask still sees the original function name (needed
    for url_for() to work correctly).
    """
    @wraps(view_func)
    def wrapped(*args, **kwargs):
        if not session.get("logged_in"):
            return redirect(url_for("login"))
        return view_func(*args, **kwargs)
    return wrapped


# ---------------------------------------------------------------------------
# Login / logout routes
# ---------------------------------------------------------------------------
@app.route("/login", methods=["GET", "POST"])
def login():
    """Show the login form (GET) and check credentials (POST)."""
    if request.method == "POST":
        username = (request.form.get("username") or "").strip()
        password = request.form.get("password") or ""

        # Compare against the configured demo credentials.
        if username == app.config["APP_USERNAME"] and password == app.config["APP_PASSWORD"]:
            session["logged_in"] = True
            session["user"] = username
            return redirect(url_for("dashboard"))

        # Wrong username or password: show an error and re-render the form.
        flash("Invalid username or password.", "danger")

    return render_template("login.html")


@app.route("/logout")
def logout():
    """Clear the session and send the user back to the login page."""
    session.clear()
    flash("You have been logged out.", "success")
    return redirect(url_for("login"))


# ---------------------------------------------------------------------------
# Routes
# ---------------------------------------------------------------------------
@app.route("/")
@login_required
def dashboard():
    """Dashboard home page.

    Queries the database for real counts and shows them on the stat cards.
    """
    # Critical alerts are anything at or above this risk score.
    critical_threshold = app.config["RISK_CRITICAL_THRESHOLD"]

    # Open one session for this request and always close it in `finally`.
    db = SessionLocal()
    try:
        stats = {
            # Total number of transactions stored.
            "total_transactions": db.query(Transaction).count(),
            # Total number of alerts ever created.
            "total_alerts": db.query(AMLAlert).count(),
            # Alerts still waiting to be reviewed.
            "open_alerts": db.query(AMLAlert).filter(AMLAlert.status == "open").count(),
            # Alerts that have been escalated to the compliance team.
            "escalated_alerts": db.query(AMLAlert).filter(AMLAlert.status == "escalated").count(),
            # Alerts whose risk score is at or above the critical threshold.
            "critical_alerts": db.query(AMLAlert).filter(AMLAlert.risk_score >= critical_threshold).count(),
        }
    finally:
        db.close()

    return render_template("dashboard.html", stats=stats, active_page="dashboard")


# ---------------------------------------------------------------------------
# Small helper functions used by the transactions routes.
# ---------------------------------------------------------------------------
def parse_bool(value):
    """Turn a form/CSV value into a boolean.

    Treats "true", "1", "yes" (any case, ignoring spaces) as True.
    Everything else (including None / empty) becomes False.
    """
    if value is None:
        return False
    return str(value).strip().lower() in {"true", "1", "yes"}


def parse_datetime(value):
    """Parse a datetime from the form or CSV.

    Accepts the datetime-local input format ("2026-01-15T14:30") as well as
    ISO with a space ("2026-01-15 14:30:00"). Returns a datetime or raises
    ValueError if nothing matches.
    """
    value = (value or "").strip()
    # Try a few common formats in order.
    for fmt in ("%Y-%m-%dT%H:%M", "%Y-%m-%dT%H:%M:%S", "%Y-%m-%d %H:%M:%S", "%Y-%m-%d %H:%M"):
        try:
            return datetime.strptime(value, fmt)
        except ValueError:
            continue
    # As a last resort let Python's flexible ISO parser try.
    return datetime.fromisoformat(value)


# ---------------------------------------------------------------------------
# Transactions page: list + manual add (GET shows page, POST adds a row).
# ---------------------------------------------------------------------------
@app.route("/transactions", methods=["GET", "POST"])
@login_required
def transactions():
    db = SessionLocal()
    try:
        # ---- POST: add one transaction from the manual entry form ----
        if request.method == "POST":
            form = request.form

            # balance_after is optional, so only convert it when provided.
            balance_raw = (form.get("balance_after") or "").strip()
            balance_after = Decimal(balance_raw) if balance_raw else None

            txn = Transaction(
                account_id=int(form["account_id"]),
                amount=Decimal(form["amount"]),
                currency=(form.get("currency") or "USD").strip() or "USD",
                direction=form.get("direction", "in"),
                channel=(form.get("channel") or "").strip(),
                is_cash=parse_bool(form.get("is_cash")),
                is_international=parse_bool(form.get("is_international")),
                counterparty_id=(form.get("counterparty_id") or "").strip(),
                counterparty_country=(form.get("counterparty_country") or "").strip().upper(),
                occurred_at=parse_datetime(form.get("occurred_at")),
                balance_after=balance_after,
            )
            db.add(txn)
            db.commit()
            flash("Transaction added.", "success")
            # Redirect (PRG pattern) so a page refresh won't re-submit the form.
            return redirect(url_for("transactions"))

        # ---- GET: show the 50 most recent transactions, newest first ----
        recent = (
            db.query(Transaction)
            .order_by(Transaction.occurred_at.desc())
            .limit(50)
            .all()
        )
        return render_template("transactions.html", transactions=recent, active_page="transactions")
    finally:
        db.close()


# ---------------------------------------------------------------------------
# CSV upload: import many transactions at once.
# ---------------------------------------------------------------------------
@app.route("/transactions/upload", methods=["POST"])
@login_required
def transactions_upload():
    db = SessionLocal()
    try:
        file = request.files.get("csv_file")
        if not file or file.filename == "":
            flash("No CSV file selected.", "danger")
            return redirect(url_for("transactions"))

        # Decode the uploaded bytes into text and parse with DictReader so we
        # can read each column by its header name.
        stream = io.StringIO(file.stream.read().decode("utf-8-sig"))
        reader = csv.DictReader(stream)

        imported = 0  # rows successfully saved
        skipped = 0   # rows that were malformed and skipped

        for row in reader:
            try:
                balance_raw = (row.get("balance_after") or "").strip()
                balance_after = Decimal(balance_raw) if balance_raw else None

                txn = Transaction(
                    account_id=int(row["account_id"]),
                    amount=Decimal(row["amount"]),
                    currency=(row.get("currency") or "USD").strip() or "USD",
                    direction=(row.get("direction") or "").strip().lower(),
                    channel=(row.get("channel") or "").strip(),
                    is_cash=parse_bool(row.get("is_cash")),
                    is_international=parse_bool(row.get("is_international")),
                    counterparty_id=(row.get("counterparty_id") or "").strip(),
                    counterparty_country=(row.get("counterparty_country") or "").strip().upper(),
                    occurred_at=parse_datetime(row.get("occurred_at")),
                    balance_after=balance_after,
                )
                db.add(txn)
                imported += 1
            except (ValueError, InvalidOperation, KeyError, TypeError):
                # Any bad row is skipped instead of crashing the whole upload.
                skipped += 1
                continue

        db.commit()
        flash(f"Imported {imported} transaction(s). Skipped {skipped} bad row(s).", "success")
        return redirect(url_for("transactions"))
    finally:
        db.close()


# ---------------------------------------------------------------------------
# Email: notify the AML/forensic team when an alert is escalated.
# ---------------------------------------------------------------------------
def severity_for_score(score):
    """Turn a numeric risk score into a severity label using the thresholds."""
    if score >= app.config["RISK_CRITICAL_THRESHOLD"]:
        return "CRITICAL"
    if score >= app.config["RISK_ALERT_THRESHOLD"]:
        return "HIGH"
    return "LOW"


def send_escalation_email(alert):
    """Send an escalation email for one alert using the SendGrid HTTPS API.

    This avoids SMTP ports, which are often blocked on free hosting platforms.
    Returns True when SendGrid accepts the message, otherwise False.
    It never raises, so escalation still works even if email fails.
    """
    try:
        from sendgrid import SendGridAPIClient
        from sendgrid.helpers.mail import Mail
    except Exception as e:
        print("EMAIL ERROR: SendGrid package is not installed:", str(e))
        return False

    api_key = os.getenv("SENDGRID_API_KEY")
    sender = os.getenv("ALERT_SENDER")
    recipient = os.getenv("ALERT_RECIPIENT", "banksamityforensic@gmail.com")

    if not all([api_key, sender, recipient]):
        print("EMAIL SKIPPED: Missing SendGrid settings")
        return False

    severity = severity_for_score(int(alert.risk_score))
    subject = f"AML {severity} Alert Escalated - Account {alert.account_id}"

    body = (
        f"Account ID: {alert.account_id}\n"
        f"Risk Score: {alert.risk_score}\n"
        f"Severity: {severity}\n"
        f"Triggered AML Rules: {alert.rules_triggered}\n\n"
        f"Explanation / Reason:\n{alert.reason}\n\n"
        f"Related Transaction IDs: {alert.txn_ids}\n"
        f"Alert Status: escalated\n"
        f"Timestamp: {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}\n\n"
        f"This case should be reviewed by the bank's AML/forensic team."
    )

    try:
        message = Mail(
            from_email=sender,
            to_emails=recipient,
            subject=subject,
            plain_text_content=body,
        )
        response = SendGridAPIClient(api_key).send(message)
        print("EMAIL SENT:", response.status_code)
        return response.status_code in (200, 202)
    except Exception as e:
        print("EMAIL ERROR:", str(e))
        return False


# ---------------------------------------------------------------------------
# Alerts & Cases page: list alerts and let analysts change their status.
# ---------------------------------------------------------------------------

# The only status values an analyst is allowed to set.
VALID_ALERT_STATUSES = ["open", "in_review", "escalated", "dismissed", "closed"]


@app.route("/alerts")
@login_required
def alerts():
    """Show every alert, newest first."""
    db = SessionLocal()
    try:
        # Newest first: order by created_at desc, then id desc as a fallback
        # (created_at can be NULL for older rows).
        rows = (
            db.query(AMLAlert)
            .order_by(AMLAlert.created_at.desc(), AMLAlert.id.desc())
            .all()
        )
        return render_template(
            "alerts.html",
            alerts=rows,
            active_page="alerts",
            # Thresholds let the template compute each alert's severity badge.
            alert_threshold=app.config["RISK_ALERT_THRESHOLD"],
            critical_threshold=app.config["RISK_CRITICAL_THRESHOLD"],
        )
    finally:
        db.close()


@app.route("/alerts/<int:alert_id>/status", methods=["POST"])
@login_required
def update_alert_status(alert_id):
    """Change a single alert's status, then return to the alerts list."""
    new_status = (request.form.get("status") or "").strip()

    # Reject anything not in our allowed list.
    if new_status not in VALID_ALERT_STATUSES:
        flash(f"Invalid status '{new_status}'.", "danger")
        return redirect(url_for("alerts"))

    db = SessionLocal()
    try:
        alert = db.get(AMLAlert, alert_id)
        if not alert:
            flash(f"Alert #{alert_id} not found.", "danger")
            return redirect(url_for("alerts"))

        # Update the status first — this must succeed regardless of email.
        alert.status = new_status
        db.commit()

        # When escalating, send a notification email ONCE. The `emailed` flag
        # (added in step 1) is our duplicate-email protection: if it's already
        # True we never send again, even if escalated repeatedly.
        email_note = ""
        if new_status == "escalated" and not alert.emailed:
            if send_escalation_email(alert):
                alert.emailed = True
                alert.email_sent_at = datetime.now()
                db.commit()
                email_note = " Escalation email sent."

        flash(f"Alert #{alert_id} marked as {new_status}.{email_note}", "success")
        return redirect(url_for("alerts"))
    finally:
        db.close()


# ---------------------------------------------------------------------------
# AML scan: reuse the same detection logic the command-line monitor uses.
# ---------------------------------------------------------------------------
def txn_to_dict(t):
    """Convert a Transaction row into the dict shape apply_rules() expects.

    This mirrors aml_monitor.py's txn_to_dict() exactly so the web scan and the
    command-line monitor analyze data in the same way.
    """
    return {
        "id": int(t.id),
        "account_id": int(t.account_id),
        "amount": Decimal(str(t.amount)),
        "currency": t.currency or "USD",
        "direction": (t.direction or "").lower(),
        "channel": t.channel or "",
        "is_cash": bool(t.is_cash),
        "is_international": bool(t.is_international),
        "counterparty_id": t.counterparty_id or "",
        "counterparty_country": (t.counterparty_country or "").upper(),
        "occurred_at": t.occurred_at,
        "balance_after": Decimal(str(t.balance_after)) if t.balance_after is not None else None,
    }


def run_aml_scan():
    """Scan transactions, apply the AML rules per account, and create alerts.

    Returns the number of NEW alerts created. Mirrors aml_monitor.py's core
    work, but for the demo it scans every transaction in the table (no
    watermark) to keep things simple.
    """
    alert_threshold = app.config["RISK_ALERT_THRESHOLD"]

    db = SessionLocal()
    try:
        # Load every transaction in the table (simple demo behaviour).
        txns = db.query(Transaction).order_by(Transaction.occurred_at.asc()).all()
        if not txns:
            return 0

        # Group the transactions by account so each account is scored on its own.
        by_account = {}
        for t in txns:
            by_account.setdefault(t.account_id, []).append(txn_to_dict(t))

        created = 0
        for account_id, items in by_account.items():
            # Reuse apply_rules() exactly as the command-line monitor does.
            result, contributing_ids = apply_rules(items, now=datetime.now())

            # Only raise an alert if the score clears the threshold AND at least
            # one rule actually fired.
            if result.score >= alert_threshold and result.triggered:
                txn_ids_str = ",".join(map(str, contributing_ids))
                rules_str = ",".join(sorted(result.triggered))
                reason_str = "; ".join(result.reasons)

                # Duplicate protection: skip if an equivalent active alert exists.
                already_exists = (
                    db.query(AMLAlert)
                    .filter(
                        AMLAlert.account_id == account_id,
                        AMLAlert.txn_ids == txn_ids_str,
                        AMLAlert.rules_triggered == rules_str,
                        AMLAlert.status.in_(["open", "in_review", "escalated"]),
                    )
                    .first()
                )
                if not already_exists:
                    alert = AMLAlert(
                        account_id=account_id,
                        txn_ids=txn_ids_str,
                        rules_triggered=rules_str,
                        risk_score=int(result.score),
                        reason=reason_str,
                        status="open",
                    )
                    db.add(alert)
                    created += 1

        db.commit()
        return created
    finally:
        db.close()


@app.route("/scan", methods=["POST"])
@login_required
def scan():
    """Trigger an AML scan from the website, then return to the Dashboard."""
    created = run_aml_scan()
    flash(f"Scan complete. Created {created} new alert(s).", "success")
    return redirect(url_for("dashboard"))


# ---------------------------------------------------------------------------
# AI Alert Agent: a rule-based agent (NO external AI API) that reviews open
# alerts, writes a readable summary, emails them, and escalates them.
# Mirrors the logic in ai_agent.py's run_agent().
# ---------------------------------------------------------------------------
def generate_agent_summary(alert):
    """Build a readable investigation summary for one alert.

    Same shape as ai_agent.py's generate_agent_summary(), but severity uses our
    configured thresholds via severity_for_score().
    """
    severity = severity_for_score(int(alert.risk_score))
    return (
        "AML AGENT ALERT REPORT\n\n"
        f"Severity: {severity}\n\n"
        f"Account ID:\n{alert.account_id}\n\n"
        f"Risk Score:\n{alert.risk_score}\n\n"
        f"Rules Triggered:\n{alert.rules_triggered}\n\n"
        f"Reason:\n{alert.reason}\n\n"
        f"Transaction IDs:\n{alert.txn_ids}\n\n"
        "Agent Recommendation:\n"
        "This case should be reviewed by the bank's AML/forensic team.\n"
        "If the activity is confirmed suspicious, escalate for compliance review."
    )


def run_ai_agent():
    """Review open alerts, email + escalate them, and return what was processed.

    Returns a tuple: (count_processed, list_of_summaries).
    """
    db = SessionLocal()
    try:
        # Same selection as ai_agent.py: open alerts, highest risk first.
        open_alerts = (
            db.query(AMLAlert)
            .filter(AMLAlert.status == "open")
            .order_by(AMLAlert.risk_score.desc())
            .all()
        )

        processed = 0
        summaries = []

        for alert in open_alerts:
            # Skip anything that has already been emailed (duplicate protection).
            if alert.emailed:
                continue

            # Build the readable summary for this alert.
            summaries.append(generate_agent_summary(alert))

            # Reuse the SINGLE email path from step 6 (same dedupe behaviour).
            sent = send_escalation_email(alert)

            # The agent always escalates so it visibly does its job in the demo.
            # If the email actually went out, also record the emailed flag/time;
            # if SMTP was skipped/failed, leave emailed=False so a real email can
            # still be sent later once SMTP is configured.
            alert.status = "escalated"
            if sent:
                alert.emailed = True
                alert.email_sent_at = datetime.now()

            processed += 1

        db.commit()
        return processed, summaries
    finally:
        db.close()


@app.route("/agent/run", methods=["POST"])
@login_required
def agent_run():
    """Trigger the AI Alert Agent, then return to the alerts list."""
    processed, _summaries = run_ai_agent()
    flash(f"AI Agent reviewed {processed} open alert(s) and escalated them.", "success")
    return redirect(url_for("alerts"))


# ---------------------------------------------------------------------------
# Analytics page: charts built from the alerts data.
# ---------------------------------------------------------------------------

# Plain-English descriptions and weights for each AML rule. Kept here so the
# Analytics page can explain what every rule means. (Weights match aml_rules.py.)
AML_RULE_INFO = [
    ("STRUCTURING", 35, "Several smaller cash deposits just under the reporting limit, likely to avoid detection."),
    ("RAPID_FLOW", 25, "Money comes in and is quickly sent back out (pass-through / layering)."),
    ("CTR_CASH", 20, "Total cash activity in a single day is above the currency-reporting threshold."),
    ("VELOCITY", 20, "An unusually high number or value of transactions in a short time window."),
    ("HIGH_RISK_GEO", 20, "Transactions involve countries flagged as high-risk."),
    ("DORMANT_BURST", 20, "A long-inactive account suddenly bursts into heavy activity."),
    ("FAN_OUT", 15, "Funds are sent out to many different counterparties in a short period."),
    ("ROUND_AMOUNTS", 10, "Repeated suspiciously round amounts (exact multiples), unusual for real spending."),
    ("UNUSUAL_HOURS", 10, "Higher-value transactions happening during unusual overnight hours."),
]


@app.route("/analytics")
@login_required
def analytics():
    """Gather alert statistics and pass them to the charts template."""
    alert_threshold = app.config["RISK_ALERT_THRESHOLD"]
    critical_threshold = app.config["RISK_CRITICAL_THRESHOLD"]

    db = SessionLocal()
    try:
        all_alerts = db.query(AMLAlert).all()

        # --- Risk score distribution: LOW / HIGH / CRITICAL buckets ---
        risk_buckets = {"LOW": 0, "HIGH": 0, "CRITICAL": 0}
        for a in all_alerts:
            score = int(a.risk_score)
            if score >= critical_threshold:
                risk_buckets["CRITICAL"] += 1
            elif score >= alert_threshold:
                risk_buckets["HIGH"] += 1
            else:
                risk_buckets["LOW"] += 1

        # --- Alert status counts (keep a fixed, friendly order) ---
        status_order = ["open", "in_review", "escalated", "dismissed", "closed"]
        status_counts = {s: 0 for s in status_order}
        for a in all_alerts:
            # Count any unexpected status too, so nothing is silently dropped.
            status_counts[a.status] = status_counts.get(a.status, 0) + 1

        # --- Rule frequency: split the comma-separated rules_triggered text ---
        rule_counter = Counter()
        for a in all_alerts:
            for rule in (a.rules_triggered or "").split(","):
                rule = rule.strip()
                if rule:
                    rule_counter[rule] += 1
        # Sort most-frequent first for a tidy horizontal bar chart.
        rule_freq = dict(rule_counter.most_common())

        return render_template(
            "analytics.html",
            active_page="analytics",
            has_data=len(all_alerts) > 0,
            rule_info=AML_RULE_INFO,
            # Pass the chart data as JSON strings so Chart.js can read them.
            risk_labels=json.dumps(list(risk_buckets.keys())),
            risk_values=json.dumps(list(risk_buckets.values())),
            status_labels=json.dumps(list(status_counts.keys())),
            status_values=json.dumps(list(status_counts.values())),
            rule_labels=json.dumps(list(rule_freq.keys())),
            rule_values=json.dumps(list(rule_freq.values())),
        )
    finally:
        db.close()


# ---------------------------------------------------------------------------
# Settings page: read-only view of configuration (never exposes secrets).
# ---------------------------------------------------------------------------
@app.route("/settings")
@login_required
def settings():
    """Show configuration and system status without revealing any secrets."""
    database_url = os.getenv("DATABASE_URL", "sqlite:///test.db")

    # Database health check: run a trivial query and report Connected/Error.
    db = SessionLocal()
    try:
        db.execute(text("SELECT 1"))
        db_status = "Connected"
    except Exception:
        db_status = "Error"
    finally:
        db.close()

    # Work out the database engine type from the URL.
    if database_url.startswith("postgres"):
        db_type = "PostgreSQL"
    elif database_url.startswith("sqlite"):
        db_type = "SQLite"
    else:
        db_type = "Unknown"

    # Email is "configured" only if the SendGrid API key and sender are present.
    smtp_configured = all([
        os.getenv("SENDGRID_API_KEY"),
        os.getenv("ALERT_SENDER"),
        os.getenv("ALERT_RECIPIENT"),
    ])

    # For each important env var, report only whether it is SET (never values).
    env_vars = ["SECRET_KEY", "DATABASE_URL", "SENDGRID_API_KEY",
                "ALERT_SENDER", "ALERT_RECIPIENT"]
    env_status = {name: bool(os.getenv(name)) for name in env_vars}

    return render_template(
        "settings.html",
        active_page="settings",
        alert_recipient=app.config["ALERT_RECIPIENT"],
        risk_threshold=app.config["RISK_ALERT_THRESHOLD"],
        critical_threshold=app.config["RISK_CRITICAL_THRESHOLD"],
        db_status=db_status,
        db_type=db_type,
        smtp_configured=smtp_configured,
        env_status=env_status,
    )


if __name__ == "__main__":
    # Run a local development server at http://localhost:5000
    app.run(host="0.0.0.0", port=5000, debug=True)
    app.run(host="0.0.0.0", port=5000, debug=True)
