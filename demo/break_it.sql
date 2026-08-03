-- The poison migration. Another team "cleans up" the transactions table:
--   * amount_usd (float, dollars)  ->  amount (bigint, CENTS)
-- Nothing crashes. Every downstream consumer that assumed dollars is now
-- silently off by 100x, and every query referencing amount_usd is broken.
create or replace table raw_transactions as
select
    txn_id,
    customer_id,
    merchant,
    country,
    cast(round(amount_usd * 100) as bigint) as amount,
    event_ts
from raw_transactions;
