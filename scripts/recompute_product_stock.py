#!/usr/bin/env python3
"""One-time maintenance: recompute every product's cached quantity/cost from
its batch stock, for every tenant.

Products.quantity is supposed to always equal the sum of its ProductBatches'
remaining stock. update_product() used to skip this and just store whatever
the edit form submitted, so re-saving a product could silently leave the
cache out of sync with what invoicing actually sells from (see commit
dcceb67, fixed going forward). This script does a one-time sweep to correct
any product that already drifted before that fix.

Products with no batch history at all are left untouched -- their quantity
is manually managed and there's nothing to derive it from.

Usage:
    python3 scripts/recompute_product_stock.py            # dry run, reports only
    python3 scripts/recompute_product_stock.py --apply     # actually fixes them
"""
import argparse
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import config
import db
import tenant


def process_tenant(tenant_id, tenant_label, apply):
    tenant.set_current_tenant(tenant_id)
    products = db.get_all_products()
    batches = db.get_product_batches()
    by_pid = {}
    for b in batches:
        by_pid.setdefault(b["product_id"], []).append(b)

    fixed = 0
    for p in products:
        pid = p["product_id"]
        if pid not in by_pid:
            continue  # no batch history -- quantity is manually managed, leave alone
        true_qty = sum(int(b["qty_remaining"] or 0) for b in by_pid[pid])
        cached_qty = int(p["quantity"] or 0)
        if cached_qty == true_qty:
            continue
        print(f"  [{tenant_label}] #{pid} {p['name']!r}: cached={cached_qty} actual={true_qty}"
              + ("" if apply else "  (dry run, not changed)"))
        if apply:
            # Re-saving through update_product triggers the batch-derived
            # recompute now wired into it (dcceb67) -- reuses the exact
            # tested code path instead of poking internals directly.
            db.update_product(pid, {
                "name": p["name"],
                "purchase_price": p["purchase_price"],
                "counter_price": p["counter_price"],
                "retail_price": p["retail_price"],
                "quantity": cached_qty,  # ignored for batch-tracked products
                "barcode": p.get("barcode", ""),
                "expiry_date": p.get("expiry_date") or "",
                "category": p.get("category", ""),
            })
        fixed += 1
    return fixed


def main():
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--apply", action="store_true", help="Actually write fixes (default: dry run/report only)")
    args = parser.parse_args()

    print(f"Backend: {config.DB_BACKEND}")
    print("Mode: " + ("APPLY (writing fixes)" if args.apply else "DRY RUN (report only, use --apply to fix)"))
    print()

    tenants = db.get_all_tenants()
    total_fixed = 0
    if tenants:
        for t in tenants:
            total_fixed += process_tenant(t["tenant_id"], t["name"], args.apply)
    elif config.DB_BACKEND != "mysql":
        total_fixed += process_tenant(None, "default", args.apply)
    else:
        print("No tenants found.")

    print()
    if total_fixed == 0:
        print("No drift found. Nothing to fix.")
    elif args.apply:
        print(f"Fixed {total_fixed} product(s).")
    else:
        print(f"Found {total_fixed} product(s) with drift. Re-run with --apply to fix them.")


if __name__ == "__main__":
    main()
