import os
import smtplib
from datetime import datetime, timedelta
from decimal import Decimal
from email.mime.text import MIMEText
from dotenv import load_dotenv
from db import SessionLocal, Transaction, AMLAlert, AMLState, init_db
from aml_rules import apply_rules

load_dotenv()
init_db()
session = SessionLocal()

ALERT_THRESHOLD = int(os.getenv("RISK_ALERT_THRESHOLD", "30"))
LOOKBACK_DAYS = int(os.getenv("LOOKBACK_DAYS", "30"))

def txn_to_dict(t):
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

def send_email_alert(subject, body):
    try:
        smtp_host = os.getenv("SMTP_HOST")
        smtp_port = int(os.getenv("SMTP_PORT", "587"))
        smtp_user = os.getenv("SMTP_USER")
        smtp_pass = os.getenv("SMTP_PASS")
        recipient = os.getenv("ALERT_RECIPIENT")
        sender = os.getenv("ALERT_SENDER")
        if not all([smtp_host, smtp_user, smtp_pass, recipient, sender]):
            print("EMAIL SKIPPED: Missing SMTP settings")
            return
        msg = MIMEText(body)
        msg["Subject"] = subject
        msg["From"] = sender
        msg["To"] = recipient
        server = smtplib.SMTP(smtp_host, smtp_port, timeout=20)
        server.starttls()
        server.login(smtp_user, smtp_pass)
        server.sendmail(sender, [recipient], msg.as_string())
        server.quit()
        print("EMAIL SENT")
    except Exception as e:
        print("EMAIL ERROR:", str(e))

try:
    state = session.get(AMLState, 1)
    if not state:
        state = AMLState(id=1, last_processed_at=datetime.now() - timedelta(days=LOOKBACK_DAYS))
        session.add(state)
        session.commit()
    watermark = state.last_processed_at or (datetime.now() - timedelta(days=LOOKBACK_DAYS))
    txns = session.query(Transaction).filter(Transaction.occurred_at > watermark).order_by(Transaction.occurred_at.asc()).all()
    if not txns:
        raise SystemExit
    by_account = {}
    for t in txns:
        by_account.setdefault(t.account_id, []).append(txn_to_dict(t))
    created = 0
    latest_time = max(t.occurred_at for t in txns)
    for account_id, items in by_account.items():
        result, contributing_ids = apply_rules(items, now=datetime.now())
        if result.score >= ALERT_THRESHOLD and result.triggered:
            txn_ids_str = ",".join(map(str, contributing_ids))
            rules_str = ",".join(sorted(result.triggered))
            reason_str = "; ".join(result.reasons)
            already_exists = session.query(AMLAlert).filter(
                AMLAlert.account_id == account_id,
                AMLAlert.txn_ids == txn_ids_str,
                AMLAlert.rules_triggered == rules_str,
                AMLAlert.status.in_(["open", "in_review", "escalated"]),
            ).first()
            if not already_exists:
                alert = AMLAlert(
                    account_id=account_id,
                    txn_ids=txn_ids_str,
                    rules_triggered=rules_str,
                    risk_score=int(result.score),
                    reason=reason_str,
                    status="open",
                )
                session.add(alert)
                created += 1
                send_email_alert(f"AML Alert - Account {account_id}",
                                 f"Account: {account_id}\nRisk Score: {result.score}\nRules Triggered: {rules_str}\n\nReason:\n{reason_str}\n\nTransaction IDs:\n{txn_ids_str}")
    session.commit()
    state.last_processed_at = latest_time
    session.commit()
    print(f"ALERTS CREATED: {created}")
except SystemExit:
    session.rollback()
except Exception as e:
    session.rollback()
    print("ERROR:", str(e))
finally:
    session.close()
