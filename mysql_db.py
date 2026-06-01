"""MySQL data access layer for the POS system — identical interface to excel_db.py."""

from contextlib import contextmanager
from datetime import datetime, date, timedelta
import re

import pymysql
import pymysql.cursors

from config import MYSQL_HOST, MYSQL_PORT, MYSQL_USER, MYSQL_PASSWORD, MYSQL_DATABASE


@contextmanager
def _conn():
    conn = pymysql.connect(
        host=MYSQL_HOST,
        port=MYSQL_PORT,
        user=MYSQL_USER,
        password=MYSQL_PASSWORD,
        database=MYSQL_DATABASE,
        charset="utf8mb4",
        cursorclass=pymysql.cursors.DictCursor,
        autocommit=False,
    )
    try:
        yield conn
        conn.commit()
    except Exception:
        conn.rollback()
        raise
    finally:
        conn.close()


# ── Schema init ───────────────────────────────────────────────────────

def init_workbook():
    """Create all tables if they don't exist."""
    with _conn() as conn:
        with conn.cursor() as cur:
            cur.execute("""
                CREATE TABLE IF NOT EXISTS products (
                    product_id    INT AUTO_INCREMENT PRIMARY KEY,
                    name          VARCHAR(255) NOT NULL,
                    purchase_price DECIMAL(12,2) DEFAULT 0,
                    counter_price  DECIMAL(12,2) DEFAULT 0,
                    retail_price   DECIMAL(12,2) DEFAULT 0,
                    quantity       INT DEFAULT 0,
                    barcode        VARCHAR(100),
                    expiry_date    DATE,
                    category         VARCHAR(100),
                    created_at       DATETIME DEFAULT CURRENT_TIMESTAMP,
                    last_supplier_id INT
                ) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4;
            """)
            cur.execute("""
                CREATE TABLE IF NOT EXISTS customers (
                    customer_id INT AUTO_INCREMENT PRIMARY KEY,
                    name        VARCHAR(255) NOT NULL,
                    phone       VARCHAR(50),
                    email       VARCHAR(100),
                    address     TEXT,
                    tax_id      VARCHAR(100),
                    notes       TEXT,
                    created_at  DATETIME DEFAULT CURRENT_TIMESTAMP
                ) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4;
            """)
            cur.execute("""
                CREATE TABLE IF NOT EXISTS invoices (
                    invoice_id     INT AUTO_INCREMENT PRIMARY KEY,
                    created_at     DATETIME DEFAULT CURRENT_TIMESTAMP,
                    subtotal       DECIMAL(12,2),
                    discount_total DECIMAL(12,2),
                    tax_rate       DECIMAL(6,4),
                    tax_amount     DECIMAL(12,2),
                    total          DECIMAL(12,2),
                    payment_method VARCHAR(50),
                    customer_id    INT,
                    status         VARCHAR(20) DEFAULT 'active',
                    deleted_at     DATETIME,
                    deleted_by     VARCHAR(100),
                    delete_reason  TEXT,
                    FOREIGN KEY (customer_id) REFERENCES customers(customer_id) ON DELETE SET NULL
                ) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4;
            """)
            for _col in [
                "ALTER TABLE invoices ADD COLUMN status VARCHAR(20) DEFAULT 'active'",
                "ALTER TABLE invoices ADD COLUMN deleted_at DATETIME",
                "ALTER TABLE invoices ADD COLUMN deleted_by VARCHAR(100)",
                "ALTER TABLE invoices ADD COLUMN delete_reason TEXT",
            ]:
                try:
                    cur.execute(_col)
                except Exception:
                    pass  # column already exists
            cur.execute("""
                CREATE TABLE IF NOT EXISTS invoice_items (
                    item_id         INT AUTO_INCREMENT PRIMARY KEY,
                    invoice_id      INT NOT NULL,
                    product_id      INT,
                    product_name    VARCHAR(255),
                    purchase_price  DECIMAL(12,2),
                    counter_price   DECIMAL(12,2),
                    quantity        INT,
                    discount_amount DECIMAL(12,2),
                    line_total      DECIMAL(12,2),
                    FOREIGN KEY (invoice_id) REFERENCES invoices(invoice_id) ON DELETE CASCADE
                ) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4;
            """)
            cur.execute("""
                CREATE TABLE IF NOT EXISTS users (
                    user_id       INT AUTO_INCREMENT PRIMARY KEY,
                    username      VARCHAR(100) NOT NULL UNIQUE,
                    password_hash VARCHAR(255) NOT NULL,
                    role          ENUM('admin','staff') DEFAULT 'staff',
                    is_active     TINYINT(1) DEFAULT 1,
                    created_at    DATETIME DEFAULT CURRENT_TIMESTAMP
                ) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4;
            """)
            cur.execute("""
                CREATE TABLE IF NOT EXISTS credit_ledger (
                    entry_id    INT AUTO_INCREMENT PRIMARY KEY,
                    customer_id INT,
                    invoice_id  INT,
                    type        ENUM('debit','credit') NOT NULL,
                    amount      DECIMAL(12,2),
                    note        TEXT,
                    created_at  DATETIME DEFAULT CURRENT_TIMESTAMP
                ) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4;
            """)
            cur.execute("""
                CREATE TABLE IF NOT EXISTS suppliers (
                    supplier_id INT AUTO_INCREMENT PRIMARY KEY,
                    name        VARCHAR(255) NOT NULL,
                    phone       VARCHAR(50),
                    email       VARCHAR(100),
                    address     TEXT,
                    notes       TEXT,
                    created_at  DATETIME DEFAULT CURRENT_TIMESTAMP
                ) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4;
            """)
            cur.execute("""
                CREATE TABLE IF NOT EXISTS purchase_invoices (
                    purchase_id  INT AUTO_INCREMENT PRIMARY KEY,
                    supplier_id  INT NOT NULL,
                    created_at   DATETIME DEFAULT CURRENT_TIMESTAMP,
                    total_amount DECIMAL(12,2),
                    notes        TEXT,
                    FOREIGN KEY (supplier_id) REFERENCES suppliers(supplier_id)
                ) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4;
            """)
            cur.execute("""
                CREATE TABLE IF NOT EXISTS purchase_invoice_items (
                    item_id      INT AUTO_INCREMENT PRIMARY KEY,
                    purchase_id  INT NOT NULL,
                    product_id   INT,
                    product_name VARCHAR(255),
                    quantity     INT,
                    unit_cost    DECIMAL(12,2),
                    line_total   DECIMAL(12,2),
                    FOREIGN KEY (purchase_id) REFERENCES purchase_invoices(purchase_id) ON DELETE CASCADE
                ) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4;
            """)
            cur.execute("""
                CREATE TABLE IF NOT EXISTS supplier_ledger (
                    entry_id    INT AUTO_INCREMENT PRIMARY KEY,
                    supplier_id INT NOT NULL,
                    purchase_id INT,
                    type        ENUM('debit','credit') NOT NULL,
                    amount      DECIMAL(12,2),
                    note        TEXT,
                    created_at  DATETIME DEFAULT CURRENT_TIMESTAMP
                ) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4;
            """)
            # Add last_supplier_id to products if not exists
            try:
                cur.execute("ALTER TABLE products ADD COLUMN last_supplier_id INT DEFAULT NULL")
            except Exception:
                pass  # column already exists


