"""
One-time migration: imports all data from data.xlsx into MySQL.
Run ONCE after setting DB_BACKEND=mysql in .env.

Usage:
    python migrate_to_mysql.py
"""

import sys
from datetime import datetime
from pathlib import Path

# Force MySQL backend for this script regardless of .env
import os
os.environ["DB_BACKEND"] = "mysql"

import mysql_db as db
from excel_db import (
    get_all_products,
    get_all_customers,
    get_all_invoices,
    get_invoice_items,
    get_credit_ledger,
    DATA_FILE,
)
import pymysql
from config import MYSQL_HOST, MYSQL_PORT, MYSQL_USER, MYSQL_PASSWORD, MYSQL_DATABASE


def _raw_conn():
    return pymysql.connect(
        host=MYSQL_HOST, port=MYSQL_PORT,
        user=MYSQL_USER, password=MYSQL_PASSWORD,
        database=MYSQL_DATABASE, charset="utf8mb4",
        cursorclass=pymysql.cursors.DictCursor, autocommit=False,
    )


def migrate():
    if not DATA_FILE.exists():
        print("ERROR: data.xlsx not found. Nothing to migrate.")
        sys.exit(1)

    print("Initialising MySQL schema...")
    db.init_workbook()

    conn = _raw_conn()
    try:
        cur = conn.cursor()

        # ── Products ──────────────────────────────────────────────────
        products = get_all_products()
        print(f"Migrating {len(products)} products...")
        for p in products:
            cur.execute("""
                INSERT INTO products
                    (product_id, name, purchase_price, counter_price, retail_price,
                     quantity, barcode, expiry_date, category, created_at)
                VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s,%s)
                ON DUPLICATE KEY UPDATE name=VALUES(name)
            """, (
                int(p["product_id"]), p["name"],
                float(p["purchase_price"] or 0), float(p["counter_price"] or 0),
                float(p["retail_price"] or 0), int(p["quantity"] or 0),
                p.get("barcode") or "", p.get("expiry_date"),
                p.get("category") or "", p.get("created_at") or datetime.now(),
            ))

        # Reset auto_increment to max+1
        cur.execute("SELECT MAX(product_id) AS m FROM products")
        max_id = cur.fetchone()["m"] or 0
        cur.execute(f"ALTER TABLE products AUTO_INCREMENT = {max_id + 1}")

        # ── Customers ─────────────────────────────────────────────────
        customers = get_all_customers()
        print(f"Migrating {len(customers)} customers...")
        for c in customers:
            cur.execute("""
                INSERT INTO customers
                    (customer_id, name, phone, email, address, tax_id, notes, created_at)
                VALUES (%s,%s,%s,%s,%s,%s,%s,%s)
                ON DUPLICATE KEY UPDATE name=VALUES(name)
            """, (
                int(c["customer_id"]), c["name"] or "",
                c.get("phone") or "", c.get("email") or "",
                c.get("address") or "", c.get("tax_id") or "",
                c.get("notes") or "", c.get("created_at") or datetime.now(),
            ))
        cur.execute("SELECT MAX(customer_id) AS m FROM customers")
        max_id = cur.fetchone()["m"] or 0
        cur.execute(f"ALTER TABLE customers AUTO_INCREMENT = {max_id + 1}")

        # ── Invoices + Items ──────────────────────────────────────────
        invoices = get_all_invoices()
        print(f"Migrating {len(invoices)} invoices...")
        for inv in invoices:
            iid = int(inv["invoice_id"])
            cid = inv.get("customer_id")
            cid = int(float(cid)) if cid not in (None, "") else None
            cur.execute("""
                INSERT INTO invoices
                    (invoice_id, created_at, subtotal, discount_total, tax_rate,
                     tax_amount, total, payment_method, customer_id)
                VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s)
                ON DUPLICATE KEY UPDATE total=VALUES(total)
            """, (
                iid, inv.get("created_at") or datetime.now(),
                float(inv.get("subtotal") or 0), float(inv.get("discount_total") or 0),
                float(inv.get("tax_rate") or 0), float(inv.get("tax_amount") or 0),
                float(inv.get("total") or 0), inv.get("payment_method") or "Cash", cid,
            ))

            items = get_invoice_items(iid)
            for item in items:
                cur.execute("""
                    INSERT INTO invoice_items
                        (item_id, invoice_id, product_id, product_name, purchase_price,
                         counter_price, quantity, discount_amount, line_total)
                    VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s)
                    ON DUPLICATE KEY UPDATE line_total=VALUES(line_total)
                """, (
                    int(item["item_id"]), iid,
                    int(item["product_id"]) if item.get("product_id") else None,
                    item.get("product_name") or "",
                    float(item.get("purchase_price") or 0),
                    float(item.get("counter_price") or 0),
                    int(item.get("quantity") or 0),
                    float(item.get("discount_amount") or 0),
                    float(item.get("line_total") or 0),
                ))

        cur.execute("SELECT MAX(invoice_id) AS m FROM invoices")
        max_id = cur.fetchone()["m"] or 0
        cur.execute(f"ALTER TABLE invoices AUTO_INCREMENT = {max_id + 1}")
        cur.execute("SELECT MAX(item_id) AS m FROM invoice_items")
        max_id = cur.fetchone()["m"] or 0
        cur.execute(f"ALTER TABLE invoice_items AUTO_INCREMENT = {max_id + 1}")

        # ── Credit Ledger ─────────────────────────────────────────────
        ledger = get_credit_ledger()
        print(f"Migrating {len(ledger)} ledger entries...")
        for e in ledger:
            eid = int(e["entry_id"])
            cid = e.get("customer_id")
            cid = int(float(cid)) if cid not in (None, "") else None
            inv_id = e.get("invoice_id")
            inv_id = int(float(inv_id)) if inv_id not in (None, "") else None
            cur.execute("""
                INSERT INTO credit_ledger
                    (entry_id, customer_id, invoice_id, type, amount, note, created_at)
                VALUES (%s,%s,%s,%s,%s,%s,%s)
                ON DUPLICATE KEY UPDATE amount=VALUES(amount)
            """, (
                eid, cid, inv_id, e.get("type") or "debit",
                float(e.get("amount") or 0), e.get("note") or "",
                e.get("created_at") or datetime.now(),
            ))
        cur.execute("SELECT MAX(entry_id) AS m FROM credit_ledger")
        max_id = cur.fetchone()["m"] or 0
        cur.execute(f"ALTER TABLE credit_ledger AUTO_INCREMENT = {max_id + 1}")

        conn.commit()
        print("\nMigration complete!")
        print(f"  Products : {len(products)}")
        print(f"  Customers: {len(customers)}")
        print(f"  Invoices : {len(invoices)}")
        print(f"  Ledger   : {len(ledger)}")

    except Exception as e:
        conn.rollback()
        print(f"\nERROR during migration: {e}")
        sys.exit(1)
    finally:
        conn.close()


if __name__ == "__main__":
    migrate()
