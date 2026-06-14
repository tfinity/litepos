"""MySQL data access layer for the POS system — identical interface to excel_db.py."""

from contextlib import contextmanager
from datetime import datetime, date, timedelta
import re

import pymysql
import pymysql.cursors

from config import MYSQL_HOST, MYSQL_PORT, MYSQL_USER, MYSQL_PASSWORD, MYSQL_DATABASE
from tenant import get_current_tenant, require_tenant


def _tid():
    """Active tenant id for the current request; raises if none is set."""
    return require_tenant()

# Standard chart of accounts (code, name, type, is_system)
CHART_OF_ACCOUNTS = [
    ("1000", "Cash",                "asset",     True),
    ("1010", "Bank",                "asset",     True),
    ("1100", "Accounts Receivable", "asset",     True),
    ("1200", "Inventory",           "asset",     True),
    ("2000", "Accounts Payable",    "liability", True),
    ("2100", "Tax Payable",         "liability", True),
    ("3000", "Owner Capital",       "equity",    True),
    ("3100", "Owner Drawings",      "equity",    True),
    ("3900", "Retained Earnings",   "equity",    True),
    ("4000", "Sales Revenue",       "income",    True),
    ("5000", "Cost of Goods Sold",  "expense",   True),
    ("5100", "Rent",                "expense",   False),
    ("5200", "Salaries",            "expense",   False),
    ("5300", "Utilities",           "expense",   False),
    ("5400", "Transport",           "expense",   False),
    ("5900", "Miscellaneous",       "expense",   False),
]
_DEBIT_NORMAL_TYPES = ("asset", "expense")


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
                CREATE TABLE IF NOT EXISTS tenants (
                    tenant_id  INT AUTO_INCREMENT PRIMARY KEY,
                    name       VARCHAR(255) NOT NULL,
                    is_active  TINYINT(1) DEFAULT 1,
                    created_at DATETIME DEFAULT CURRENT_TIMESTAMP
                ) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4;
            """)
            cur.execute("""
                CREATE TABLE IF NOT EXISTS users (
                    user_id       INT AUTO_INCREMENT PRIMARY KEY,
                    tenant_id     INT,
                    username      VARCHAR(100) NOT NULL UNIQUE,
                    password_hash VARCHAR(255) NOT NULL,
                    role          ENUM('super_admin','admin','staff') DEFAULT 'staff',
                    is_active     TINYINT(1) DEFAULT 1,
                    created_at    DATETIME DEFAULT CURRENT_TIMESTAMP
                ) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4;
            """)
            # Upgrade existing users table for multi-tenant
            try:
                cur.execute("ALTER TABLE users ADD COLUMN tenant_id INT")
            except Exception:
                pass
            try:
                cur.execute("ALTER TABLE users MODIFY role ENUM('super_admin','admin','staff') DEFAULT 'staff'")
            except Exception:
                pass
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
                    purchase_id    INT AUTO_INCREMENT PRIMARY KEY,
                    supplier_id    INT NOT NULL,
                    created_at     DATETIME DEFAULT CURRENT_TIMESTAMP,
                    total_amount   DECIMAL(12,2),
                    notes          TEXT,
                    payment_method VARCHAR(20) DEFAULT 'credit',
                    FOREIGN KEY (supplier_id) REFERENCES suppliers(supplier_id)
                ) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4;
            """)
            try:
                cur.execute("ALTER TABLE purchase_invoices ADD COLUMN payment_method VARCHAR(20) DEFAULT 'credit'")
            except Exception:
                pass  # column already exists
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

            # ── Double-entry accounting ──
            cur.execute("""
                CREATE TABLE IF NOT EXISTS accounts (
                    account_id INT AUTO_INCREMENT PRIMARY KEY,
                    code       VARCHAR(20) NOT NULL UNIQUE,
                    name       VARCHAR(255) NOT NULL,
                    type       ENUM('asset','liability','equity','income','expense') NOT NULL,
                    is_system  TINYINT(1) DEFAULT 0,
                    created_at DATETIME DEFAULT CURRENT_TIMESTAMP
                ) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4;
            """)
            cur.execute("""
                CREATE TABLE IF NOT EXISTS journal_entries (
                    entry_id    INT AUTO_INCREMENT PRIMARY KEY,
                    date        DATETIME NOT NULL,
                    description VARCHAR(500),
                    source_type VARCHAR(50),
                    source_id   INT,
                    created_by  VARCHAR(100),
                    created_at  DATETIME DEFAULT CURRENT_TIMESTAMP
                ) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4;
            """)
            cur.execute("""
                CREATE TABLE IF NOT EXISTS journal_lines (
                    line_id    INT AUTO_INCREMENT PRIMARY KEY,
                    entry_id   INT NOT NULL,
                    account_id INT NOT NULL,
                    debit      DECIMAL(14,2) DEFAULT 0,
                    credit     DECIMAL(14,2) DEFAULT 0,
                    FOREIGN KEY (entry_id) REFERENCES journal_entries(entry_id) ON DELETE CASCADE,
                    FOREIGN KEY (account_id) REFERENCES accounts(account_id)
                ) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4;
            """)
            cur.execute("""
                CREATE TABLE IF NOT EXISTS partners (
                    partner_id INT AUTO_INCREMENT PRIMARY KEY,
                    name       VARCHAR(255) NOT NULL,
                    share_pct  DECIMAL(6,2) DEFAULT 0,
                    is_active  TINYINT(1) DEFAULT 1,
                    created_at DATETIME DEFAULT CURRENT_TIMESTAMP
                ) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4;
            """)
            cur.execute("""
                CREATE TABLE IF NOT EXISTS partner_transactions (
                    txn_id     INT AUTO_INCREMENT PRIMARY KEY,
                    partner_id INT NOT NULL,
                    type       ENUM('capital','drawing') NOT NULL,
                    amount     DECIMAL(14,2),
                    note       TEXT,
                    date       DATETIME,
                    created_at DATETIME DEFAULT CURRENT_TIMESTAMP,
                    FOREIGN KEY (partner_id) REFERENCES partners(partner_id)
                ) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4;
            """)

            # Multi-tenant: every data table carries the owning tenant_id.
            for _tbl in ("products", "customers", "invoices", "invoice_items",
                         "credit_ledger", "suppliers", "purchase_invoices",
                         "purchase_invoice_items", "supplier_ledger", "accounts",
                         "journal_entries", "journal_lines", "partners",
                         "partner_transactions"):
                try:
                    cur.execute(f"ALTER TABLE {_tbl} ADD COLUMN tenant_id INT")
                except Exception:
                    pass  # column already exists
                try:
                    cur.execute(f"CREATE INDEX idx_{_tbl}_tenant ON {_tbl} (tenant_id)")
                except Exception:
                    pass  # index already exists
            # Chart-of-accounts code must be unique per tenant, not globally.
            try:
                cur.execute("ALTER TABLE accounts DROP INDEX code")
            except Exception:
                pass
            try:
                cur.execute("ALTER TABLE accounts ADD UNIQUE KEY uniq_tenant_code (tenant_id, code)")
            except Exception:
                pass


def normalize_customer_id(val):
    if val is None or val == "":
        return None
    try:
        return int(float(val))
    except (ValueError, TypeError):
        return None


def _to_float(val, default=0.0):
    if val is None or val == "":
        return default
    try:
        return float(val)
    except (ValueError, TypeError):
        return default


def _to_int(val, default=0):
    if val is None or val == "":
        return default
    try:
        return int(float(val))
    except (ValueError, TypeError):
        return default


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
            cur.execute("SELECT * FROM products WHERE tenant_id = %s ORDER BY product_id", (_tid(),))
            rows = cur.fetchall()
    for p in rows:
        p["expiry_date"] = _normalize_date(p["expiry_date"])
    return rows