def normalize_customer_id(val):
    if val is None or val == "":
        return None
    try:
        return int(float(val))
    except (ValueError, TypeError):
        return None


def _normalize_date(val):
    if val is None or val == "":
        return None
    if isinstance(val, datetime):
        return val.date()
    if isinstance(val, date):
        return val
    try:
        return datetime.strptime(str(val), "%Y-%m-%d").date()
    except (ValueError, TypeError):
        return None


# ── Products ──────────────────────────────────────────────────────────

def get_all_products():
    with _conn() as conn:
        with conn.cursor() as cur:
            cur.execute("SELECT * FROM products ORDER BY product_id")
            rows = cur.fetchall()
    for p in rows:
        p["expiry_date"] = _normalize_date(p["expiry_date"])
    return rows


def get_product(product_id):
    with _conn() as conn:
        with conn.cursor() as cur:
            cur.execute("SELECT * FROM products WHERE product_id = %s", (int(product_id),))
            row = cur.fetchone()
    if row:
        row["expiry_date"] = _normalize_date(row["expiry_date"])
    return row


def get_product_by_barcode(barcode):
    with _conn() as conn:
        with conn.cursor() as cur:
            cur.execute("SELECT * FROM products WHERE barcode = %s", (str(barcode).strip(),))
            row = cur.fetchone()
    if row:
        row["expiry_date"] = _normalize_date(row["expiry_date"])
    return row


def add_product(data):
    expiry = data.get("expiry_date")
    if isinstance(expiry, str) and expiry:
        expiry = datetime.strptime(expiry, "%Y-%m-%d").date()
    elif not isinstance(expiry, (date, datetime)):
        expiry = None
    with _conn() as conn:
        with conn.cursor() as cur:
            cur.execute("""
                INSERT INTO products
                    (name, purchase_price, counter_price, retail_price, quantity, barcode, expiry_date, category, created_at)
                VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s)
            """, (
                data["name"],
                float(data.get("purchase_price", 0)),
                float(data.get("counter_price", 0)),
                float(data.get("retail_price", 0)),
                int(data["quantity"]),
                data.get("barcode", ""),
                expiry,
                data.get("category", ""),
                datetime.now(),
            ))
            return cur.lastrowid


def update_product(product_id, data):
    expiry = data.get("expiry_date")
    if isinstance(expiry, str) and expiry:
        expiry = datetime.strptime(expiry, "%Y-%m-%d").date()
    elif not isinstance(expiry, (date, datetime)):
        expiry = None
    with _conn() as conn:
        with conn.cursor() as cur:
            cur.execute("""
                UPDATE products SET
                    name = %s, purchase_price = %s, counter_price = %s,
                    retail_price = %s, quantity = %s, barcode = %s,
                    expiry_date = %s, category = %s
                WHERE product_id = %s
            """, (
                data["name"],
                float(data.get("purchase_price", 0)),
                float(data.get("counter_price", 0)),
                float(data.get("retail_price", 0)),
                int(data["quantity"]),
                data.get("barcode", ""),
                expiry,
                data.get("category", ""),
                int(product_id),
            ))


def delete_product(product_id):
    with _conn() as conn:
        with conn.cursor() as cur:
            cur.execute("DELETE FROM products WHERE product_id = %s", (int(product_id),))


def get_low_stock_products(threshold=10):
    with _conn() as conn:
        with conn.cursor() as cur:
            cur.execute("SELECT * FROM products WHERE quantity <= %s ORDER BY quantity", (threshold,))
            rows = cur.fetchall()
    for p in rows:
        p["expiry_date"] = _normalize_date(p["expiry_date"])
    return rows


def get_expiry_products(days_ahead=30):
    today = date.today()
    cutoff = today + timedelta(days=days_ahead)
    with _conn() as conn:
        with conn.cursor() as cur:
            cur.execute(
                "SELECT * FROM products WHERE expiry_date IS NOT NULL AND expiry_date <= %s ORDER BY expiry_date",
                (cutoff,)
            )
            rows = cur.fetchall()
    results = []
    for p in rows:
        exp = _normalize_date(p["expiry_date"])
        p["expiry_date"] = exp
        p["expired"] = exp < today
        p["days_left"] = (exp - today).days
        results.append(p)
    return results


