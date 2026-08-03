-- Standardized chargebacks joined to the customer who made the disputed
-- transaction. chargeback_ts is always AFTER the disputed transaction.
select
    cb.txn_id,
    t.customer_id,
    cb.chargeback_ts
from {{ source('warehouse', 'chargebacks') }} cb
join {{ source('warehouse', 'raw_transactions') }} t
  on t.txn_id = cb.txn_id
