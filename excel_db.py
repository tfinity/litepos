"""Excel-based data access layer for the POS system."""

import threading
from datetime import datetime, date, timedelta
from pathlib import Path

from openpyxl import Workbook, load_workbook

DATA_FILE = Path(__file__).parent / "data.xlsx"
_lock = threading.Lock()

PRODUCT_HEADERS = [
    "product_id", "name", "purchase_price", "counter_price", "retail_price",
    "quantity", "barcode", "expiry_date", "category", "created_at",
]
INVOICE_HEADERS = [
    "invoice_id", "created_at", "subtotal", "discount_total",
    "tax_rate", "tax_amount", "total", "payment_method",
]
ITEM_HEADERS = [
    "item_id", "invoice_id", "product_id", "product_name",
    "purchase_price", "counter_price", "quantity",
    "discount_amount", "line_total",
]


def init_workbook():
    """Create data.xlsx with header rows if it doesn't exist."""
    if DATA_FILE.exists():
        return
    wb = Workbook()
    ws = wb.active
    ws.title = "Products"
    ws.append(PRODUCT_HEADERS)
    ws2 = wb.create_sheet("Invoices")
    ws2.append(INVOICE_HEADERS)
    ws3 = wb.create_sheet("InvoiceItems")
    ws3.append(ITEM_HEADERS)
    wb.save(DATA_FILE)


def _open():
    return load_workbook(DATA_FILE)


def _next_id(ws):
    max_id = 0
    for row in ws.iter_rows(min_row=2, max_col=1, values_only=True):
        if row[0] is not None and isinstance(row[0], (int, float)):
            max_id = max(max_id, int(row[0]))
    return max_id + 1


def _row_to_dict(headers, row):
    d = {}
    for i, h in enumerate(headers):
        val = row[i] if i < len(row) else None
        d[h] = val
    return d


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
    with _lock:
        wb = _open()
        ws = wb["Products"]
        products = []
        for row in ws.iter_rows(min_row=2, values_only=True):
            if row[0] is None:
                continue
            p = _row_to_dict(PRODUCT_HEADERS, row)
            p["expiry_date"] = _normalize_date(p["expiry_date"])
            products.append(p)
        wb.close()
    return products


def get_product(product_id):
    for p in get_all_products():
        if int(p["product_id"]) == int(product_id):
            return p
    return None


def get_product_by_barcode(barcode):
    for p in get_all_products():
        if str(p.get("barcode", "")).strip() == str(barcode).strip():
            return p
    return None


def add_product(data):
    with _lock:
        wb = _open()
        ws = wb["Products"]
        pid = _next_id(ws)
        expiry = data.get("expiry_date")
        if isinstance(expiry, str) and expiry:
            expiry = datetime.strptime(expiry, "%Y-%m-%d").date()
        elif not isinstance(expiry, (date, datetime)):
            expiry = None
        ws.append([
            pid,
            data["name"],
            float(data.get("purchase_price", 0)),
            float(data.get("counter_price", 0)),
            float(data.get("retail_price", 0)),
            int(data["quantity"]),
            data.get("barcode", ""),
            expiry,
            data.get("category", ""),
            datetime.now(),
        ])
        wb.save(DATA_FILE)
        wb.close()
    return pid


def update_product(product_id, data):
    with _lock:
        wb = _open()
        ws = wb["Products"]
        for row in ws.iter_rows(min_row=2):
            if row[0].value is not None and int(row[0].value) == int(product_id):
                row[1].value = data["name"]
                row[2].value = float(data.get("purchase_price", 0))
                row[3].value = float(data.get("counter_price", 0))
                row[4].value = float(data.get("retail_price", 0))
                row[5].value = int(data["quantity"])
                row[6].value = data.get("barcode", "")
                expiry = data.get("expiry_date")
                if isinstance(expiry, str) and expiry:
                    expiry = datetime.strptime(expiry, "%Y-%m-%d").date()
                elif not isinstance(expiry, (date, datetime)):
                    expiry = None
                row[7].value = expiry
                row[8].value = data.get("category", "")
                break
        wb.save(DATA_FILE)
        wb.close()


def delete_product(product_id):
    with _lock:
        wb = _open()
        ws = wb["Products"]
        for idx, row in enumerate(ws.iter_rows(min_row=2), start=2):
            if row[0].value is not None and int(row[0].value) == int(product_id):
                ws.delete_rows(idx)
                break
        wb.save(DATA_FILE)
        wb.close()


def get_low_stock_products(threshold=10):
    return [p for p in get_all_products() if int(p["quantity"]) <= threshold]


def get_expiry_products(days_ahead=30):
    today = date.today()
    cutoff = today + timedelta(days=days_ahead)
    results = []
    for p in get_all_products():
        exp = p["expiry_date"]
        if exp is None:
            continue
        if exp <= cutoff:
            p["expired"] = exp < today
            p["days_left"] = (exp - today).days
            results.append(p)
    results.sort(key=lambda x: x["expiry_date"])
    return results


# ── Invoices ──────────────────────────────────────────────────────────

def get_all_invoices():
    with _lock:
        wb = _open()
        ws = wb["Invoices"]
        invoices = []
        for row in ws.iter_rows(min_row=2, values_only=True):
            if row[0] is None:
                continue
            invoices.append(_row_to_dict(INVOICE_HEADERS, row))
        wb.close()
    invoices.sort(key=lambda x: x["invoice_id"], reverse=True)
    return invoices


def get_invoice(invoice_id):
    for inv in get_all_invoices():
        if int(inv["invoice_id"]) == int(invoice_id):
            return inv
    return None