def search_products(query):
    q = f"%{str(query).strip()}%"
    with _conn() as conn:
        with conn.cursor() as cur:
            cur.execute(
                "SELECT * FROM products WHERE name LIKE %s OR barcode LIKE %s LIMIT 20",
                (q, q)
            )
            rows = cur.fetchall()
    for p in rows:
        p["expiry_date"] = _normalize_date(p["expiry_date"])
    return rows


# ── Customers ─────────────────────────────────────────────────────────

def get_all_customers():
    with _conn() as conn:
        with conn.cursor() as cur:
            cur.execute("SELECT * FROM customers ORDER BY customer_id")
            return cur.fetchall()


def get_customer(customer_id):
    cid = normalize_customer_id(customer_id)
    if cid is None:
        return None
    with _conn() as conn:
        with conn.cursor() as cur:
            cur.execute("SELECT * FROM customers WHERE customer_id = %s", (cid,))
            return cur.fetchone()


def add_customer(data):
    with _conn() as conn:
        with conn.cursor() as cur:
            cur.execute("""
                INSERT INTO customers (name, phone, email, address, tax_id, notes, created_at)
                VALUES (%s, %s, %s, %s, %s, %s, %s)
            """, (
                (data.get("name") or "").strip(),
                (data.get("phone") or "").strip(),
                (data.get("email") or "").strip(),
                (data.get("address") or "").strip(),
                (data.get("tax_id") or "").strip(),
                (data.get("notes") or "").strip(),
                datetime.now(),
            ))
            return cur.lastrowid


def update_customer(customer_id, data):
    cid = normalize_customer_id(customer_id)
    if cid is None:
        return
    with _conn() as conn:
        with conn.cursor() as cur:
            cur.execute("""
                UPDATE customers SET
                    name = %s, phone = %s, email = %s,
                    address = %s, tax_id = %s, notes = %s
                WHERE customer_id = %s
            """, (
                (data.get("name") or "").strip(),
                (data.get("phone") or "").strip(),
                (data.get("email") or "").strip(),
                (data.get("address") or "").strip(),
                (data.get("tax_id") or "").strip(),
                (data.get("notes") or "").strip(),
                cid,
            ))


def delete_customer(customer_id):
    cid = normalize_customer_id(customer_id)
    if cid is None:
        return False
    with _conn() as conn:
        with conn.cursor() as cur:
            cur.execute("SELECT COUNT(*) AS cnt FROM invoices WHERE customer_id = %s", (cid,))
            if cur.fetchone()["cnt"] > 0:
                raise ValueError("Cannot delete customer: invoices reference this profile.")
            cur.execute("DELETE FROM customers WHERE customer_id = %s", (cid,))
    return True


def search_customers(query):
    q = f"%{str(query).strip()}%"
    with _conn() as conn:
        with conn.cursor() as cur:
            cur.execute(
                "SELECT * FROM customers WHERE name LIKE %s OR phone LIKE %s OR email LIKE %s LIMIT 20",
                (q, q, q)
            )
            return cur.fetchall()


def customer_lookup():
    return {c["customer_id"]: c for c in get_all_customers()}


def get_sales_summary_by_customer():
    cmap = customer_lookup()
    by_c = {cid: {"customer": c, "invoice_count": 0, "total_revenue": 0.0} for cid, c in cmap.items()}
    walk_in = {"customer": None, "invoice_count": 0, "total_revenue": 0.0}
    for inv in get_all_invoices():
        total = float(inv["total"] or 0)
        cid = normalize_customer_id(inv.get("customer_id"))
        if cid is None or cid not in by_c:
            walk_in["invoice_count"] += 1
            walk_in["total_revenue"] += total
        else:
            by_c[cid]["invoice_count"] += 1
            by_c[cid]["total_revenue"] += total
    rows = [v for v in by_c.values() if v["invoice_count"]]
    rows.sort(key=lambda x: x["total_revenue"], reverse=True)
    for r in rows:
        r["total_revenue"] = round(r["total_revenue"], 2)
    walk_in["total_revenue"] = round(walk_in["total_revenue"], 2)
    return rows, walk_in


def get_invoices_for_customer(customer_id):
    cid = normalize_customer_id(customer_id)
    if cid is None:
        return []
    with _conn() as conn:
        with conn.cursor() as cur:
            cur.execute(
                "SELECT * FROM invoices WHERE customer_id = %s ORDER BY invoice_id DESC",
                (cid,)
            )
            return cur.fetchall()


def get_customer_product_aggregates(customer_id):
    cid = normalize_customer_id(customer_id)
    if cid is None:
        return []
    with _conn() as conn:
        with conn.cursor() as cur:
            cur.execute("""
                SELECT ii.product_id, ii.product_name,
                       SUM(ii.quantity) AS total_qty,
                       ROUND(SUM(ii.line_total), 2) AS total_amount
                FROM invoice_items ii
                JOIN invoices i ON i.invoice_id = ii.invoice_id
                WHERE i.customer_id = %s
                GROUP BY ii.product_id, ii.product_name
                ORDER BY ii.product_name
            """, (cid,))
            return cur.fetchall()


# ── Invoices ──────────────────────────────────────────────────────────

def get_all_invoices(include_deleted=False):
    with _conn() as conn:
        with conn.cursor() as cur:
            if include_deleted:
                cur.execute("SELECT * FROM invoices ORDER BY invoice_id DESC")
            else:
                cur.execute("SELECT * FROM invoices WHERE status != 'deleted' OR status IS NULL ORDER BY invoice_id DESC")
            return cur.fetchall()


