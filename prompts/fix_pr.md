You are fixing dbt models after an upstream schema change broke them. Output
the COMPLETE corrected contents of every file, preserving the downstream-facing
column names (use `<new_column> as <old_column>` aliases) and correcting units
when the change implies one (e.g. dollars stored as cents after a float ->
integer narrowing means dividing by 100.0). Change nothing else — no
reformatting, no new logic.

Respond with one section per file, exactly:

### <file path>
```sql
<full corrected file contents>
```

Schema change:
{change}

Current files:
{files}

Previous validation error (fix it if not "none"):
{error}
