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
    python3 reset_keep_products.py            # asks for confirmation
    python3 reset_keep_products.py --yes      # skip the prompt
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

    if "--yes" not in sys.argv:
        print("This will DELETE all customers, suppliers, invoices, purchases,")
        print("ledgers, journal and accounting — keeping only PRODUCTS and logins.")
        if input("Type NUKE to proceed: ").strip() != "NUKE":
            print("Aborted.")
            sys.exit(0)

    mysql_db.init_workbook()  # ensure schema exists

    conn = pymysql.connect(
        host=config.MYSQL_HOST, port=config.MYSQL_PORT, user=config.MYSQL_USER,
        password=config.MYSQL_PASSWORD, database=config.MYSQL_DATABASE,
        charset="utf8mb4", cursorclass=pymysql.cursors.DictCursor,
    )
    try:
        with conn.cursor() as cur:
            cur.execute("SET FOREIGN_KEY_CHECKS=0")
            for tbl in _WIPE:
                cur.execute(f"DELETE FROM {tbl}")
                print(f"  cleared {tbl}: {cur.rowcount} rows")
            cur.execute("SET FOREIGN_KEY_CHECKS=1")
            cur.execute("SELECT tenant_id, name FROM tenants")
            tenants = cur.fetchall()
        conn.commit()
    finally:
        conn.close()

    # Re-seed a clean standard chart of accounts for every business.
    for t in tenants:
        tenant.set_current_tenant(t["tenant_id"])
        mysql_db.seed_chart_of_accounts()
        print(f"  re-seeded chart of accounts for tenant #{t['tenant_id']} ({t['name']})")

    print("\nDone. Products and logins kept; everything else reset.")


if __name__ == "__main__":
    main()
