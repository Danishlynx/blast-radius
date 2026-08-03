"""Generate the synthetic fintech warehouse (demo/warehouse.duckdb).

Deterministic (fixed RNG seed). Four raw tables:
  customers        — 1,000 customers
  raw_transactions — 50,000 transactions with amount_usd (dollars) + event_ts
  chargebacks      — ~2,000 chargebacks; chargeback_ts is strictly AFTER the
                     related transaction's event_ts (the post-outcome table
                     that powers the planted target-leakage feature)
  labels           — is_fraud per transaction

The fraud signal is real: foreign-country transactions, unusual amounts, and
risky merchants raise fraud probability, so the trailing-window features in
dbt carry genuine signal and the model learns something meaningful.
"""

from __future__ import annotations

from datetime import datetime, timedelta
from pathlib import Path

import duckdb
import numpy as np
import pandas as pd

RNG = np.random.default_rng(42)
DB_PATH = Path(__file__).resolve().parents[1] / "warehouse.duckdb"

N_CUSTOMERS = 1_000
N_TXNS = 50_000

COUNTRIES = ["US", "GB", "DE", "IN", "BR", "NG", "RU"]
COUNTRY_WEIGHTS = [0.40, 0.15, 0.12, 0.12, 0.09, 0.07, 0.05]
COUNTRY_RISK = {"US": 0.1, "GB": 0.1, "DE": 0.15, "IN": 0.3, "BR": 0.4, "NG": 0.7, "RU": 0.8}

MERCHANTS = [
    ("grocery_mart", 0.05), ("coffee_corner", 0.03), ("steam_games", 0.25),
    ("luxe_watches", 0.55), ("cloud_hosting", 0.15), ("air_travel_co", 0.30),
    ("crypto_exchange", 0.70), ("book_nook", 0.02), ("fuel_station", 0.05),
    ("gift_cards_now", 0.65),
]

WINDOW_START = datetime(2025, 1, 1)
WINDOW_DAYS = 180


def build_customers() -> pd.DataFrame:
    created = [
        datetime(2024, 1, 1) + timedelta(days=float(d))
        for d in RNG.uniform(0, 540, N_CUSTOMERS)
    ]
    return pd.DataFrame(
        {
            "customer_id": [f"C{i:05d}" for i in range(N_CUSTOMERS)],
            "country": RNG.choice(COUNTRIES, N_CUSTOMERS, p=COUNTRY_WEIGHTS),
            "created_at": created,
        }
    )


def build_transactions(customers: pd.DataFrame) -> pd.DataFrame:
    # A minority of "hot" customers transact much more — gives the trailing
    # count/velocity features real variance.
    activity = RNG.lognormal(0, 1.0, N_CUSTOMERS)
    cust_idx = RNG.choice(N_CUSTOMERS, N_TXNS, p=activity / activity.sum())

    merchant_names = [m for m, _ in MERCHANTS]
    merchant_idx = RNG.integers(0, len(MERCHANTS), N_TXNS)

    home = customers["country"].to_numpy()[cust_idx]
    foreign = RNG.random(N_TXNS) < 0.12
    txn_country = np.where(foreign, RNG.choice(COUNTRIES, N_TXNS), home)

    amounts = np.round(RNG.lognormal(3.4, 1.1, N_TXNS), 2)  # median ~$30
    event_ts = [
        WINDOW_START + timedelta(days=float(d))
        for d in np.sort(RNG.uniform(0, WINDOW_DAYS, N_TXNS))
    ]

    return pd.DataFrame(
        {
            "txn_id": [f"T{i:07d}" for i in range(N_TXNS)],
            "customer_id": customers["customer_id"].to_numpy()[cust_idx],
            "merchant": np.array(merchant_names)[merchant_idx],
            "country": txn_country,
            "amount_usd": amounts,
            "event_ts": event_ts,
            "_merchant_risk": np.array([r for _, r in MERCHANTS])[merchant_idx],
            "_is_foreign": foreign,
        }
    )


def build_labels(txns: pd.DataFrame) -> pd.DataFrame:
    # Fraud odds rise with merchant risk, foreign use, destination-country
    # risk, and abnormal amounts.
    z = (
        -4.6
        + 2.2 * txns["_merchant_risk"].to_numpy()
        + 1.6 * txns["_is_foreign"].to_numpy()
        + 1.4 * txns["country"].map(COUNTRY_RISK).to_numpy()
        + 0.5 * (np.log1p(txns["amount_usd"].to_numpy()) - 3.4)
    )
    p = 1 / (1 + np.exp(-z))
    is_fraud = RNG.random(len(txns)) < p
    return pd.DataFrame({"txn_id": txns["txn_id"], "is_fraud": is_fraud})


def build_chargebacks(txns: pd.DataFrame, labels: pd.DataFrame) -> pd.DataFrame:
    merged = txns[["txn_id", "event_ts"]].merge(labels, on="txn_id")
    fraud = merged[merged["is_fraud"]]
    clean = merged[~merged["is_fraud"]]

    # ~70% of fraud is charged back; a sliver of legit txns too (disputes).
    cb_fraud = fraud.sample(frac=0.70, random_state=7)
    cb_clean = clean.sample(n=min(len(clean), max(1, len(cb_fraud) // 12)), random_state=7)
    cb = pd.concat([cb_fraud, cb_clean], ignore_index=True)

    # Chargebacks land 3–25 days AFTER the transaction: strictly post-outcome.
    delays = RNG.uniform(3, 25, len(cb))
    cb["chargeback_ts"] = [
        ts + timedelta(days=float(d)) for ts, d in zip(cb["event_ts"], delays)
    ]
    return cb[["txn_id", "chargeback_ts"]].sort_values("txn_id").reset_index(drop=True)


def main() -> None:
    customers = build_customers()
    txns = build_transactions(customers)
    labels = build_labels(txns)
    chargebacks = build_chargebacks(txns, labels)
    raw_txns = txns[["txn_id", "customer_id", "merchant", "country", "amount_usd", "event_ts"]]

    DB_PATH.parent.mkdir(parents=True, exist_ok=True)
    if DB_PATH.exists():
        DB_PATH.unlink()

    con = duckdb.connect(str(DB_PATH))
    con.register("customers_df", customers)
    con.register("raw_transactions_df", raw_txns)
    con.register("chargebacks_df", chargebacks)
    con.register("labels_df", labels)
    con.execute("CREATE TABLE customers AS SELECT * FROM customers_df")
    con.execute("CREATE TABLE raw_transactions AS SELECT * FROM raw_transactions_df")
    con.execute("CREATE TABLE chargebacks AS SELECT * FROM chargebacks_df")
    con.execute("CREATE TABLE labels AS SELECT * FROM labels_df")
    con.close()

    fraud_rate = labels["is_fraud"].mean()
    print(f"seeded {DB_PATH}")
    print(
        f"  customers={len(customers):,}  raw_transactions={len(raw_txns):,}  "
        f"chargebacks={len(chargebacks):,}  labels={len(labels):,}  fraud_rate={fraud_rate:.2%}"
    )


if __name__ == "__main__":
    main()
