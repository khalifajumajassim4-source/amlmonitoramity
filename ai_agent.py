import os
import smtplib
from email.mime.text import MIMEText
from dotenv import load_dotenv

from db import SessionLocal, AMLAlert

load_dotenv()

ALERT_RECIPIENT = os.getenv("ALERT_RECIPIENT", "banksamityforensic@gmail.com")


def classify_alert(alert):
    score = int(alert.risk_score)

    if score >= 60:
        return "CRITICAL"
    elif score >= 30:
        return "HIGH"
    else:
        return "LOW"


def generate_agent_summary(alert):
    severity = classify_alert(alert)

    return f"""
AML AGENT ALERT REPORT

Severity: {severity}

Account ID:
{alert.account_id}

Risk Score:
{alert.risk_score}

Rules Triggered:
{alert.rules_triggered}

Reason:
{alert.reason}

Transaction IDs:
{alert.txn_ids}

Agent Recommendation:
This case should be reviewed by the bank's AML/forensic team.
If the activity is confirmed suspicious, escalate for compliance review.
"""


def send_email(subject, body):
    smtp_host = os.getenv("SMTP_HOST")
    smtp_port = int(os.getenv("SMTP_PORT", "587"))
    smtp_user = os.getenv("SMTP_USER")
    smtp_pass = os.getenv("SMTP_PASS")
    sender = os.getenv("ALERT_SENDER", smtp_user)

    if not all([smtp_host, smtp_user, smtp_pass, sender]):
        print("EMAIL NOT SENT: Missing SMTP settings.")
        return False

    msg = MIMEText(body)
    msg["Subject"] = subject
    msg["From"] = sender
    msg["To"] = ALERT_RECIPIENT

    with smtplib.SMTP(smtp_host, smtp_port, timeout=20) as server:
        server.starttls()
        server.login(smtp_user, smtp_pass)
        server.sendmail(sender, [ALERT_RECIPIENT], msg.as_string())

    return True


def run_agent():
    db = SessionLocal()

    alerts = (
        db.query(AMLAlert)
        .filter(AMLAlert.status == "open")
        .order_by(AMLAlert.risk_score.desc())
        .all()
    )

    sent_count = 0

    for alert in alerts:
        severity = classify_alert(alert)

        subject = f"AML {severity} Alert - Account {alert.account_id}"
        body = generate_agent_summary(alert)

        sent = send_email(subject, body)

        if sent:
            alert.status = "escalated"
            sent_count += 1

    db.commit()
    db.close()

    print(f"Agent sent {sent_count} alert emails.")


if __name__ == "__main__":
    run_agent()