def get_product(product_id):
    with _conn() as conn:
        with conn.cursor() as cur:
            cur.execute("SELECT * FROM products WHERE product_id = %s AND tenant_id = %s",
                        (int(product_id), _tid()))
            row = cur.fetchone()
    if row:
        row["expiry_date"] = _normalize_date(row["expiry_date"])
    return row


def get_product_by_barcode(barcode):
    with _conn() as conn:
        with conn.cursor() as cur:
            cur.execute("SELECT * FROM products WHERE barcode = %s AND tenant_id = %s",
                        (str(barcode).strip(), _tid()))
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
                    (tenant_id, name, purchase_price, counter_price, retail_price, quantity, barcode, expiry_date, category, created_at)
                VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s)
            """, (
                _tid(),
                data["name"],
                _to_float(data.get("purchase_price")),
                _to_float(data.get("counter_price")),
                _to_float(data.get("retail_price")),
                _to_int(data.get("quantity")),
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
                WHERE product_id = %s AND tenant_id = %s
            """, (
                data["name"],
                _to_float(data.get("purchase_price")),
                _to_float(data.get("counter_price")),
                _to_float(data.get("retail_price")),
                _to_int(data.get("quantity")),
                data.get("barcode", ""),
                expiry,
                data.get("category", ""),
                int(product_id),
                _tid(),
            ))


def delete_product(product_id):
    with _conn() as conn:
        with conn.cursor() as cur:
            cur.execute("DELETE FROM products WHERE product_id = %s AND tenant_id = %s",
                        (int(product_id), _tid()))


def get_low_stock_products(threshold=10):
    with _conn() as conn:
        with conn.cursor() as cur:
            cur.execute("SELECT * FROM products WHERE tenant_id = %s AND quantity <= %s ORDER BY quantity",
                        (_tid(), threshold))
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
                "SELECT * FROM products WHERE tenant_id = %s AND expiry_date IS NOT NULL "
                "AND expiry_date <= %s ORDER BY expiry_date",
                (_tid(), cutoff)
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
                "SELECT * FROM products WHERE tenant_id = %s AND (name LIKE %s OR barcode LIKE %s) LIMIT 20",
                (_tid(), q, q)
            )
            rows = cur.fetchall()
    for p in rows:
        p["expiry_date"] = _normalize_date(p["expiry_date"])
    return rows


# ── Customers ─────────────────────────────────────────────────────────

def get_all_customers():
    with _conn() as conn:
        with conn.cursor() as cur:
            cur.execute("SELECT * FROM customers WHERE tenant_id = %s ORDER BY customer_id", (_tid(),))
            return cur.fetchall()


def get_customer(customer_id):
    cid = normalize_customer_id(customer_id)
    if cid is None:
        return None
    with _conn() as conn:
        with conn.cursor() as cur:
            cur.execute("SELECT * FROM customers WHERE customer_id = %s AND tenant_id = %s",
                        (cid, _tid()))
            return cur.fetchone()


