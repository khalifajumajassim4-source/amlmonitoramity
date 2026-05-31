import os
from collections import defaultdict, deque
from datetime import datetime, timedelta
from decimal import Decimal

def parse_set(env_value: str):
    return {x.strip().upper() for x in env_value.split(",") if x.strip()} if env_value else set()

class RuleResult:
    def __init__(self):
        self.triggered = set()
        self.reasons = []
        self.score = 0

    def add(self, rule_name: str, weight: int, reason: str):
        if rule_name not in self.triggered:
            self.triggered.add(rule_name)
            self.score += weight
            self.reasons.append(f"[{rule_name}] {reason}")

def apply_rules(transactions, now=None):
    if not now:
        now = datetime.now()
    ctr_cash_threshold = Decimal(os.getenv("CTR_CASH_THRESHOLD", "10000"))
    structuring_min = Decimal(os.getenv("STRUCTURING_MIN", "8000"))
    structuring_max = Decimal(os.getenv("STRUCTURING_MAX", "9999.99"))
    structuring_window_days = int(os.getenv("STRUCTURING_WINDOW_DAYS", "7"))
    structuring_total = Decimal(os.getenv("STRUCTURING_TOTAL", "20000"))
    suspicious_amount_floor = Decimal(os.getenv("SUSPICIOUS_AMOUNT_FLOOR", "5000"))
    velocity_window_minutes = int(os.getenv("VELOCITY_WINDOW_MINUTES", "60"))
    velocity_txn_count = int(os.getenv("VELOCITY_TXN_COUNT", "8"))
    velocity_total = Decimal(os.getenv("VELOCITY_TOTAL", "25000"))
    round_modulus = Decimal(os.getenv("ROUND_AMOUNT_MODULUS", "1000"))
    round_min_occ = int(os.getenv("ROUND_AMOUNT_MIN_OCCURRENCES", "3"))
    round_window_days = int(os.getenv("ROUND_AMOUNT_WINDOW_DAYS", "3"))
    unusual_hour_start = int(os.getenv("UNUSUAL_HOUR_START", "0"))
    unusual_hour_end = int(os.getenv("UNUSUAL_HOUR_END", "5"))
    dormant_months = int(os.getenv("DORMANT_MONTHS", "6"))
    dormant_burst_txns = int(os.getenv("DORMANT_BURST_TXNS", "4"))
    dormant_burst_total = Decimal(os.getenv("DORMANT_BURST_TOTAL", "15000"))
    rapid_flow_window_hours = int(os.getenv("RAPID_FLOW_WINDOW_HOURS", "48"))
    rapid_flow_min_in = Decimal(os.getenv("RAPID_FLOW_MIN_IN", "10000"))
    rapid_flow_ratio = Decimal(os.getenv("RAPID_FLOW_RATIO", "0.80"))
    fan_out_min_recipients = int(os.getenv("FAN_OUT_MIN_RECIPIENTS", "5"))
    fan_out_window_days = int(os.getenv("FAN_OUT_WINDOW_DAYS", "2"))
    high_risk_countries = parse_set(os.getenv("HIGH_RISK_COUNTRIES", "IR,KP,SY,CU,RU"))
    weights = {
        "CTR_CASH": int(os.getenv("WEIGHT_CTR_CASH", "20")),
        "STRUCTURING": int(os.getenv("WEIGHT_STRUCTURING", "35")),
        "VELOCITY": int(os.getenv("WEIGHT_VELOCITY", "20")),
        "ROUND_AMOUNTS": int(os.getenv("WEIGHT_ROUND_AMOUNTS", "10")),
        "HIGH_RISK_GEO": int(os.getenv("WEIGHT_HIGH_RISK_GEO", "20")),
        "UNUSUAL_HOURS": int(os.getenv("WEIGHT_UNUSUAL_HOURS", "10")),
        "DORMANT_BURST": int(os.getenv("WEIGHT_DORMANT_BURST", "20")),
        "RAPID_FLOW": int(os.getenv("WEIGHT_RAPID_FLOW", "25")),
        "FAN_OUT": int(os.getenv("WEIGHT_FAN_OUT", "15")),
    }
    res = RuleResult()
    contributing_txns = set()
    if not transactions:
        return res, []
    txns_sorted = sorted(transactions, key=lambda t: t["occurred_at"])
    cash_by_day = defaultdict(list)
    for t in txns_sorted:
        if t["is_cash"]:
            cash_by_day[t["occurred_at"].date()].append(t)
    for day, items in cash_by_day.items():
        total = sum((t["amount"] for t in items), Decimal("0"))
        if total > ctr_cash_threshold:
            res.add("CTR_CASH", weights["CTR_CASH"], f"Cash activity totaled {total} on {day}, above {ctr_cash_threshold} in one day.")
            contributing_txns.update(t["id"] for t in items)
    start = now - timedelta(days=structuring_window_days)
    s_txns = [t for t in txns_sorted if start <= t["occurred_at"] <= now and t["direction"] == "in" and t["is_cash"] and structuring_min <= t["amount"] <= structuring_max]
    s_sum = sum((t["amount"] for t in s_txns), Decimal("0"))
    if len(s_txns) >= 2 and s_sum >= structuring_total:
        res.add("STRUCTURING", weights["STRUCTURING"], f"{len(s_txns)} cash deposits between {structuring_min} and {structuring_max} totaled {s_sum} in {structuring_window_days} days.")
        contributing_txns.update(t["id"] for t in s_txns)
    win = deque()
    for t in txns_sorted:
        while win and (t["occurred_at"] - win[0]["occurred_at"]).total_seconds() > velocity_window_minutes * 60:
            win.popleft()
        win.append(t)
        win_total = sum((x["amount"] for x in win), Decimal("0"))
        if len(win) >= velocity_txn_count or win_total >= velocity_total:
            res.add("VELOCITY", weights["VELOCITY"], f"{len(win)} transactions totaling {win_total} within {velocity_window_minutes} minutes.")
            contributing_txns.update(x["id"] for x in win)
            break
    r_start = now - timedelta(days=round_window_days)
    r_txns = [t for t in txns_sorted if r_start <= t["occurred_at"] <= now and t["amount"] >= suspicious_amount_floor and t["amount"] % round_modulus == 0]
    if len(r_txns) >= round_min_occ:
        res.add("ROUND_AMOUNTS", weights["ROUND_AMOUNTS"], f"{len(r_txns)} transactions were exact multiples of {round_modulus} within {round_window_days} days.")
        contributing_txns.update(t["id"] for t in r_txns)
    geo_txns = [t for t in txns_sorted if (t.get("counterparty_country") or "").upper() in high_risk_countries]
    if geo_txns:
        countries = sorted({(t["counterparty_country"] or "").upper() for t in geo_txns})
        res.add("HIGH_RISK_GEO", weights["HIGH_RISK_GEO"], f"Transactions involved configured high-risk countries: {', '.join(countries)}.")
        contributing_txns.update(t["id"] for t in geo_txns)
    u_txns = [t for t in txns_sorted if unusual_hour_start <= t["occurred_at"].hour <= unusual_hour_end and t["amount"] >= suspicious_amount_floor]
    if len(u_txns) >= 2:
        res.add("UNUSUAL_HOURS", weights["UNUSUAL_HOURS"], f"{len(u_txns)} suspicious-value transactions occurred during unusual hours.")
        contributing_txns.update(t["id"] for t in u_txns)
    dormant_gap = timedelta(days=30 * dormant_months)
    for i in range(1, len(txns_sorted)):
        gap = txns_sorted[i]["occurred_at"] - txns_sorted[i - 1]["occurred_at"]
        if gap >= dormant_gap:
            burst_start = txns_sorted[i]["occurred_at"]
            burst_end = burst_start + timedelta(days=7)
            burst = [t for t in txns_sorted if burst_start <= t["occurred_at"] <= burst_end]
            burst_total = sum((t["amount"] for t in burst), Decimal("0"))
            if len(burst) >= dormant_burst_txns or burst_total >= dormant_burst_total:
                res.add("DORMANT_BURST", weights["DORMANT_BURST"], f"After {dormant_months}+ months of inactivity, {len(burst)} transactions totaling {burst_total} appeared within 7 days.")
                contributing_txns.update(t["id"] for t in burst)
            break
    incoming = [t for t in txns_sorted if t["direction"] == "in"]
    outgoing = [t for t in txns_sorted if t["direction"] == "out"]
    for in_txn in incoming:
        if in_txn["amount"] < rapid_flow_min_in:
            continue
        end = in_txn["occurred_at"] + timedelta(hours=rapid_flow_window_hours)
        related_out = [t for t in outgoing if in_txn["occurred_at"] <= t["occurred_at"] <= end]
        out_total = sum((t["amount"] for t in related_out), Decimal("0"))
        if related_out and out_total >= (in_txn["amount"] * rapid_flow_ratio):
            res.add("RAPID_FLOW", weights["RAPID_FLOW"], f"Incoming {in_txn['amount']} was followed by outgoing total {out_total} within {rapid_flow_window_hours} hours.")
            contributing_txns.add(in_txn["id"])
            contributing_txns.update(t["id"] for t in related_out)
            break
    f_start = now - timedelta(days=fan_out_window_days)
    recent_out = [t for t in txns_sorted if t["direction"] == "out" and f_start <= t["occurred_at"] <= now]
    counterparties = {t.get("counterparty_id") for t in recent_out if t.get("counterparty_id")}
    if len(counterparties) >= fan_out_min_recipients:
        res.add("FAN_OUT", weights["FAN_OUT"], f"Funds were sent to {len(counterparties)} distinct counterparties in {fan_out_window_days} days.")
        contributing_txns.update(t["id"] for t in recent_out)
    return res, sorted(contributing_txns)
