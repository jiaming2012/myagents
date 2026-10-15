"""Dump toast_xero's previewed deposits (per Mercury credit, with Xero-coded
line items) to JSON for zoho_books_recon.py. Read-only apart from toast_xero's
own Mercury cache. Needs the toast_xero checkout at ~/projects/yumyums/accounting/xero
with its inputs/ exports present (`task toast:export` there to add sales days).

Usage: python toast_preview_dump.py FROM TO OUT.json   (see `task zoho:toast-dump`)"""
import json
import os
import sys
from datetime import date
from decimal import Decimal
from pathlib import Path

REPO = Path.home() / "projects/yumyums/accounting/xero"
fd, td, out = date.fromisoformat(sys.argv[1]), date.fromisoformat(sys.argv[2]), Path(sys.argv[3]).resolve()
sys.path.insert(0, str(REPO))
os.chdir(REPO)  # toast_xero resolves inputs/ relative to its package

from toast_xero import mapping as mapping_mod  # noqa: E402
from toast_xero.preview import collect  # noqa: E402

m = mapping_mod.load()
try:
    res = collect(m, from_date=fd, to_date=td)
except RuntimeError as e:
    print("collect() hard-failed:", str(e)[:2000])
    sys.exit(2)

deps = []
for d in res.deposits:
    deps.append({
        "settle_date": d.settle_date.isoformat() if d.settle_date else None,
        "bank_credit_date": d.bank_credit_date.isoformat() if d.bank_credit_date else None,
        "sales_dates": [s.isoformat() for s in d.sales_dates],
        "total_payouts": str(d.total_payouts),
        "signed_sum": str(d.signed_sum),
        "unreconciled_diff": str(d.unreconciled_diff) if d.unreconciled_diff is not None else None,
        "split_remainder": d.split_remainder,
        "has_problems": d.has_problems,
        "unmapped": [[c, str(a)] for c, a in d.unmapped],
        "line_items": [{"category": li.category, "code": li.xero_code, "amount": str(li.amount)}
                       for li in d.line_items if li.amount != Decimal("0")],
    })
summary = {
    "from": fd.isoformat(), "to": td.isoformat(),
    "deposits": deps,
    "pending_settlement": [{"sales_date": r.sales_date.isoformat(), "payouts": str(r.payouts)} for r in res.pending_settlement],
    "missing_sales_summary": [[s.isoformat(), [x.isoformat() for x in xs]] for s, xs in res.missing_sales_summary],
}
out.write_text(json.dumps(summary, indent=1))
print(f"deposits={len(deps)} problems={sum(1 for d in deps if d['has_problems'])} "
      f"pending={len(summary['pending_settlement'])} missing={len(summary['missing_sales_summary'])} -> {out}")
for d in deps[:3]:
    print(json.dumps(d)[:400])
if summary["missing_sales_summary"]:
    print("missing:", summary["missing_sales_summary"][:10])
if summary["pending_settlement"]:
    print("pending:", summary["pending_settlement"][:10])