def add_customer(data):
    with _conn() as conn:
        with conn.cursor() as cur:
            cur.execute("""
                INSERT INTO customers (tenant_id, name, phone, email, address, tax_id, notes, created_at)
                VALUES (%s, %s, %s, %s, %s, %s, %s, %s)
            """, (
                _tid(),
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
                WHERE customer_id = %s AND tenant_id = %s
            """, (
                (data.get("name") or "").strip(),
                (data.get("phone") or "").strip(),
                (data.get("email") or "").strip(),
                (data.get("address") or "").strip(),
                (data.get("tax_id") or "").strip(),
                (data.get("notes") or "").strip(),
                cid,
                _tid(),
            ))


def delete_customer(customer_id):
    cid = normalize_customer_id(customer_id)
    if cid is None:
        return False
    with _conn() as conn:
        with conn.cursor() as cur:
            cur.execute("SELECT COUNT(*) AS cnt FROM invoices WHERE customer_id = %s AND tenant_id = %s",
                        (cid, _tid()))
            if cur.fetchone()["cnt"] > 0:
                raise ValueError("Cannot delete customer: invoices reference this profile.")
            cur.execute("DELETE FROM customers WHERE customer_id = %s AND tenant_id = %s",
                        (cid, _tid()))
    return True


def search_customers(query):
    q = f"%{str(query).strip()}%"
    with _conn() as conn:
        with conn.cursor() as cur:
            cur.execute(
                "SELECT * FROM customers WHERE tenant_id = %s AND "
                "(name LIKE %s OR phone LIKE %s OR email LIKE %s) LIMIT 20",
                (_tid(), q, q, q)
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
                "SELECT * FROM invoices WHERE customer_id = %s AND tenant_id = %s ORDER BY invoice_id DESC",
                (cid, _tid())
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
                WHERE i.customer_id = %s AND i.tenant_id = %s
                GROUP BY ii.product_id, ii.product_name
                ORDER BY ii.product_name
            """, (cid, _tid()))
            return cur.fetchall()


# ── Invoices ──────────────────────────────────────────────────────────

def get_all_invoices(include_deleted=False):
    with _conn() as conn:
        with conn.cursor() as cur:
            if include_deleted:
                cur.execute("SELECT * FROM invoices WHERE tenant_id = %s ORDER BY invoice_id DESC",
                            (_tid(),))
            else:
                cur.execute("SELECT * FROM invoices WHERE tenant_id = %s AND "
                            "(status != 'deleted' OR status IS NULL) ORDER BY invoice_id DESC",
                            (_tid(),))
            return cur.fetchall()


def get_deleted_invoices():
    with _conn() as conn:
        with conn.cursor() as cur:
            cur.execute("SELECT * FROM invoices WHERE tenant_id = %s AND status = 'deleted' "
                        "ORDER BY invoice_id DESC", (_tid(),))
            return cur.fetchall()


def get_invoice(invoice_id):
    with _conn() as conn:
        with conn.cursor() as cur:
            cur.execute("SELECT * FROM invoices WHERE invoice_id = %s AND tenant_id = %s",
                        (int(invoice_id), _tid()))
            return cur.fetchone()


def delete_invoice(invoice_id, deleted_by, reason=""):
    """Soft-delete: mark deleted, reverse stock, reverse credit ledger."""
    iid = int(invoice_id)
    tid = _tid()
    with _conn() as conn:
        with conn.cursor() as cur:
            cur.execute("SELECT * FROM invoices WHERE invoice_id = %s AND tenant_id = %s", (iid, tid))
            inv = cur.fetchone()
            if not inv:
                raise ValueError("Invoice not found")
            if inv.get("status") == "deleted":
                raise ValueError("Invoice already deleted")

            # Guard: deleting a credit sale the customer has already paid against
            # would leave them overpaid (negative balance). Block it.
            cid = normalize_customer_id(inv.get("customer_id"))
            is_credit = str(inv.get("payment_method") or "").strip().lower() == "credit"
            if is_credit and cid is not None:
                cur.execute("""
                    SELECT
                        COALESCE(SUM(CASE WHEN type='debit'  THEN amount ELSE 0 END),0) AS debit,
                        COALESCE(SUM(CASE WHEN type='credit' THEN amount ELSE 0 END),0) AS paid
                    FROM credit_ledger WHERE customer_id = %s AND tenant_id = %s
                """, (cid, tid))
                bal = cur.fetchone()
                inv_amount = float(inv.get("total") or 0)
                if round((float(bal["debit"]) - inv_amount) - float(bal["paid"]), 2) < 0:
                    raise ValueError(
                        "This credit sale has payments against the customer's account. "
                        "Deleting it would leave the customer overpaid. Record a refund or "
                        "adjust their balance first, then delete.")

            # Reverse stock
            cur.execute("SELECT * FROM invoice_items WHERE invoice_id = %s AND tenant_id = %s", (iid, tid))
            for item in cur.fetchall():
                cur.execute(
                    "UPDATE products SET quantity = quantity + %s WHERE product_id = %s AND tenant_id = %s",
                    (int(item["quantity"]), int(item["product_id"]), tid)
                )
            # Remove the credit-sale debit for this invoice (payments have NULL invoice_id)
            cur.execute("DELETE FROM credit_ledger WHERE invoice_id = %s AND type = 'debit' AND tenant_id = %s",
                        (iid, tid))
            # Remove the auto-synced journal entry for this sale (cascade clears lines)
            cur.execute(
                "DELETE FROM journal_entries WHERE source_type = 'sale' AND source_id = %s AND tenant_id = %s",
                (iid, tid))
            # Soft-delete
            from datetime import datetime as _dt
            cur.execute(
                """UPDATE invoices SET status='deleted', deleted_at=%s, deleted_by=%s, delete_reason=%s
                   WHERE invoice_id = %s AND tenant_id = %s""",
                (_dt.now(), deleted_by, reason, iid, tid)
            )


def get_invoice_items(invoice_id):
    with _conn() as conn:
        with conn.cursor() as cur:
            cur.execute(
                "SELECT * FROM invoice_items WHERE invoice_id = %s AND tenant_id = %s",
                (int(invoice_id), _tid())
            )
            return cur.fetchall()


def create_invoice(items, tax_rate, payment_method, customer_id=None):
    cid = normalize_customer_id(customer_id)
    if payment_method == "Credit" and cid is None:
        raise ValueError("Credit payment requires a customer to be selected.")
    tid = _tid()

    with _conn() as conn:
        with conn.cursor() as cur:
            if cid is not None:
                cur.execute("SELECT customer_id FROM customers WHERE customer_id = %s AND tenant_id = %s",
                            (cid, tid))
                if not cur.fetchone():
                    raise ValueError("Customer not found.")

            subtotal = 0.0
            discount_total = 0.0
            line_entries = []

            for item in items:
                pid = int(item["product_id"])
                qty = int(item["quantity"])
                discount_per_unit = float(item.get("discount_amount", 0))

                cur.execute("SELECT * FROM products WHERE product_id = %s AND tenant_id = %s FOR UPDATE",
                            (pid, tid))
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
                cur.execute("UPDATE products SET quantity = quantity - %s WHERE product_id = %s AND tenant_id = %s",
                            (qty, pid, tid))

            net = round(subtotal - discount_total, 2)
            tax_amount = round(net * tax_rate, 2)
            total = round(net + tax_amount, 2)

            cur.execute("""
                INSERT INTO invoices (tenant_id, created_at, subtotal, discount_total, tax_rate, tax_amount, total, payment_method, customer_id)
                VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s)
            """, (tid, datetime.now(), round(subtotal, 2), round(discount_total, 2), tax_rate, tax_amount, total, payment_method, cid))
            invoice_id = cur.lastrowid

            for pid, pname, pp, up, qty, ld, lt in line_entries:
                cur.execute("""
                    INSERT INTO invoice_items (tenant_id, invoice_id, product_id, product_name, purchase_price, counter_price, quantity, discount_amount, line_total)
                    VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s)
                """, (tid, invoice_id, pid, pname, pp, up, qty, ld, lt))

            if payment_method == "Credit" and cid is not None:
                cur.execute("""
                    INSERT INTO credit_ledger (tenant_id, customer_id, invoice_id, type, amount, note, created_at)
                    VALUES (%s, %s, %s, 'debit', %s, 'Credit sale', %s)
                """, (tid, cid, invoice_id, total, datetime.now()))

    return invoice_id


def update_invoice(invoice_id, items, tax_rate, payment_method=None, customer_id=None):
    iid = int(invoice_id)
    tid = _tid()
    with _conn() as conn:
        with conn.cursor() as cur:
            cur.execute("SELECT * FROM invoices WHERE invoice_id = %s AND tenant_id = %s FOR UPDATE",
                        (iid, tid))
            invoice = cur.fetchone()
            if not invoice:
                raise ValueError("Invoice not found.")

            cur.execute("SELECT * FROM invoice_items WHERE invoice_id = %s AND tenant_id = %s", (iid, tid))
            old_items = cur.fetchall()

            # Preserve original cost (COGS) per product so editing never rewrites
            # historical cost with today's price.
            old_cost_by_pid = {}
            for old in old_items:
                old_cost_by_pid.setdefault(int(old["product_id"]),
                                           float(old.get("purchase_price") or 0))

            # Return stock from old items
            for old in old_items:
                cur.execute(
                    "UPDATE products SET quantity = quantity + %s WHERE product_id = %s AND tenant_id = %s",
                    (int(old["quantity"]), int(old["product_id"]), tid)
                )

            old_payment = invoice.get("payment_method")
            old_cid = normalize_customer_id(invoice.get("customer_id"))
            new_payment = payment_method if payment_method is not None else old_payment
            new_cid = normalize_customer_id(customer_id) if customer_id is not None else old_cid

            if new_payment == "Credit" and new_cid is None:
                raise ValueError("Credit payment requires a customer.")
            if new_cid is not None:
                cur.execute("SELECT customer_id FROM customers WHERE customer_id = %s AND tenant_id = %s",
                            (new_cid, tid))
                if not cur.fetchone():
                    raise ValueError("Customer not found.")

            subtotal = 0.0
            discount_total = 0.0
            line_entries = []

            for item in items:
                pid = int(item["product_id"])
                qty = int(item["quantity"])
                discount_per_unit = float(item.get("discount_amount", 0))

                cur.execute("SELECT * FROM products WHERE product_id = %s AND tenant_id = %s FOR UPDATE",
                            (pid, tid))
                prod = cur.fetchone()
                if not prod:
                    raise ValueError(f"Product ID {pid} not found")
                available = int(prod["quantity"])
                if qty > available:
                    raise ValueError(
                        f"Not enough stock for '{prod['name']}': "
                        f"requested {qty}, available {available}"
                    )

                # Keep original cost for products already on this invoice;
                # use current cost only for newly added lines.
                purchase_price = old_cost_by_pid.get(pid, float(prod["purchase_price"]))
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
                cur.execute("UPDATE products SET quantity = quantity - %s WHERE product_id = %s AND tenant_id = %s",
                            (qty, pid, tid))

            net = round(subtotal - discount_total, 2)
            tax_amount = round(net * tax_rate, 2)
            total = round(net + tax_amount, 2)

            cur.execute("DELETE FROM invoice_items WHERE invoice_id = %s AND tenant_id = %s", (iid, tid))
            for pid, pname, pp, up, qty, ld, lt in line_entries:
                cur.execute("""
                    INSERT INTO invoice_items (tenant_id, invoice_id, product_id, product_name, purchase_price, counter_price, quantity, discount_amount, line_total)
                    VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s)
                """, (tid, iid, pid, pname, pp, up, qty, ld, lt))

            cur.execute("""
                UPDATE invoices SET
                    subtotal = %s, discount_total = %s, tax_rate = %s,
                    tax_amount = %s, total = %s, payment_method = %s, customer_id = %s
                WHERE invoice_id = %s AND tenant_id = %s
            """, (round(subtotal, 2), round(discount_total, 2), tax_rate, tax_amount, total, new_payment, new_cid, iid, tid))

            # Reconcile ledger: delete old debit for this invoice, re-add if Credit
            cur.execute(
                "DELETE FROM credit_ledger WHERE invoice_id = %s AND type = 'debit' AND tenant_id = %s",
                (iid, tid)
            )
            if new_payment == "Credit" and new_cid is not None:
                cur.execute("""
                    INSERT INTO credit_ledger (tenant_id, customer_id, invoice_id, type, amount, note, created_at)
                    VALUES (%s, %s, %s, 'debit', %s, 'Credit sale', %s)
                """, (tid, new_cid, iid, total, datetime.now()))

    return iid


# ── Credit Ledger ─────────────────────────────────────────────────────

def get_credit_ledger(customer_id=None):
    cid = normalize_customer_id(customer_id) if customer_id is not None else None
    with _conn() as conn:
        with conn.cursor() as cur:
            if cid is not None:
                cur.execute(
                    "SELECT * FROM credit_ledger WHERE customer_id = %s AND tenant_id = %s ORDER BY entry_id DESC",
                    (cid, _tid())
                )
            else:
                cur.execute("SELECT * FROM credit_ledger WHERE tenant_id = %s ORDER BY entry_id DESC",
                            (_tid(),))
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
                INSERT INTO credit_ledger (tenant_id, customer_id, invoice_id, type, amount, note, created_at)
                VALUES (%s, %s, NULL, 'credit', %s, %s, %s)
            """, (_tid(), cid, amount, (note or "").strip(), datetime.now()))
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
                INSERT INTO credit_ledger (tenant_id, customer_id, invoice_id, type, amount, note, created_at)
                VALUES (%s, %s, NULL, 'debit', %s, %s, %s)
            """, (_tid(), cid, amount, (note or "").strip(), datetime.now()))
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
                WHERE tenant_id = %s
                GROUP BY customer_id
            """, (_tid(),))
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
                WHERE tenant_id = %s AND DATE(created_at) = CURDATE()
                  AND (status != 'deleted' OR status IS NULL)
            """, (_tid(),))
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

