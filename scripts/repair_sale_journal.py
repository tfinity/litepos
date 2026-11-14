#!/usr/bin/env python3
"""One-time maintenance: rebuild the accounting entries for sales, for every tenant.

Editing an already-printed invoice used to update the invoice but leave its
accounting (journal) entry as originally posted, so the balance sheet and the
Cash/Bank ledgers kept the pre-edit amounts (fixed going forward: update_invoice
now drops the stale entry). This script repairs books that already drifted.

Sale entries are derived data -- the app rebuilds them from the invoices on the
next accounting page view -- so repair = delete them and let the normal sync
re-post them from each invoice's current values. Opening balances, expenses,
Add/Remove Funds, purchases and payments are not touched.

It also reports invoices whose stored total doesn't equal
subtotal - discount + tax + delivery (a second bug: editing used to drop the
delivery charge from the total). Those are only reported, never changed.

BACK UP THE DATABASE FIRST (mysqldump) before using --apply.

Usage:
    python3 scripts/repair_sale_journal.py            # dry run, reports only
    python3 scripts/repair_sale_journal.py --apply    # rebuild sale entries
"""
import argparse
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import config
import db
import tenant

WATCH = [("1000", "Cash"), ("1010", "Bank"), ("1200", "Inventory"), ("4000", "Sales"), ("5000", "COGS")]


def balances():
    by_code = {str(a["code"]): a["account_id"] for a in db.get_all_accounts()}
    return {code: db.get_account_balance(by_code[code]) for code, _ in WATCH if code in by_code}


def total_mismatches():
    out = []
    for inv in db.get_all_invoices():
        net = float(inv.get("subtotal") or 0) - float(inv.get("discount_total") or 0)
        expected = round(net + float(inv.get("tax_amount") or 0) + float(inv.get("delivery_charges") or 0), 2)
        stored = round(float(inv.get("total") or 0), 2)
        if abs(expected - stored) > 0.01:
            out.append((inv["invoice_id"], stored, expected))
    return out


def process_tenant(tenant_id, label, apply):
    tenant.set_current_tenant(tenant_id)
    invoices = db.get_all_invoices()
    print(f"[{label}] {len(invoices)} active invoice(s)")

    for iid, stored, expected in total_mismatches():
        print(f"  WARNING INV-{iid}: stored total {stored:,.2f} but items+tax+delivery = {expected:,.2f} "
              f"(delivery dropped by an old edit?) -- not changed, review manually")

    if not apply:
        return len(invoices)

    db.sync_journal_from_operations()  # make sure books are current before the 'before' snapshot
    before = balances()
    removed = db.clear_sale_journal_entries()
    posted = db.sync_journal_from_operations()
    after = balances()
    print(f"  removed {removed} sale entries, re-posted {posted}")
    for code, name in WATCH:
        if code in before and abs(before[code] - after[code]) > 0.005:
            print(f"  {name:<10} {before[code]:>14,.2f} -> {after[code]:>14,.2f}  ({after[code]-before[code]:+,.2f})")
    return len(invoices)


def main():
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--apply", action="store_true", help="Actually rebuild (default: dry run/report only)")
    args = parser.parse_args()

    print(f"Backend: {config.DB_BACKEND}")
    print("Mode: " + ("APPLY (rebuilding sale entries)" if args.apply else "DRY RUN (report only, use --apply to fix)"))
    print()

    tenants = db.get_all_tenants()
    if tenants:
        for t in tenants:
            process_tenant(t["tenant_id"], t["name"], args.apply)
    elif config.DB_BACKEND != "mysql":
        process_tenant(None, "default", args.apply)
    else:
        print("No tenants found.")
    print()
    if not args.apply:
        print("Dry run only. Re-run with --apply (after a backup) to rebuild the sale entries.")


if __name__ == "__main__":
    main()
