"""
DESTRUCTIVE one-off: wipe everything EXCEPT products (and logins/businesses),
then re-seed a fresh standard chart of accounts per business.

Keeps:   products, users, tenants
Removes:  customers, suppliers, invoices + items, credit ledger,
          purchases + items, supplier ledger, journal, partners, and the
          old (possibly incomplete) chart of accounts.

Take a backup first:
    mysqldump -u root -p pos > ~/pos_backup_$(date +%F).sql

Run:
    python3 reset_keep_products.py                        # wipe ALL tenants, asks for confirmation
    python3 reset_keep_products.py --tenant-id 2          # wipe only tenant #2
    python3 reset_keep_products.py --tenant-id 2 --yes   # skip the prompt
"""

import sys
import pymysql

import config
import mysql_db
import tenant

# Child tables first so FK order is safe (we also disable FK checks to be sure).
_WIPE = [
    "journal_lines", "journal_entries",
    "invoice_items", "invoices", "credit_ledger",
    "purchase_invoice_items", "purchase_invoices", "supplier_ledger",
    "partner_transactions", "partners",
    "customers", "suppliers",
    "accounts",
]


def main():
    if config.DB_BACKEND != "mysql":
        print("This script targets the MySQL backend. Set DB_BACKEND=mysql.")
        sys.exit(1)

    # Parse --tenant-id N
    target_tid = None
    if "--tenant-id" in sys.argv:
        idx = sys.argv.index("--tenant-id")
        try:
            target_tid = int(sys.argv[idx + 1])
        except (IndexError, ValueError):
            print("Usage: --tenant-id <integer>")
            sys.exit(1)

    mysql_db.init_workbook()  # ensure schema exists

    conn = pymysql.connect(
        host=config.MYSQL_HOST, port=config.MYSQL_PORT, user=config.MYSQL_USER,
        password=config.MYSQL_PASSWORD, database=config.MYSQL_DATABASE,
        charset="utf8mb4", cursorclass=pymysql.cursors.DictCursor,
    )
    try:
        with conn.cursor() as cur:
            # Resolve tenant name for confirmation message
            if target_tid is not None:
                cur.execute("SELECT tenant_id, name FROM tenants WHERE tenant_id=%s", (target_tid,))
                row = cur.fetchone()
                if not row:
                    print(f"Tenant #{target_tid} not found.")
                    sys.exit(1)
                target_tenants = [row]
                scope = f"tenant #{target_tid} ({row['name']})"
            else:
                cur.execute("SELECT tenant_id, name FROM tenants")
                target_tenants = cur.fetchall()
                scope = "ALL tenants"

            if "--yes" not in sys.argv:
                print(f"This will DELETE all customers, suppliers, invoices, purchases,")
                print(f"ledgers, journal and accounting for {scope} — keeping only PRODUCTS and logins.")
                if input("Type NUKE to proceed: ").strip() != "NUKE":
                    print("Aborted.")
                    sys.exit(0)

            cur.execute("SET FOREIGN_KEY_CHECKS=0")
            for tbl in _WIPE:
                if target_tid is not None:
                    cur.execute(f"DELETE FROM {tbl} WHERE tenant_id=%s", (target_tid,))
                else:
                    cur.execute(f"DELETE FROM {tbl}")
                print(f"  cleared {tbl}: {cur.rowcount} rows")
            cur.execute("SET FOREIGN_KEY_CHECKS=1")
        conn.commit()
    finally:
        conn.close()

    # Re-seed a clean standard chart of accounts for targeted tenants.
    for t in target_tenants:
        tenant.set_current_tenant(t["tenant_id"])
        mysql_db.seed_chart_of_accounts()
        print(f"  re-seeded chart of accounts for tenant #{t['tenant_id']} ({t['name']})")

    print(f"\nDone. Products and logins kept; everything else reset for {scope}.")


if __name__ == "__main__":
    main()
