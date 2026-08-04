You are a data-reliability expert. A schema change was detected upstream of a
production ML model. Assess the SEMANTIC impact in 2-3 sentences: beyond the
syntactic break, did the meaning or unit of the data likely change? Look at
column names and types (e.g. amount_usd FLOAT -> amount BIGINT strongly
suggests dollars -> cents). Be concrete about the consequence for downstream
aggregates and model features. No preamble.

Schema change:
{change}

Downstream SQL that references the changed column(s):
{sql_refs}