def get_all_users(tenant_id=None):
    with _conn() as conn:
        with conn.cursor() as cur:
            if tenant_id is not None:
                cur.execute("SELECT * FROM users WHERE tenant_id = %s ORDER BY user_id",
                            (int(tenant_id),))
            else:
                cur.execute("SELECT * FROM users ORDER BY user_id")
            rows = cur.fetchall()
    for u in rows:
        u["is_active"] = bool(u["is_active"])
    return rows


def has_any_super_admin():
    with _conn() as conn:
        with conn.cursor() as cur:
            cur.execute("SELECT COUNT(*) AS cnt FROM users WHERE role = 'super_admin'")
            return cur.fetchone()["cnt"] > 0


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


def add_user(username, password_hash, role="staff", tenant_id=None):
    with _conn() as conn:
        with conn.cursor() as cur:
            cur.execute("""
                INSERT INTO users (tenant_id, username, password_hash, role, is_active, created_at)
                VALUES (%s, %s, %s, %s, 1, %s)
            """, (int(tenant_id) if tenant_id is not None else None,
                  username.strip().lower(), password_hash, role, datetime.now()))
            return cur.lastrowid


# ── Tenants (business accounts) ──────────────────────────────────────

def create_tenant(name):
    name = str(name).strip()
    if not name:
        raise ValueError("Business name is required.")
    with _conn() as conn:
        with conn.cursor() as cur:
            cur.execute("INSERT INTO tenants (name, is_active) VALUES (%s, 1)", (name,))
            return cur.lastrowid


def get_all_tenants(include_inactive=True):
    with _conn() as conn:
        with conn.cursor() as cur:
            if include_inactive:
                cur.execute("SELECT * FROM tenants ORDER BY tenant_id")
            else:
                cur.execute("SELECT * FROM tenants WHERE is_active = 1 ORDER BY tenant_id")
            rows = cur.fetchall()
    for t in rows:
        t["is_active"] = bool(t["is_active"])
    return rows


def get_tenant(tenant_id):
    with _conn() as conn:
        with conn.cursor() as cur:
            cur.execute("SELECT * FROM tenants WHERE tenant_id = %s", (int(tenant_id),))
            t = cur.fetchone()
    if t:
        t["is_active"] = bool(t["is_active"])
    return t


def set_tenant_active(tenant_id, active):
    with _conn() as conn:
        with conn.cursor() as cur:
            cur.execute("UPDATE tenants SET is_active = %s WHERE tenant_id = %s",
                        (1 if active else 0, int(tenant_id)))


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
            cur.execute("SELECT * FROM suppliers WHERE tenant_id = %s ORDER BY supplier_id", (_tid(),))
            return cur.fetchall()


def get_supplier(supplier_id):
    with _conn() as conn:
        with conn.cursor() as cur:
            cur.execute("SELECT * FROM suppliers WHERE supplier_id = %s AND tenant_id = %s",
                        (int(supplier_id), _tid()))
            return cur.fetchone()