def get_deleted_invoices():
    with _conn() as conn:
        with conn.cursor() as cur:
            cur.execute("SELECT * FROM invoices WHERE status = 'deleted' ORDER BY invoice_id DESC")
            return cur.fetchall()


def get_invoice(invoice_id):
    with _conn() as conn:
        with conn.cursor() as cur:
            cur.execute("SELECT * FROM invoices WHERE invoice_id = %s", (int(invoice_id),))
            return cur.fetchone()


def delete_invoice(invoice_id, deleted_by, reason=""):
    """Soft-delete: mark deleted, reverse stock, reverse credit ledger."""
    iid = int(invoice_id)
    with _conn() as conn:
        with conn.cursor() as cur:
            cur.execute("SELECT * FROM invoices WHERE invoice_id = %s", (iid,))
            inv = cur.fetchone()
            if not inv:
                raise ValueError("Invoice not found")
            if inv.get("status") == "deleted":
                raise ValueError("Invoice already deleted")
            # Reverse stock
            cur.execute("SELECT * FROM invoice_items WHERE invoice_id = %s", (iid,))
            for item in cur.fetchall():
                cur.execute(
                    "UPDATE products SET quantity = quantity + %s WHERE product_id = %s",
                    (int(item["quantity"]), int(item["product_id"]))
                )
            # Reverse credit ledger
            cur.execute(
                "UPDATE credit_ledger SET entry_type = 'deleted' WHERE invoice_id = %s",
                (iid,)
            )
            # Soft-delete
            from datetime import datetime as _dt
            cur.execute(
                """UPDATE invoices SET status='deleted', deleted_at=%s, deleted_by=%s, delete_reason=%s
                   WHERE invoice_id = %s""",
                (_dt.now(), deleted_by, reason, iid)
            )


def get_invoice_items(invoice_id):
    with _conn() as conn:
        with conn.cursor() as cur:
            cur.execute(
                "SELECT * FROM invoice_items WHERE invoice_id = %s",
                (int(invoice_id),)
            )
            return cur.fetchall()


def create_invoice(items, tax_rate, payment_method, customer_id=None):
    cid = normalize_customer_id(customer_id)
    if payment_method == "Credit" and cid is None:
        raise ValueError("Credit payment requires a customer to be selected.")

    with _conn() as conn:
        with conn.cursor() as cur:
            if cid is not None:
                cur.execute("SELECT customer_id FROM customers WHERE customer_id = %s", (cid,))
                if not cur.fetchone():
                    raise ValueError("Customer not found.")

            subtotal = 0.0
            discount_total = 0.0
            line_entries = []

            for item in items:
                pid = int(item["product_id"])
                qty = int(item["quantity"])
                discount_per_unit = float(item.get("discount_amount", 0))

                cur.execute("SELECT * FROM products WHERE product_id = %s FOR UPDATE", (pid,))
                prod = cur.fetchone()
                if not prod:
                    raise ValueError(f"Product ID {pid} not found")
                available = int(prod["quantity"])
                if qty > available:
                    raise ValueError(
                        f"Not enough stock for '{prod['name']}': "
                        f"requested {qty}, available {available}"
                    )

                purchase_price = float(prod["purchase_price"])
                catalog_counter = float(prod["counter_price"])
                raw_unit = item.get("unit_price")
                unit_price = float(raw_unit) if raw_unit not in (None, "") else catalog_counter

                if unit_price < 0:
                    raise ValueError(f"Invalid sale price for '{prod['name']}'")

                discounted_price = unit_price - discount_per_unit
                if discounted_price < purchase_price:
                    raise ValueError(
                        f"Discount too high for '{prod['name']}': "
                        f"price after discount {discounted_price:.2f} below "
                        f"purchase price {purchase_price:.2f}. "
                        f"Max discount: {unit_price - purchase_price:.2f}"
                    )

                line_discount = discount_per_unit * qty
                line_total = discounted_price * qty
                subtotal += unit_price * qty
                discount_total += line_discount

                line_entries.append((pid, prod["name"], purchase_price, unit_price, qty, line_discount, round(line_total, 2)))
                cur.execute("UPDATE products SET quantity = quantity - %s WHERE product_id = %s", (qty, pid))

            net = round(subtotal - discount_total, 2)
            tax_amount = round(net * tax_rate, 2)
            total = round(net + tax_amount, 2)

            cur.execute("""
                INSERT INTO invoices (created_at, subtotal, discount_total, tax_rate, tax_amount, total, payment_method, customer_id)
                VALUES (%s, %s, %s, %s, %s, %s, %s, %s)
            """, (datetime.now(), round(subtotal, 2), round(discount_total, 2), tax_rate, tax_amount, total, payment_method, cid))
            invoice_id = cur.lastrowid

            for pid, pname, pp, up, qty, ld, lt in line_entries:
                cur.execute("""
                    INSERT INTO invoice_items (invoice_id, product_id, product_name, purchase_price, counter_price, quantity, discount_amount, line_total)
                    VALUES (%s, %s, %s, %s, %s, %s, %s, %s)
                """, (invoice_id, pid, pname, pp, up, qty, ld, lt))

            if payment_method == "Credit" and cid is not None:
                cur.execute("""
                    INSERT INTO credit_ledger (customer_id, invoice_id, type, amount, note, created_at)
                    VALUES (%s, %s, 'debit', %s, 'Credit sale', %s)
                """, (cid, invoice_id, total, datetime.now()))

    return invoice_id


