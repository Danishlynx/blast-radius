

---
## 🧬 Leakage audit (Blast Radius)
Structural audit of `fraud_model_1` — label `is_fraud`, prediction time `event_ts`. Rules: L1 direct label, L2 post-outcome lineage, L3 forward-looking SQL. Lineage-evidence based, not a statistical guarantee.

| feature | verdict | rule | why |
|---|---|---|---|
| `avg_amount_30d` | 🟢 CLEAN | — | no leakage signal |
| `chargebacks_next_30d` | 🔴 LEAK | L2 | lineage crosses `warehouse.main.chargebacks` (tagged post-outcome) via `txn_id` — information from after the prediction timestamp |
| `country_risk` | 🟢 CLEAN | — | no leakage signal |
| `distinct_merchants_30d` | 🟢 CLEAN | — | no leakage signal |
| `txn_count_30d` | 🟢 CLEAN | — | no leakage signal |

**chargebacks_next_30d path:** `warehouse.main.fct_customer_features.chargebacks_next_30d → warehouse.main.stg_chargebacks.txn_id → warehouse.main.chargebacks.txn_id`
```sql
-- Per-transaction feature table for the fraud model. Trailing-window features
-- only use information available at event_ts — with one exception:
-- chargebacks_next_30d aggregates chargebacks filed AFTER event_ts. It is the
-- planted target-leakage feature this demo's auditor must catch.
...
-- transaction. Chargebacks are filed days-to-weeks after a fraudulent
    -- transaction, so this feature contains the outcome itself.
    select
        t.txn_id,
        count(cb.txn_id) as chargebacks_next_30d
    from txns t
    left join {{ ref('stg_chargebacks') }} cb
...
when 'NG' then 0.70
        when 'RU' then 0.80
        else 0.50
    end as country_risk,
    coalesce(fcb.chargebacks_next_30d, 0) as chargebacks_next_30d,
    l.is_fraud
from txns t
```
_Remediation:_ recompute `chargebacks_next_30d` using only data available at `event_ts`, or drop it and retrain.

`evidence_hash: 1e8e42fe90fc0bf536d6ed64d89eb71ff071df55d9f69cb31fa9f551dc46e94c`