def add_supplier(data):
    with _conn() as conn:
        with conn.cursor() as cur:
            cur.execute("""
                INSERT INTO suppliers (tenant_id, name, phone, email, address, notes, created_at)
                VALUES (%s,%s,%s,%s,%s,%s,%s)
            """, (
                _tid(),
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
                WHERE supplier_id=%s AND tenant_id=%s
            """, (
                (data.get("name") or "").strip(),
                (data.get("phone") or "").strip(),
                (data.get("email") or "").strip(),
                (data.get("address") or "").strip(),
                (data.get("notes") or "").strip(),
                int(supplier_id),
                _tid(),
            ))


def delete_supplier(supplier_id):
    sid = int(supplier_id)
    with _conn() as conn:
        with conn.cursor() as cur:
            cur.execute("SELECT COUNT(*) AS cnt FROM purchase_invoices WHERE supplier_id=%s AND tenant_id=%s",
                        (sid, _tid()))
            if cur.fetchone()["cnt"] > 0:
                raise ValueError("Cannot delete supplier: purchase invoices reference this supplier.")
            cur.execute("DELETE FROM suppliers WHERE supplier_id=%s AND tenant_id=%s", (sid, _tid()))
    return True


def supplier_lookup():
    return {s["supplier_id"]: s for s in get_all_suppliers()}


# ── Purchase Invoices ─────────────────────────────────────────────────

def create_purchase_invoice(supplier_id, items, notes="", payment_method="credit"):
    sid = int(supplier_id)
    tid = _tid()
    payment_method = (payment_method or "credit").strip().lower()
    if payment_method not in ("credit", "cash", "bank"):
        payment_method = "credit"
    with _conn() as conn:
        with conn.cursor() as cur:
            cur.execute("SELECT supplier_id FROM suppliers WHERE supplier_id=%s AND tenant_id=%s", (sid, tid))
            if not cur.fetchone():
                raise ValueError("Supplier not found.")

            total_amount = 0.0
            line_entries = []

            for item in items:
                pid = int(item["product_id"])
                qty = int(item["quantity"])
                unit_cost = float(item["unit_cost"])
                cur.execute("SELECT * FROM products WHERE product_id=%s AND tenant_id=%s FOR UPDATE", (pid, tid))
                prod = cur.fetchone()
                if not prod:
                    raise ValueError(f"Product ID {pid} not found")
                line_total = round(unit_cost * qty, 2)
                total_amount += line_total
                line_entries.append((pid, prod["name"], qty, unit_cost, line_total))
                # Weighted-average cost: blend old stock value with the new receipt
                old_qty = int(prod["quantity"] or 0)
                old_cost = float(prod["purchase_price"] or 0)
                new_qty = old_qty + qty
                if old_qty > 0 and old_cost > 0 and new_qty > 0:
                    new_cost = round((old_qty * old_cost + qty * unit_cost) / new_qty, 2)
                else:
                    new_cost = unit_cost
                cur.execute(
                    "UPDATE products SET quantity=%s, purchase_price=%s, last_supplier_id=%s WHERE product_id=%s AND tenant_id=%s",
                    (new_qty, new_cost, sid, pid, tid)
                )

            total_amount = round(total_amount, 2)
            cur.execute("""
                INSERT INTO purchase_invoices (tenant_id, supplier_id, created_at, total_amount, notes, payment_method)
                VALUES (%s,%s,%s,%s,%s,%s)
            """, (tid, sid, datetime.now(), total_amount, (notes or "").strip(), payment_method))
            purchase_id = cur.lastrowid

            for pid, pname, qty, uc, lt in line_entries:
                cur.execute("""
                    INSERT INTO purchase_invoice_items
                        (tenant_id, purchase_id, product_id, product_name, quantity, unit_cost, line_total)
                    VALUES (%s,%s,%s,%s,%s,%s,%s)
                """, (tid, purchase_id, pid, pname, qty, uc, lt))

            # Only a credit (unpaid) purchase creates a payable in the supplier ledger.
            if payment_method == "credit":
                cur.execute("""
                    INSERT INTO supplier_ledger (tenant_id, supplier_id, purchase_id, type, amount, note, created_at)
                    VALUES (%s,%s,%s,'debit',%s,%s,%s)
                """, (tid, sid, purchase_id, total_amount, f"Purchase invoice #{purchase_id}", datetime.now()))

    return purchase_id


def get_all_purchase_invoices():
    with _conn() as conn:
        with conn.cursor() as cur:
            cur.execute("SELECT * FROM purchase_invoices WHERE tenant_id=%s ORDER BY purchase_id DESC", (_tid(),))
            return cur.fetchall()


def get_purchase_invoice(purchase_id):
    with _conn() as conn:
        with conn.cursor() as cur:
            cur.execute("SELECT * FROM purchase_invoices WHERE purchase_id=%s AND tenant_id=%s",
                        (int(purchase_id), _tid()))
            return cur.fetchone()


def get_purchase_invoice_items(purchase_id):
    with _conn() as conn:
        with conn.cursor() as cur:
            cur.execute(
                "SELECT * FROM purchase_invoice_items WHERE purchase_id=%s AND tenant_id=%s",
                (int(purchase_id), _tid())
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
                INSERT INTO supplier_ledger (tenant_id, supplier_id, purchase_id, type, amount, note, created_at)
                VALUES (%s, %s, NULL, 'credit', %s, %s, %s)
            """, (_tid(), sid, amount, (note or "").strip(), datetime.now()))
            return cur.lastrowid


def get_supplier_ledger_entries(supplier_id=None):
    with _conn() as conn:
        with conn.cursor() as cur:
            if supplier_id is not None:
                cur.execute(
                    "SELECT * FROM supplier_ledger WHERE supplier_id=%s AND tenant_id=%s ORDER BY entry_id",
                    (int(supplier_id), _tid())
                )
            else:
                cur.execute("SELECT * FROM supplier_ledger WHERE tenant_id=%s ORDER BY entry_id", (_tid(),))
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
                FROM supplier_ledger WHERE tenant_id = %s GROUP BY supplier_id
            """, (_tid(),))
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

def _empty_pl_totals():
    zero = {"revenue": 0, "cogs": 0, "profit": 0}
    return {"revenue": 0, "cogs": 0, "profit": 0,
            "paid": dict(zero), "credit": dict(zero)}


def _split_pl_totals(rows):
    """Aggregate rows into grand totals plus paid vs credit buckets.
    Each row must have line_total, cogs, and is_credit."""
    buckets = {"paid": {"revenue": 0.0, "cogs": 0.0},
               "credit": {"revenue": 0.0, "cogs": 0.0}}
    for r in rows:
        b = buckets["credit"] if r.get("is_credit") else buckets["paid"]
        b["revenue"] += float(r["line_total"])
        b["cogs"] += float(r["cogs"])
    out = {}
    for key in ("paid", "credit"):
        rev = round(buckets[key]["revenue"], 2)
        cogs = round(buckets[key]["cogs"], 2)
        out[key] = {"revenue": rev, "cogs": cogs, "profit": round(rev - cogs, 2)}
    total_rev = round(out["paid"]["revenue"] + out["credit"]["revenue"], 2)
    total_cogs = round(out["paid"]["cogs"] + out["credit"]["cogs"], 2)
    out["revenue"] = total_rev
    out["cogs"] = total_cogs
    out["profit"] = round(total_rev - total_cogs, 2)
    return out


def get_sales_pl_report(start_date, end_date):
    with _conn() as conn:
        with conn.cursor() as cur:
            cur.execute("""
                SELECT
                    DATE(i.created_at) AS date,
                    i.invoice_id,
                    i.payment_method,
                    ii.product_name,
                    ii.quantity,
                    ii.purchase_price,
                    ROUND(ii.line_total / ii.quantity, 2) AS sale_price,
                    ii.line_total,
                    ROUND(ii.purchase_price * ii.quantity, 2) AS cogs,
                    ROUND(ii.line_total - ii.purchase_price * ii.quantity, 2) AS profit
                FROM invoice_items ii
                JOIN invoices i ON i.invoice_id = ii.invoice_id
                WHERE i.tenant_id = %s AND DATE(i.created_at) BETWEEN %s AND %s
                  AND (i.status IS NULL OR i.status <> 'deleted')
                ORDER BY i.created_at, i.invoice_id
            """, (_tid(), start_date, end_date))
            rows = cur.fetchall()
    for r in rows:
        r["is_credit"] = str(r.get("payment_method") or "").strip().lower() == "credit"
    if not rows:
        return [], _empty_pl_totals()
    return rows, _split_pl_totals(rows)


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
                WHERE p.last_supplier_id = %s AND i.tenant_id = %s
                  AND DATE(i.created_at) BETWEEN %s AND %s
                  AND (i.status IS NULL OR i.status <> 'deleted')
                GROUP BY ii.product_id, ii.product_name
                ORDER BY ii.product_name
            """, (sid, _tid(), start_date, end_date))
            rows = cur.fetchall()
    total_revenue = round(sum(float(r["total_revenue"]) for r in rows), 2)
    total_cogs = round(sum(float(r["total_cogs"]) for r in rows), 2)
    total_profit = round(total_revenue - total_cogs, 2)
    return rows, {"revenue": total_revenue, "cogs": total_cogs, "profit": total_profit}


# ── Double-entry accounting: data layer ──────────────────────────────

def seed_chart_of_accounts():
    tid = get_current_tenant()
    if tid is None:
        return  # no tenant context -> nothing to seed
    with _conn() as conn:
        with conn.cursor() as cur:
            cur.execute("SELECT COUNT(*) AS n FROM accounts WHERE tenant_id = %s", (tid,))
            if cur.fetchone()["n"] > 0:
                return
            cur.executemany(
                "INSERT INTO accounts (tenant_id, code, name, type, is_system) VALUES (%s,%s,%s,%s,%s)",
                [(tid, c, n, t, 1 if sys else 0) for c, n, t, sys in CHART_OF_ACCOUNTS],
            )


def get_all_accounts():
    with _conn() as conn:
        with conn.cursor() as cur:
            cur.execute("SELECT * FROM accounts WHERE tenant_id = %s ORDER BY code", (_tid(),))
            return cur.fetchall()


def get_account(account_id):
    with _conn() as conn:
        with conn.cursor() as cur:
            cur.execute("SELECT * FROM accounts WHERE account_id = %s AND tenant_id = %s",
                        (int(account_id), _tid()))
            return cur.fetchone()


def get_account_by_code(code):
    with _conn() as conn:
        with conn.cursor() as cur:
            cur.execute("SELECT * FROM accounts WHERE code = %s AND tenant_id = %s",
                        (str(code).strip(), _tid()))
            return cur.fetchone()


def get_accounts_by_type(atype):
    with _conn() as conn:
        with conn.cursor() as cur:
            cur.execute("SELECT * FROM accounts WHERE type = %s AND tenant_id = %s ORDER BY code",
                        (atype, _tid()))
            return cur.fetchall()


def add_account(code, name, atype, is_system=False):
    code = str(code).strip()
    if not code or not str(name).strip():
        raise ValueError("Account code and name are required.")
    if atype not in ("asset", "liability", "equity", "income", "expense"):
        raise ValueError("Invalid account type.")
    if get_account_by_code(code):
        raise ValueError(f"Account code {code} already exists.")
    with _conn() as conn:
        with conn.cursor() as cur:
            cur.execute(
                "INSERT INTO accounts (tenant_id, code, name, type, is_system) VALUES (%s,%s,%s,%s,%s)",
                (_tid(), code, str(name).strip(), atype, 1 if is_system else 0),
            )
            return cur.lastrowid