def update_invoice(invoice_id, items, tax_rate, payment_method=None, customer_id=None):
    iid = int(invoice_id)
    with _conn() as conn:
        with conn.cursor() as cur:
            cur.execute("SELECT * FROM invoices WHERE invoice_id = %s FOR UPDATE", (iid,))
            invoice = cur.fetchone()
            if not invoice:
                raise ValueError("Invoice not found.")

            cur.execute("SELECT * FROM invoice_items WHERE invoice_id = %s", (iid,))
            old_items = cur.fetchall()

            # Return stock from old items
            for old in old_items:
                cur.execute(
                    "UPDATE products SET quantity = quantity + %s WHERE product_id = %s",
                    (int(old["quantity"]), int(old["product_id"]))
                )

            old_payment = invoice.get("payment_method")
            old_cid = normalize_customer_id(invoice.get("customer_id"))
            new_payment = payment_method if payment_method is not None else old_payment
            new_cid = normalize_customer_id(customer_id) if customer_id is not None else old_cid

            if new_payment == "Credit" and new_cid is None:
                raise ValueError("Credit payment requires a customer.")
            if new_cid is not None:
                cur.execute("SELECT customer_id FROM customers WHERE customer_id = %s", (new_cid,))
                if not cur.fetchone():
                    raise ValueError("Customer not found.")

            subtotal = 0.0
            discount_total = 0.0
            line_entries = []

            for item in items:
                pid = int(item["product_id"])
                qty = int(item["quantity"])
                discount_per_unit = float(item.get("discount_amount", 0))

                cur.execute("SELECT * FROM products WHERE product_id = %s FOR UPDATE", (pid,))
                prod = cur.fetchone()
                if not prod:
                    raise ValueError(f"Product ID {pid} not found")
                available = int(prod["quantity"])
                if qty > available:
                    raise ValueError(
                        f"Not enough stock for '{prod['name']}': "
                        f"requested {qty}, available {available}"
                    )

                purchase_price = float(prod["purchase_price"])
                catalog_counter = float(prod["counter_price"])
                raw_unit = item.get("unit_price")
                unit_price = float(raw_unit) if raw_unit not in (None, "") else catalog_counter

                if unit_price < 0:
                    raise ValueError(f"Invalid sale price for '{prod['name']}'")

                discounted_price = unit_price - discount_per_unit
                if discounted_price < purchase_price:
                    raise ValueError(
                        f"Discount too high for '{prod['name']}': "
                        f"price after discount {discounted_price:.2f} below purchase {purchase_price:.2f}"
                    )

                line_discount = discount_per_unit * qty
                line_total = discounted_price * qty
                subtotal += unit_price * qty
                discount_total += line_discount

                line_entries.append((pid, prod["name"], purchase_price, unit_price, qty, line_discount, round(line_total, 2)))
                cur.execute("UPDATE products SET quantity = quantity - %s WHERE product_id = %s", (qty, pid))

            net = round(subtotal - discount_total, 2)
            tax_amount = round(net * tax_rate, 2)
            total = round(net + tax_amount, 2)

            cur.execute("DELETE FROM invoice_items WHERE invoice_id = %s", (iid,))
            for pid, pname, pp, up, qty, ld, lt in line_entries:
                cur.execute("""
                    INSERT INTO invoice_items (invoice_id, product_id, product_name, purchase_price, counter_price, quantity, discount_amount, line_total)
                    VALUES (%s, %s, %s, %s, %s, %s, %s, %s)
                """, (iid, pid, pname, pp, up, qty, ld, lt))

            cur.execute("""
                UPDATE invoices SET
                    subtotal = %s, discount_total = %s, tax_rate = %s,
                    tax_amount = %s, total = %s, payment_method = %s, customer_id = %s
                WHERE invoice_id = %s
            """, (round(subtotal, 2), round(discount_total, 2), tax_rate, tax_amount, total, new_payment, new_cid, iid))

            # Reconcile ledger: delete old debit for this invoice, re-add if Credit
            cur.execute(
                "DELETE FROM credit_ledger WHERE invoice_id = %s AND type = 'debit'",
                (iid,)
            )
            if new_payment == "Credit" and new_cid is not None:
                cur.execute("""
                    INSERT INTO credit_ledger (customer_id, invoice_id, type, amount, note, created_at)
                    VALUES (%s, %s, 'debit', %s, 'Credit sale', %s)
                """, (new_cid, iid, total, datetime.now()))

    return iid


# ── Credit Ledger ─────────────────────────────────────────────────────

def get_credit_ledger(customer_id=None):
    cid = normalize_customer_id(customer_id) if customer_id is not None else None
    with _conn() as conn:
        with conn.cursor() as cur:
            if cid is not None:
                cur.execute(
                    "SELECT * FROM credit_ledger WHERE customer_id = %s ORDER BY entry_id DESC",
                    (cid,)
                )
            else:
                cur.execute("SELECT * FROM credit_ledger ORDER BY entry_id DESC")
            return cur.fetchall()


def add_ledger_payment(customer_id, amount, note=""):
    cid = normalize_customer_id(customer_id)
    if cid is None:
        raise ValueError("Invalid customer.")
    amount = float(amount)
    if amount <= 0:
        raise ValueError("Amount must be positive.")
    if not get_customer(cid):
        raise ValueError("Customer not found.")
    with _conn() as conn:
        with conn.cursor() as cur:
            cur.execute("""
                INSERT INTO credit_ledger (customer_id, invoice_id, type, amount, note, created_at)
                VALUES (%s, NULL, 'credit', %s, %s, %s)
            """, (cid, amount, (note or "").strip(), datetime.now()))
            return cur.lastrowid


