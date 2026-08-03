-- Per-transaction feature table for the fraud model. Trailing-window features
-- only use information available at event_ts — with one exception:
-- chargebacks_next_30d aggregates chargebacks filed AFTER event_ts. It is the
-- planted target-leakage feature this demo's auditor must catch.

with txns as (

    select * from {{ ref('stg_transactions') }}

),

trailing_window as (

    -- 30-day trailing activity per transaction (inclusive of the current one).
    select
        t.txn_id,
        count(t2.txn_id)            as txn_count_30d,
        avg(t2.amount_usd)          as avg_amount_30d,
        count(distinct t2.merchant) as distinct_merchants_30d
    from txns t
    left join txns t2
      on t2.customer_id = t.customer_id
     and t2.event_ts <= t.event_ts
     and t2.event_ts >  t.event_ts - interval 30 day
    group by t.txn_id

),

future_chargebacks as (

    -- LEAK: counts this customer's chargebacks in the 30 days AFTER the
    -- transaction. Chargebacks are filed days-to-weeks after a fraudulent
    -- transaction, so this feature contains the outcome itself.
    select
        t.txn_id,
        count(cb.txn_id) as chargebacks_next_30d
    from txns t
    left join {{ ref('stg_chargebacks') }} cb
      on cb.customer_id = t.customer_id
     and cb.chargeback_ts >= t.event_ts
     and cb.chargeback_ts <  t.event_ts + interval 30 day
    group by t.txn_id

)

select
    t.txn_id,
    t.customer_id,
    t.event_ts,
    tr.txn_count_30d,
    tr.avg_amount_30d,
    tr.distinct_merchants_30d,
    case t.country
        when 'US' then 0.10
        when 'GB' then 0.10
        when 'DE' then 0.15
        when 'IN' then 0.30
        when 'BR' then 0.40
        when 'NG' then 0.70
        when 'RU' then 0.80
        else 0.50
    end as country_risk,
    coalesce(fcb.chargebacks_next_30d, 0) as chargebacks_next_30d,
    l.is_fraud
from txns t
join trailing_window tr on tr.txn_id = t.txn_id
join future_chargebacks fcb on fcb.txn_id = t.txn_id
left join {{ source('warehouse', 'labels') }} l on l.txn_id = t.txn_id