def post_journal(description, lines, source_type="manual", source_id=None,
                 created_by="system", entry_date=None):
    """Post a balanced double-entry journal entry. lines: list of
    {account_id, debit, credit}. Returns entry_id; raises ValueError if unbalanced."""
    if not lines or len(lines) < 2:
        raise ValueError("A journal entry needs at least two lines.")
    norm = []
    total_debit = 0.0
    total_credit = 0.0
    for ln in lines:
        aid = int(ln["account_id"])
        debit = round(float(ln.get("debit", 0) or 0), 2)
        credit = round(float(ln.get("credit", 0) or 0), 2)
        if debit < 0 or credit < 0:
            raise ValueError("Debit/credit cannot be negative.")
        if debit > 0 and credit > 0:
            raise ValueError("A line cannot have both debit and credit.")
        if debit == 0 and credit == 0:
            continue
        norm.append((aid, debit, credit))
        total_debit += debit
        total_credit += credit
    if not norm:
        raise ValueError("Journal entry has no non-zero lines.")
    if round(total_debit - total_credit, 2) != 0:
        raise ValueError(
            f"Entry not balanced: debits {total_debit:.2f} != credits {total_credit:.2f}")

    when = entry_date or datetime.now()
    tid = _tid()
    with _conn() as conn:
        with conn.cursor() as cur:
            ids = {a["account_id"] for a in get_all_accounts()}
            for aid, _, _ in norm:
                if aid not in ids:
                    raise ValueError(f"Account id {aid} not found.")
            cur.execute(
                """INSERT INTO journal_entries (tenant_id, date, description, source_type, source_id, created_by)
                   VALUES (%s,%s,%s,%s,%s,%s)""",
                (tid, when, str(description).strip(), source_type, source_id, created_by),
            )
            entry_id = cur.lastrowid
            cur.executemany(
                "INSERT INTO journal_lines (tenant_id, entry_id, account_id, debit, credit) VALUES (%s,%s,%s,%s,%s)",
                [(tid, entry_id, aid, debit, credit) for aid, debit, credit in norm],
            )
            return entry_id


def get_journal_lines(entry_id=None):
    with _conn() as conn:
        with conn.cursor() as cur:
            if entry_id is not None:
                cur.execute("SELECT * FROM journal_lines WHERE entry_id = %s AND tenant_id = %s",
                            (int(entry_id), _tid()))
            else:
                cur.execute("SELECT * FROM journal_lines WHERE tenant_id = %s", (_tid(),))
            return cur.fetchall()


def get_account_balance(account_id):
    aid = int(account_id)
    acct = get_account(aid)
    if not acct:
        raise ValueError("Account not found.")
    with _conn() as conn:
        with conn.cursor() as cur:
            cur.execute(
                "SELECT COALESCE(SUM(debit),0) AS d, COALESCE(SUM(credit),0) AS c "
                "FROM journal_lines WHERE account_id = %s AND tenant_id = %s", (aid, _tid()))
            r = cur.fetchone()
    debit = float(r["d"])
    credit = float(r["c"])
    if acct["type"] in _DEBIT_NORMAL_TYPES:
        return round(debit - credit, 2)
    return round(credit - debit, 2)


def get_trial_balance():
    with _conn() as conn:
        with conn.cursor() as cur:
            cur.execute("""
                SELECT a.account_id, a.code, a.name, a.type,
                       COALESCE(SUM(jl.debit),0) AS debit,
                       COALESCE(SUM(jl.credit),0) AS credit
                FROM accounts a
                LEFT JOIN journal_lines jl ON jl.account_id = a.account_id
                WHERE a.tenant_id = %s
                GROUP BY a.account_id, a.code, a.name, a.type
                ORDER BY a.code
            """, (_tid(),))
            raw = cur.fetchall()
    rows = []
    total_debit = 0.0
    total_credit = 0.0
    for r in raw:
        d = round(float(r["debit"]), 2)
        c = round(float(r["credit"]), 2)
        if d == 0 and c == 0:
            continue
        acct = {"account_id": r["account_id"], "code": r["code"],
                "name": r["name"], "type": r["type"]}
        if r["type"] in _DEBIT_NORMAL_TYPES:
            bal = round(d - c, 2)
        else:
            bal = round(c - d, 2)
        rows.append({"account": acct, "debit": d, "credit": c, "balance": bal})
        total_debit += d
        total_credit += c
    return rows, {"debit": round(total_debit, 2), "credit": round(total_credit, 2)}


# ── Expenses (built on the journal) ──────────────────────────────────

def record_expense(expense_account_id, amount, paid_from_account_id,
                   description="", created_by="system", entry_date=None):
    """Record an operating expense paid from a cash/bank account.
    Posts: Dr Expense, Cr Cash/Bank."""
    amount = round(float(amount), 2)
    if amount <= 0:
        raise ValueError("Amount must be positive.")
    exp = get_account(int(expense_account_id))
    if not exp or exp["type"] != "expense":
        raise ValueError("Select a valid expense category.")
    src = get_account(int(paid_from_account_id))
    if not src or src["type"] != "asset":
        raise ValueError("Select a valid account to pay from (Cash/Bank).")
    desc = (description or "").strip() or f"Expense: {exp['name']}"
    return post_journal(
        desc,
        [{"account_id": exp["account_id"], "debit": amount},
         {"account_id": src["account_id"], "credit": amount}],
        source_type="expense", created_by=created_by, entry_date=entry_date)


def get_expense_entries(start_date=None, end_date=None):
    """List expense journal entries with amount, category, and paid-from account."""
    where = ["je.source_type = 'expense'", "je.tenant_id = %s"]
    params = [_tid()]
    if start_date:
        where.append("DATE(je.date) >= %s")
        params.append(start_date)
    if end_date:
        where.append("DATE(je.date) <= %s")
        params.append(end_date)
    clause = " AND ".join(where)
    with _conn() as conn:
        with conn.cursor() as cur:
            cur.execute(f"""
                SELECT je.entry_id, je.date, je.description, je.created_by,
                       da.account_id AS exp_id, da.code AS exp_code, da.name AS exp_name, da.type AS exp_type,
                       ca.account_id AS src_id, ca.code AS src_code, ca.name AS src_name, ca.type AS src_type,
                       dl.debit AS amount
                FROM journal_entries je
                JOIN journal_lines dl ON dl.entry_id = je.entry_id AND dl.debit > 0
                JOIN accounts da ON da.account_id = dl.account_id
                LEFT JOIN journal_lines cl ON cl.entry_id = je.entry_id AND cl.credit > 0
                LEFT JOIN accounts ca ON ca.account_id = cl.account_id
                WHERE {clause}
                ORDER BY je.entry_id DESC
            """, params)
            raw = cur.fetchall()
    entries = []
    for r in raw:
        entries.append({
            "entry_id": r["entry_id"],
            "date": r["date"],
            "description": r["description"],
            "category": {"account_id": r["exp_id"], "code": r["exp_code"],
                         "name": r["exp_name"], "type": r["exp_type"]} if r["exp_id"] else None,
            "paid_from": {"account_id": r["src_id"], "code": r["src_code"],
                          "name": r["src_name"], "type": r["src_type"]} if r["src_id"] else None,
            "amount": round(float(r["amount"]), 2),
            "created_by": r["created_by"],
        })
    return entries


def update_account_name(account_id, name):
    aid = int(account_id)
    name = str(name).strip()
    if not name:
        raise ValueError("Name is required.")
    acct = get_account(aid)
    if not acct:
        raise ValueError("Account not found.")
    if acct.get("is_system"):
        raise ValueError("System accounts cannot be renamed.")
    with _conn() as conn:
        with conn.cursor() as cur:
            cur.execute("UPDATE accounts SET name = %s WHERE account_id = %s AND tenant_id = %s",
                        (name, aid, _tid()))


