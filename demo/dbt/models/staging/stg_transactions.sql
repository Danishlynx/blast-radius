-- Standardized transaction stream. Passes amounts through in US dollars.
select
    txn_id,
    customer_id,
    merchant,
    country,
    amount / 100.0 as amount_usd,
    event_ts
from {{ source('warehouse', 'raw_transactions') }}