def add_ledger_debit(customer_id, amount, note=""):
    cid = normalize_customer_id(customer_id)
    if cid is None:
        raise ValueError("Invalid customer.")
    amount = float(amount)
    if amount <= 0:
        raise ValueError("Amount must be positive.")
    if not get_customer(cid):
        raise ValueError("Customer not found.")
    with _conn() as conn:
        with conn.cursor() as cur:
            cur.execute("""
                INSERT INTO credit_ledger (customer_id, invoice_id, type, amount, note, created_at)
                VALUES (%s, NULL, 'debit', %s, %s, %s)
            """, (cid, amount, (note or "").strip(), datetime.now()))
            return cur.lastrowid


def get_customer_balance(customer_id):
    entries = get_credit_ledger(customer_id=customer_id)
    total_debt = sum(float(e["amount"] or 0) for e in entries if e["type"] == "debit")
    total_paid = sum(float(e["amount"] or 0) for e in entries if e["type"] == "credit")
    return round(total_debt, 2), round(total_paid, 2), round(total_debt - total_paid, 2)


def get_all_credit_balances():
    cmap = customer_lookup()
    with _conn() as conn:
        with conn.cursor() as cur:
            cur.execute("""
                SELECT customer_id,
                       SUM(CASE WHEN type='debit'  THEN amount ELSE 0 END) AS total_debt,
                       SUM(CASE WHEN type='credit' THEN amount ELSE 0 END) AS total_paid
                FROM credit_ledger
                GROUP BY customer_id
            """)
            rows = cur.fetchall()
    result = []
    for row in rows:
        cid = normalize_customer_id(row["customer_id"])
        if cid is None:
            continue
        balance = round(float(row["total_debt"]) - float(row["total_paid"]), 2)
        result.append({
            "customer": cmap.get(cid),
            "customer_id": cid,
            "total_debt": round(float(row["total_debt"]), 2),
            "total_paid": round(float(row["total_paid"]), 2),
            "balance": balance,
        })
    result.sort(key=lambda x: x["balance"], reverse=True)
    return result


# ── Analytics ─────────────────────────────────────────────────────────

def get_today_sales():
    with _conn() as conn:
        with conn.cursor() as cur:
            cur.execute("""
                SELECT COUNT(*) AS cnt, COALESCE(SUM(total), 0) AS total
                FROM invoices
                WHERE DATE(created_at) = CURDATE()
            """)
            row = cur.fetchone()
    return int(row["cnt"]), round(float(row["total"]), 2)


# ── Import from Excel ─────────────────────────────────────────────────

def _parse_expiry(val):
    if val is None or val == "" or val == "None":
        return None
    if isinstance(val, datetime):
        return val.date()
    if isinstance(val, date):
        return val
    s = str(val).strip()
    m = re.match(r'^(\d{1,2})\s+(\d{4})$', s)
    if m:
        month, year = int(m.group(1)), int(m.group(2))
        if 1 <= month <= 12:
            if month == 12:
                return date(year, 12, 31)
            return date(year, month + 1, 1) - timedelta(days=1)
    for fmt in ("%Y-%m-%d", "%d/%m/%Y", "%m/%d/%Y", "%d-%m-%Y"):
        try:
            return datetime.strptime(s, fmt).date()
        except ValueError:
            continue
    return None


def import_from_excel(filepath):
    from openpyxl import load_workbook as lw
    wb_src = lw(filepath)
    ws = wb_src.active
    imported = 0
    skipped = 0
    errors = []
    has_counter_col = ws.max_column >= 9

    for i, row in enumerate(ws.iter_rows(min_row=2, values_only=True), start=2):
        if row[0] is None and row[1] is None:
            continue
        name = str(row[1] or "").strip()
        if not name:
            skipped += 1
            continue
        packing = str(row[2] or "").strip()
        if packing:
            name = f"{name} ({packing})"
        try:
            qty = int(row[3] or 0)
        except (ValueError, TypeError):
            qty = 0
        try:
            purchase_price = float(row[4] or 0)
        except (ValueError, TypeError):
            purchase_price = 0.0
        company = str(row[5] or "").strip()
        expiry = _parse_expiry(row[6])
        try:
            mrp = float(row[7] or 0)
        except (ValueError, TypeError):
            mrp = 0.0
        if has_counter_col and len(row) >= 9 and row[8] is not None:
            try:
                counter_price = float(row[8])
            except (ValueError, TypeError):
                counter_price = mrp if mrp > 0 else purchase_price
        else:
            counter_price = mrp if mrp > 0 else purchase_price
        try:
            add_product({
                "name": name,
                "purchase_price": purchase_price,
                "counter_price": counter_price,
                "retail_price": mrp,
                "quantity": qty,
                "barcode": "",
                "expiry_date": expiry,
                "category": company,
            })
            imported += 1
        except Exception as e:
            errors.append(f"Row {i}: {e}")
            skipped += 1

    wb_src.close()
    return imported, skipped, errors


# ── Users ─────────────────────────────────────────────────────────────

def get_all_users():
    with _conn() as conn:
        with conn.cursor() as cur:
            cur.execute("SELECT * FROM users ORDER BY user_id")
            rows = cur.fetchall()
    for u in rows:
        u["is_active"] = bool(u["is_active"])
    return rows


def get_user_by_id(user_id):
    with _conn() as conn:
        with conn.cursor() as cur:
            cur.execute("SELECT * FROM users WHERE user_id = %s", (int(user_id),))
            row = cur.fetchone()
    if row:
        row["is_active"] = bool(row["is_active"])
    return row


def get_user_by_username(username):
    with _conn() as conn:
        with conn.cursor() as cur:
            cur.execute("SELECT * FROM users WHERE username = %s", (username.strip().lower(),))
            row = cur.fetchone()
    if row:
        row["is_active"] = bool(row["is_active"])
    return row