def next_expense_code():
    """Next available expense account code in the 5xxx+ range."""
    codes = [int(a["code"]) for a in get_accounts_by_type("expense")
             if str(a["code"]).isdigit()]
    base = max(codes) if codes else 5000
    code = base + 10
    existing = {str(a["code"]) for a in get_all_accounts()}
    while str(code) in existing:
        code += 10
    return str(code)


# ── Opening balances, journal sync, balance sheet ────────────────────

def get_books_start():
    with _conn() as conn:
        with conn.cursor() as cur:
            cur.execute("SELECT date FROM journal_entries WHERE source_type='opening' AND tenant_id=%s LIMIT 1",
                        (_tid(),))
            r = cur.fetchone()
    if not r or not r.get("date"):
        return None
    d = r["date"]
    return d.date() if isinstance(d, datetime) else d


def get_opening_balances():
    with _conn() as conn:
        with conn.cursor() as cur:
            cur.execute("""
                SELECT a.code, a.type, jl.debit, jl.credit
                FROM journal_entries je
                JOIN journal_lines jl ON jl.entry_id = je.entry_id
                JOIN accounts a ON a.account_id = jl.account_id
                WHERE je.source_type = 'opening' AND je.tenant_id = %s
            """, (_tid(),))
            raw = cur.fetchall()
    out = {}
    for r in raw:
        amount = float(r["debit"]) if r["type"] in _DEBIT_NORMAL_TYPES else float(r["credit"])
        out[str(r["code"])] = round(amount, 2)
    return out


def set_opening_balances(start_date, balances, created_by="system"):
    if isinstance(start_date, str):
        start_date = date.fromisoformat(start_date)
    acc = {str(a["code"]): a for a in get_all_accounts()}
    total_assets = 0.0
    total_liab = 0.0
    lines = []
    for code, amount in balances.items():
        amount = round(float(amount or 0), 2)
        if amount == 0:
            continue
        a = acc.get(str(code))
        if not a or str(code) == "3000":
            continue
        if a["type"] in _DEBIT_NORMAL_TYPES:
            lines.append((a["account_id"], amount, 0.0))
            total_assets += amount
        else:
            lines.append((a["account_id"], 0.0, amount))
            total_liab += amount
    capital = round(total_assets - total_liab, 2)
    cap_id = acc["3000"]["account_id"]
    if capital >= 0:
        lines.append((cap_id, 0.0, capital))
    else:
        lines.append((cap_id, -capital, 0.0))
    if len(lines) < 2:
        raise ValueError("Enter at least one opening balance.")
    tid = _tid()
    with _conn() as conn:
        with conn.cursor() as cur:
            # Replace opening + clear auto-synced operational entries so the next
            # sync re-posts only transactions on/after the new start date.
            cur.execute("""DELETE FROM journal_entries
                           WHERE tenant_id = %s AND source_type IN ('opening','sale','purchase',
                                                 'supplier_payment','customer_payment')""", (tid,))
            cur.execute(
                """INSERT INTO journal_entries (tenant_id, date, description, source_type, source_id, created_by)
                   VALUES (%s,%s,%s,'opening',NULL,%s)""",
                (tid, datetime.combine(start_date, datetime.min.time()), "Opening balances", created_by))
            eid = cur.lastrowid
            cur.executemany(
                "INSERT INTO journal_lines (tenant_id, entry_id, account_id, debit, credit) VALUES (%s,%s,%s,%s,%s)",
                [(tid, eid, aid, d, c) for aid, d, c in lines])
            return eid


def _existing_journal_sources():
    out = set()
    with _conn() as conn:
        with conn.cursor() as cur:
            cur.execute("SELECT source_type, source_id FROM journal_entries "
                        "WHERE source_id IS NOT NULL AND tenant_id = %s", (_tid(),))
            for r in cur.fetchall():
                out.add((str(r["source_type"]), str(int(r["source_id"]))))
    return out


def _realized_credit_invoice_ids():
    """Credit invoices considered paid: a customer's payments cover them oldest
    first. Returns the set of invoice_ids fully covered by received payments."""
    paid_by_cust = {}
    for e in get_credit_ledger():
        if e.get("type") == "credit":
            cid = normalize_customer_id(e.get("customer_id"))
            if cid is not None:
                paid_by_cust[cid] = paid_by_cust.get(cid, 0.0) + float(e.get("amount") or 0)
    credit_by_cust = {}
    for inv in get_all_invoices():
        if str(inv.get("payment_method") or "").strip().lower() == "credit":
            cid = normalize_customer_id(inv.get("customer_id"))
            if cid is not None:
                credit_by_cust.setdefault(cid, []).append(inv)
    realized = set()
    for cid, invs in credit_by_cust.items():
        invs.sort(key=lambda x: int(x["invoice_id"]))
        running = 0.0
        tp = round(paid_by_cust.get(cid, 0.0), 2)
        for inv in invs:
            running = round(running + float(inv.get("total") or 0), 2)
            if running <= tp:
                realized.add(int(inv["invoice_id"]))
            else:
                break
    return realized


def sync_journal_from_operations():
    acc = {str(a["code"]): a["account_id"] for a in get_all_accounts()}
    CASH, BANK, AR, INV, AP = acc["1000"], acc["1010"], acc["1100"], acc["1200"], acc["2000"]
    TAX, SALES, COGS = acc["2100"], acc["4000"], acc["5000"]
    existing = _existing_journal_sources()
    books_start = get_books_start()

    def _after_start(val):
        if books_start is None:
            return True
        d = val.date() if isinstance(val, datetime) else val
        return d is None or d >= books_start

    # Cash-basis for credit sales: a credit sale only reaches the books once the
    # customer's payments cover it (oldest invoice first). Cash sales post at once.
    realized = _realized_credit_invoice_ids()

    pending = []
    for inv in get_all_invoices():
        sid = int(inv["invoice_id"])
        if ("sale", str(sid)) in existing or not _after_start(inv.get("created_at")):
            continue
        is_credit = str(inv.get("payment_method") or "").strip().lower() == "credit"
        if is_credit and sid not in realized:
            continue  # unpaid credit sale -> stays off the balance sheet
        items = get_invoice_items(sid)
        cogs = round(sum(float(it["purchase_price"] or 0) * int(it["quantity"] or 0) for it in items), 2)
        net = round(float(inv.get("subtotal") or 0) - float(inv.get("discount_total") or 0), 2)
        tax = round(float(inv.get("tax_amount") or 0), 2)
        total = round(float(inv.get("total") or 0), 2)
        # Recorded as cash received (a realized credit sale has now been paid).
        lines = [(CASH, total, 0.0), (SALES, 0.0, net)]
        if tax > 0:
            lines.append((TAX, 0.0, tax))
        if cogs > 0:
            lines.append((COGS, cogs, 0.0))
            lines.append((INV, 0.0, cogs))
        pending.append((inv.get("created_at"), f"Sale INV-{sid}", "sale", sid, lines))

    _pay_acct = {"credit": AP, "cash": CASH, "bank": BANK}
    for p in get_all_purchase_invoices():
        pid = int(p["purchase_id"])
        if ("purchase", str(pid)) in existing or not _after_start(p.get("created_at")):
            continue
        total = round(float(p.get("total_amount") or 0), 2)
        if total <= 0:
            continue
        pm = str(p.get("payment_method") or "credit").strip().lower()
        credit_acct = _pay_acct.get(pm, AP)
        pending.append((p.get("created_at"), f"Purchase PINV-{pid}", "purchase", pid,
                        [(INV, total, 0.0), (credit_acct, 0.0, total)]))

    for e in get_supplier_ledger_entries():
        if e.get("type") != "credit":
            continue
        eid = int(e["entry_id"])
        if ("supplier_payment", str(eid)) in existing or not _after_start(e.get("created_at")):
            continue
        amt = round(float(e.get("amount") or 0), 2)
        if amt <= 0:
            continue
        pending.append((e.get("created_at"), "Supplier payment", "supplier_payment", eid,
                        [(AP, amt, 0.0), (CASH, 0.0, amt)]))

    # Customer credit repayments are NOT posted separately: the cash for a credit
    # sale enters the books when that sale is recognised (above), keeping the
    # balance sheet on a cash basis for credit.

    if not pending:
        return 0
    tid = _tid()
    with _conn() as conn:
        with conn.cursor() as cur:
            for when, desc, stype, sid, lines in pending:
                td = round(sum(d for _, d, _ in lines), 2)
                tc = round(sum(c for _, _, c in lines), 2)
                if td != tc:
                    continue
                cur.execute(
                    """INSERT INTO journal_entries (tenant_id, date, description, source_type, source_id, created_by)
                       VALUES (%s,%s,%s,%s,%s,'system')""", (tid, when, desc, stype, sid))
                eid = cur.lastrowid
                cur.executemany(
                    "INSERT INTO journal_lines (tenant_id, entry_id, account_id, debit, credit) VALUES (%s,%s,%s,%s,%s)",
                    [(tid, eid, aid, d, c) for aid, d, c in lines])
    return len(pending)


