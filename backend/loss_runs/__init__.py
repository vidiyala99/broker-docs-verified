"""Slice 5a: loss runs (docs/specs/2026-11-03-loss-runs-design.md, eng review ledger R1 to R8, O1 to O7).

    normalize.py  rules as data: money to cents, dates to ISO, status map, claim and carrier keys
    headers.py    the header synonym table (data) and header matching
    sheets.py     spreadsheets parsed in code (xlsx / csv), printed totals, parsed-rows cache, sheet pages
    pdf_rows.py   PDF claim rows by the model per 2-page window, verified by band and column
    ledger.py     one pure build_ledger: roles, effective values, totals, reconciliation, checks, notices
    report.py     glue: rows from the database -> build_ledger -> derived columns + report artifact
"""
