import os
from sqlalchemy import create_engine, Column, Integer, String, Boolean, Numeric, Text, DateTime, func
from sqlalchemy.orm import declarative_base, sessionmaker

# Database connection string. Defaults to a local SQLite file so the app
# runs out of the box; in production (e.g. Render) DATABASE_URL points at Postgres.
DATABASE_URL = os.getenv("DATABASE_URL", "sqlite:///test.db")

engine = create_engine(DATABASE_URL, future=True, pool_pre_ping=True)
SessionLocal = sessionmaker(bind=engine, autoflush=False, autocommit=False, future=True)
Base = declarative_base()

class Transaction(Base):
    __tablename__ = "transactions"
    id = Column(Integer, primary_key=True, autoincrement=True)
    account_id = Column(Integer, nullable=False)
    amount = Column(Numeric(18, 2), nullable=False)
    currency = Column(String, nullable=False)
    direction = Column(String, nullable=False)
    channel = Column(String, nullable=True)
    is_cash = Column(Boolean, default=False)
    is_international = Column(Boolean, default=False)
    counterparty_id = Column(String, nullable=True)
    counterparty_country = Column(String, nullable=True)
    occurred_at = Column(DateTime, nullable=False)
    balance_after = Column(Numeric(18, 2), nullable=True)

class AMLState(Base):
    __tablename__ = "aml_state"
    id = Column(Integer, primary_key=True, default=1)
    last_processed_at = Column(DateTime, nullable=True)

class AMLAlert(Base):
    __tablename__ = "aml_alerts"
    id = Column(Integer, primary_key=True, autoincrement=True)
    account_id = Column(Integer, nullable=False)
    txn_ids = Column(Text, nullable=False)
    rules_triggered = Column(Text, nullable=False)
    risk_score = Column(Integer, nullable=False)
    reason = Column(Text, nullable=False)
    created_at = Column(DateTime, server_default=func.now())
    status = Column(String, default="open")
    # --- New in step 1 ---
    # Whether a notification email has been sent for this alert.
    emailed = Column(Boolean, default=False)
    # Timestamp of when that email was sent (NULL until it is sent).
    email_sent_at = Column(DateTime, nullable=True)

def init_db():
    Base.metadata.create_all(bind=engine)