def get_invoice_items(invoice_id):
    with _lock:
        wb = _open()
        ws = wb["InvoiceItems"]
        items = []
        for row in ws.iter_rows(min_row=2, values_only=True):
            if row[0] is None:
                continue
            item = _row_to_dict(ITEM_HEADERS, row)
            if int(item["invoice_id"]) == int(invoice_id):
                items.append(item)
        wb.close()
    return items


def create_invoice(items, tax_rate, payment_method):
    """
    items: list of dicts with keys: product_id, quantity, discount_amount,
    optional unit_price (sale price per unit for this line; defaults to product counter_price).
    discount_amount is a direct amount per unit (not percentage).
    Returns the new invoice_id, or raises ValueError on stock/discount issues.
    """
    with _lock:
        wb = _open()
        ws_products = wb["Products"]
        ws_invoices = wb["Invoices"]
        ws_items = wb["InvoiceItems"]

        invoice_id = _next_id(ws_invoices)
        item_id_start = _next_id(ws_items)

        product_rows = {}
        for row in ws_products.iter_rows(min_row=2):
            if row[0].value is not None:
                product_rows[int(row[0].value)] = row

        subtotal = 0.0
        discount_total = 0.0
        line_entries = []

        for i, item in enumerate(items):
            pid = int(item["product_id"])
            qty = int(item["quantity"])
            discount_per_unit = float(item.get("discount_amount", 0))

            if pid not in product_rows:
                raise ValueError(f"Product ID {pid} not found")
            prow = product_rows[pid]
            available = int(prow[5].value)  # quantity column
            if qty > available:
                raise ValueError(
                    f"Not enough stock for '{prow[1].value}': "
                    f"requested {qty}, available {available}"
                )

            purchase_price = float(prow[2].value)   # purchase_price
            catalog_counter = float(prow[3].value)  # product counter_price

            raw_unit = item.get("unit_price")
            if raw_unit is None or raw_unit == "":
                unit_price = catalog_counter
            else:
                unit_price = float(raw_unit)

            if unit_price < 0:
                raise ValueError(
                    f"Invalid sale price for '{prow[1].value}': must be non-negative"
                )

            # Guard: price after per-unit discount must not go below purchase price
            discounted_price = unit_price - discount_per_unit
            if discounted_price < purchase_price:
                raise ValueError(
                    f"Discount too high for '{prow[1].value}': "
                    f"price after discount {discounted_price:.2f} is below "
                    f"purchase price {purchase_price:.2f}. "
                    f"Max discount: {unit_price - purchase_price:.2f}"
                )

            line_discount = discount_per_unit * qty
            line_total = discounted_price * qty
            subtotal += unit_price * qty  # pre-discount subtotal (uses line sale price)
            discount_total += line_discount

            line_entries.append([
                item_id_start + i,
                invoice_id,
                pid,
                prow[1].value,       # product_name
                purchase_price,
                unit_price,          # effective unit price for this line (receipt / history)
                qty,
                line_discount,
                round(line_total, 2),
            ])
            # Decrement stock
            prow[5].value = available - qty

        net_subtotal = round(subtotal - discount_total, 2)
        tax_amount = round(net_subtotal * tax_rate, 2)
        total = round(net_subtotal + tax_amount, 2)

        ws_invoices.append([
            invoice_id,
            datetime.now(),
            round(subtotal, 2),
            round(discount_total, 2),
            tax_rate,
            tax_amount,
            total,
            payment_method,
        ])

        for entry in line_entries:
            ws_items.append(entry)

        wb.save(DATA_FILE)
        wb.close()

    return invoice_id


def get_today_sales():
    today = date.today()
    count = 0
    total = 0.0
    for inv in get_all_invoices():
        created = inv["created_at"]
        if isinstance(created, datetime):
            inv_date = created.date()
        elif isinstance(created, date):
            inv_date = created
        else:
            continue
        if inv_date == today:
            count += 1
            total += float(inv["total"] or 0)
    return count, round(total, 2)


def search_products(query):
    q = str(query).lower().strip()
    results = []
    for p in get_all_products():
        if (q in str(p["name"]).lower()
                or q in str(p.get("barcode", "")).lower()):
            results.append(p)
    return results[:20]


def _parse_expiry(val):
    """Parse expiry from various formats: datetime, 'MM YYYY', 'M YYYY', date string."""
    if val is None or val == "" or val == "None":
        return None
    if isinstance(val, datetime):
        return val.date()
    if isinstance(val, date):
        return val
    s = str(val).strip()
    import re
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
    """
    Import products from external Excel file.
    Columns: Sr.No, Product Name, Packing, Qty, Price(purchase), Company, Expiry, MRP, Sale Price(counter)
    Returns (imported_count, skipped_count, errors).
    """
    wb_src = load_workbook(filepath)
    ws = wb_src.active

    imported = 0
    skipped = 0
    errors = []

    # Detect if column I (index 8) exists (Sale Price / counter_price)
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

        qty = 0
        try:
            qty = int(row[3] or 0)
        except (ValueError, TypeError):
            pass

        purchase_price = 0.0
        try:
            purchase_price = float(row[4] or 0)
        except (ValueError, TypeError):
            pass

        company = str(row[5] or "").strip()
        expiry = _parse_expiry(row[6])

        mrp = 0.0
        try:
            mrp = float(row[7] or 0)
        except (ValueError, TypeError):
            pass

        # Counter price from column I if available, otherwise fall back to MRP
        counter_price = 0.0
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