def has_any_users():
    with _conn() as conn:
        with conn.cursor() as cur:
            cur.execute("SELECT COUNT(*) AS cnt FROM users")
            return cur.fetchone()["cnt"] > 0


def add_user(username, password_hash, role="staff"):
    with _conn() as conn:
        with conn.cursor() as cur:
            cur.execute("""
                INSERT INTO users (username, password_hash, role, is_active, created_at)
                VALUES (%s, %s, %s, 1, %s)
            """, (username.strip().lower(), password_hash, role, datetime.now()))
            return cur.lastrowid


def update_user_password(user_id, password_hash):
    with _conn() as conn:
        with conn.cursor() as cur:
            cur.execute(
                "UPDATE users SET password_hash = %s WHERE user_id = %s",
                (password_hash, int(user_id))
            )


def set_user_role(user_id, role):
    with _conn() as conn:
        with conn.cursor() as cur:
            cur.execute(
                "UPDATE users SET role = %s WHERE user_id = %s",
                (role, int(user_id))
            )


def toggle_user_active(user_id):
    with _conn() as conn:
        with conn.cursor() as cur:
            cur.execute(
                "UPDATE users SET is_active = NOT is_active WHERE user_id = %s",
                (int(user_id),)
            )


def delete_user(user_id):
    with _conn() as conn:
        with conn.cursor() as cur:
            cur.execute("DELETE FROM users WHERE user_id = %s", (int(user_id),))


# ── Suppliers ─────────────────────────────────────────────────────────

def get_all_suppliers():
    with _conn() as conn:
        with conn.cursor() as cur:
            cur.execute("SELECT * FROM suppliers ORDER BY supplier_id")
            return cur.fetchall()


def get_supplier(supplier_id):
    with _conn() as conn:
        with conn.cursor() as cur:
            cur.execute("SELECT * FROM suppliers WHERE supplier_id = %s", (int(supplier_id),))
            return cur.fetchone()


def add_supplier(data):
    with _conn() as conn:
        with conn.cursor() as cur:
            cur.execute("""
                INSERT INTO suppliers (name, phone, email, address, notes, created_at)
                VALUES (%s,%s,%s,%s,%s,%s)
            """, (
                (data.get("name") or "").strip(),
                (data.get("phone") or "").strip(),
                (data.get("email") or "").strip(),
                (data.get("address") or "").strip(),
                (data.get("notes") or "").strip(),
                datetime.now(),
            ))
            return cur.lastrowid


def update_supplier(supplier_id, data):
    with _conn() as conn:
        with conn.cursor() as cur:
            cur.execute("""
                UPDATE suppliers SET name=%s, phone=%s, email=%s, address=%s, notes=%s
                WHERE supplier_id=%s
            """, (
                (data.get("name") or "").strip(),
                (data.get("phone") or "").strip(),
                (data.get("email") or "").strip(),
                (data.get("address") or "").strip(),
                (data.get("notes") or "").strip(),
                int(supplier_id),
            ))


def delete_supplier(supplier_id):
    sid = int(supplier_id)
    with _conn() as conn:
        with conn.cursor() as cur:
            cur.execute("SELECT COUNT(*) AS cnt FROM purchase_invoices WHERE supplier_id=%s", (sid,))
            if cur.fetchone()["cnt"] > 0:
                raise ValueError("Cannot delete supplier: purchase invoices reference this supplier.")
            cur.execute("DELETE FROM suppliers WHERE supplier_id=%s", (sid,))
    return True


def supplier_lookup():
    return {s["supplier_id"]: s for s in get_all_suppliers()}


# ── Purchase Invoices ─────────────────────────────────────────────────

def create_purchase_invoice(supplier_id, items, notes=""):
    sid = int(supplier_id)
    with _conn() as conn:
        with conn.cursor() as cur:
            cur.execute("SELECT supplier_id FROM suppliers WHERE supplier_id=%s", (sid,))
            if not cur.fetchone():
                raise ValueError("Supplier not found.")

            total_amount = 0.0
            line_entries = []

            for item in items:
                pid = int(item["product_id"])
                qty = int(item["quantity"])
                unit_cost = float(item["unit_cost"])
                cur.execute("SELECT * FROM products WHERE product_id=%s FOR UPDATE", (pid,))
                prod = cur.fetchone()
                if not prod:
                    raise ValueError(f"Product ID {pid} not found")
                line_total = round(unit_cost * qty, 2)
                total_amount += line_total
                line_entries.append((pid, prod["name"], qty, unit_cost, line_total))
                cur.execute(
                    "UPDATE products SET quantity=quantity+%s, purchase_price=%s, last_supplier_id=%s WHERE product_id=%s",
                    (qty, unit_cost, sid, pid)
                )

            total_amount = round(total_amount, 2)
            cur.execute("""
                INSERT INTO purchase_invoices (supplier_id, created_at, total_amount, notes)
                VALUES (%s,%s,%s,%s)
            """, (sid, datetime.now(), total_amount, (notes or "").strip()))
            purchase_id = cur.lastrowid

            for pid, pname, qty, uc, lt in line_entries:
                cur.execute("""
                    INSERT INTO purchase_invoice_items
                        (purchase_id, product_id, product_name, quantity, unit_cost, line_total)
                    VALUES (%s,%s,%s,%s,%s,%s)
                """, (purchase_id, pid, pname, qty, uc, lt))

            cur.execute("""
                INSERT INTO supplier_ledger (supplier_id, purchase_id, type, amount, note, created_at)
                VALUES (%s,%s,'debit',%s,%s,%s)
            """, (sid, purchase_id, total_amount, f"Purchase invoice #{purchase_id}", datetime.now()))

    return purchase_id