def get_balance_sheet():
    rows, _ = get_trial_balance()
    sections = {"asset": [], "liability": [], "equity": []}
    total = {"asset": 0.0, "liability": 0.0, "equity": 0.0}
    income = 0.0
    expense = 0.0
    for r in rows:
        atype = r["account"]["type"]
        bal = r["balance"]
        if atype in sections:
            sections[atype].append({"name": r["account"]["name"], "balance": bal})
            total[atype] += bal
        elif atype == "income":
            income += bal
        elif atype == "expense":
            expense += bal
    net_income = round(income - expense, 2)
    equity_rows = list(sections["equity"])
    equity_rows.append({"name": "Net Income (current)", "balance": net_income})
    total_equity = round(total["equity"] + net_income, 2)
    return {
        "assets": sections["asset"],
        "liabilities": sections["liability"],
        "equity": equity_rows,
        "total_assets": round(total["asset"], 2),
        "total_liabilities": round(total["liability"], 2),
        "total_equity": total_equity,
        "total_liab_equity": round(total["liability"] + total_equity, 2),
        "net_income": net_income,
    }


# ── Partners / investor equity ───────────────────────────────────────

def get_all_partners(include_inactive=False):
    with _conn() as conn:
        with conn.cursor() as cur:
            if include_inactive:
                cur.execute("SELECT * FROM partners WHERE tenant_id = %s ORDER BY partner_id", (_tid(),))
            else:
                cur.execute("SELECT * FROM partners WHERE tenant_id = %s AND is_active = 1 ORDER BY partner_id",
                            (_tid(),))
            rows = cur.fetchall()
    for r in rows:
        r["share_pct"] = float(r["share_pct"] or 0)
        r["is_active"] = bool(r["is_active"])
    return rows


def get_partner(partner_id):
    with _conn() as conn:
        with conn.cursor() as cur:
            cur.execute("SELECT * FROM partners WHERE partner_id = %s AND tenant_id = %s",
                        (int(partner_id), _tid()))
            r = cur.fetchone()
    if r:
        r["share_pct"] = float(r["share_pct"] or 0)
        r["is_active"] = bool(r["is_active"])
    return r


def add_partner(name, share_pct):
    name = str(name).strip()
    if not name:
        raise ValueError("Partner name is required.")
    share = round(float(share_pct or 0), 2)
    if share < 0 or share > 100:
        raise ValueError("Share % must be between 0 and 100.")
    with _conn() as conn:
        with conn.cursor() as cur:
            cur.execute("INSERT INTO partners (tenant_id, name, share_pct, is_active) VALUES (%s,%s,%s,1)",
                        (_tid(), name, share))
            return cur.lastrowid


def update_partner(partner_id, name, share_pct, is_active=True):
    name = str(name).strip()
    if not name:
        raise ValueError("Partner name is required.")
    share = round(float(share_pct or 0), 2)
    if share < 0 or share > 100:
        raise ValueError("Share % must be between 0 and 100.")
    if not get_partner(partner_id):
        raise ValueError("Partner not found.")
    with _conn() as conn:
        with conn.cursor() as cur:
            cur.execute("UPDATE partners SET name=%s, share_pct=%s, is_active=%s WHERE partner_id=%s AND tenant_id=%s",
                        (name, share, 1 if is_active else 0, int(partner_id), _tid()))


def add_partner_transaction(partner_id, txn_type, amount, note="", txn_date=None,
                            created_by="system"):
    pid = int(partner_id)
    if txn_type not in ("capital", "drawing"):
        raise ValueError("Type must be 'capital' or 'drawing'.")
    amount = round(float(amount or 0), 2)
    if amount <= 0:
        raise ValueError("Amount must be positive.")
    if not get_partner(pid):
        raise ValueError("Partner not found.")
    when = txn_date or date.today()
    if isinstance(when, str):
        when = date.fromisoformat(when)
    when_dt = datetime.combine(when, datetime.min.time())
    with _conn() as conn:
        with conn.cursor() as cur:
            cur.execute(
                """INSERT INTO partner_transactions (tenant_id, partner_id, type, amount, note, date)
                   VALUES (%s,%s,%s,%s,%s,%s)""",
                (_tid(), pid, txn_type, amount, (note or "").strip(), when_dt))
            txn_id = cur.lastrowid
    acc = {str(a["code"]): a["account_id"] for a in get_all_accounts()}
    if txn_type == "capital":
        lines = [{"account_id": acc["1000"], "debit": amount},
                 {"account_id": acc["3000"], "credit": amount}]
        desc, stype = "Partner capital contribution", "partner_capital"
    else:
        lines = [{"account_id": acc["3100"], "debit": amount},
                 {"account_id": acc["1000"], "credit": amount}]
        desc, stype = "Partner drawing", "partner_drawing"
    post_journal(desc, lines, source_type=stype, source_id=txn_id,
                 created_by=created_by, entry_date=when_dt)
    return txn_id


def get_partner_transactions(partner_id=None):
    with _conn() as conn:
        with conn.cursor() as cur:
            if partner_id is not None:
                cur.execute("SELECT * FROM partner_transactions WHERE partner_id = %s AND tenant_id = %s ORDER BY txn_id DESC",
                            (int(partner_id), _tid()))
            else:
                cur.execute("SELECT * FROM partner_transactions WHERE tenant_id = %s ORDER BY txn_id DESC",
                            (_tid(),))
            return cur.fetchall()


def get_partner_equity():
    partners = get_all_partners()
    net_income = get_balance_sheet()["net_income"]
    txns = get_partner_transactions()
    cap_by, draw_by = {}, {}
    for t in txns:
        p = int(t["partner_id"])
        amt = float(t["amount"] or 0)
        if t["type"] == "capital":
            cap_by[p] = cap_by.get(p, 0.0) + amt
        elif t["type"] == "drawing":
            draw_by[p] = draw_by.get(p, 0.0) + amt
    rows = []
    for p in partners:
        pid = p["partner_id"]
        cap = round(cap_by.get(pid, 0.0), 2)
        draw = round(draw_by.get(pid, 0.0), 2)
        profit_share = round(net_income * (p["share_pct"] / 100.0), 2)
        equity = round(cap + profit_share - draw, 2)
        rows.append({"partner": p, "capital": cap, "share_pct": p["share_pct"],
                     "profit_share": profit_share, "drawings": draw, "equity": equity})
    totals = {
        "capital": round(sum(r["capital"] for r in rows), 2),
        "profit_share": round(sum(r["profit_share"] for r in rows), 2),
        "drawings": round(sum(r["drawings"] for r in rows), 2),
        "equity": round(sum(r["equity"] for r in rows), 2),
        "share_pct": round(sum(p["share_pct"] for p in partners), 2),
        "net_income": net_income,
    }
    return rows, totals