def get_all_purchase_invoices():
    with _conn() as conn:
        with conn.cursor() as cur:
            cur.execute("SELECT * FROM purchase_invoices ORDER BY purchase_id DESC")
            return cur.fetchall()


def get_purchase_invoice(purchase_id):
    with _conn() as conn:
        with conn.cursor() as cur:
            cur.execute("SELECT * FROM purchase_invoices WHERE purchase_id=%s", (int(purchase_id),))
            return cur.fetchone()


def get_purchase_invoice_items(purchase_id):
    with _conn() as conn:
        with conn.cursor() as cur:
            cur.execute(
                "SELECT * FROM purchase_invoice_items WHERE purchase_id=%s",
                (int(purchase_id),)
            )
            return cur.fetchall()


# ── Supplier Ledger ───────────────────────────────────────────────────

def add_supplier_payment(supplier_id, amount, note=""):
    sid = int(supplier_id)
    amount = float(amount)
    if amount <= 0:
        raise ValueError("Amount must be positive.")
    if not get_supplier(sid):
        raise ValueError("Supplier not found.")
    with _conn() as conn:
        with conn.cursor() as cur:
            cur.execute("""
                INSERT INTO supplier_ledger (supplier_id, purchase_id, type, amount, note, created_at)
                VALUES (%s, NULL, 'credit', %s, %s, %s)
            """, (sid, amount, (note or "").strip(), datetime.now()))
            return cur.lastrowid


def get_supplier_ledger_entries(supplier_id=None):
    with _conn() as conn:
        with conn.cursor() as cur:
            if supplier_id is not None:
                cur.execute(
                    "SELECT * FROM supplier_ledger WHERE supplier_id=%s ORDER BY entry_id",
                    (int(supplier_id),)
                )
            else:
                cur.execute("SELECT * FROM supplier_ledger ORDER BY entry_id")
            return cur.fetchall()


def get_supplier_balance(supplier_id):
    entries = get_supplier_ledger_entries(supplier_id)
    total_debt = sum(float(e["amount"] or 0) for e in entries if e["type"] == "debit")
    total_paid = sum(float(e["amount"] or 0) for e in entries if e["type"] == "credit")
    return round(total_debt, 2), round(total_paid, 2), round(total_debt - total_paid, 2)


def get_all_supplier_balances():
    smap = supplier_lookup()
    with _conn() as conn:
        with conn.cursor() as cur:
            cur.execute("""
                SELECT supplier_id,
                       SUM(CASE WHEN type='debit'  THEN amount ELSE 0 END) AS total_debt,
                       SUM(CASE WHEN type='credit' THEN amount ELSE 0 END) AS total_paid
                FROM supplier_ledger GROUP BY supplier_id
            """)
            rows = cur.fetchall()
    result = []
    for row in rows:
        sid = int(row["supplier_id"])
        balance = round(float(row["total_debt"]) - float(row["total_paid"]), 2)
        result.append({
            "supplier": smap.get(sid),
            "supplier_id": sid,
            "total_debt": round(float(row["total_debt"]), 2),
            "total_paid": round(float(row["total_paid"]), 2),
            "balance": balance,
        })
    result.sort(key=lambda x: x["balance"], reverse=True)
    return result


# ── P&L Reports ───────────────────────────────────────────────────────

def get_sales_pl_report(start_date, end_date):
    with _conn() as conn:
        with conn.cursor() as cur:
            cur.execute("""
                SELECT
                    DATE(i.created_at) AS date,
                    i.invoice_id,
                    ii.product_name,
                    ii.quantity,
                    ii.purchase_price,
                    ROUND(ii.line_total / ii.quantity, 2) AS sale_price,
                    ii.line_total,
                    ROUND(ii.purchase_price * ii.quantity, 2) AS cogs,
                    ROUND(ii.line_total - ii.purchase_price * ii.quantity, 2) AS profit
                FROM invoice_items ii
                JOIN invoices i ON i.invoice_id = ii.invoice_id
                WHERE DATE(i.created_at) BETWEEN %s AND %s
                ORDER BY i.created_at, i.invoice_id
            """, (start_date, end_date))
            rows = cur.fetchall()
    total_revenue = round(sum(float(r["line_total"]) for r in rows), 2)
    total_cogs = round(sum(float(r["cogs"]) for r in rows), 2)
    total_profit = round(total_revenue - total_cogs, 2)
    return rows, {"revenue": total_revenue, "cogs": total_cogs, "profit": total_profit}


def get_supplier_sales_pl(supplier_id, start_date, end_date):
    sid = int(supplier_id)
    with _conn() as conn:
        with conn.cursor() as cur:
            cur.execute("""
                SELECT
                    ii.product_id,
                    ii.product_name,
                    SUM(ii.quantity) AS total_qty,
                    ROUND(SUM(ii.purchase_price * ii.quantity), 2) AS total_cogs,
                    ROUND(SUM(ii.line_total), 2) AS total_revenue,
                    ROUND(SUM(ii.line_total - ii.purchase_price * ii.quantity), 2) AS total_profit
                FROM invoice_items ii
                JOIN invoices i ON i.invoice_id = ii.invoice_id
                JOIN products p ON p.product_id = ii.product_id
                WHERE p.last_supplier_id = %s
                  AND DATE(i.created_at) BETWEEN %s AND %s
                GROUP BY ii.product_id, ii.product_name
                ORDER BY ii.product_name
            """, (sid, start_date, end_date))
            rows = cur.fetchall()
    total_revenue = round(sum(float(r["total_revenue"]) for r in rows), 2)
    total_cogs = round(sum(float(r["total_cogs"]) for r in rows), 2)
    total_profit = round(total_revenue - total_cogs, 2)
    return rows, {"revenue": total_revenue, "cogs": total_cogs, "profit": total_profit}
