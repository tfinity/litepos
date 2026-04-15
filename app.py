"""Flask POS Application - Excel-based Point of Sale System."""

import os
from datetime import date
from dotenv import load_dotenv

load_dotenv()

from flask import (
    Flask, render_template, request, redirect, url_for,
    flash, jsonify,
)

import excel_db

app = Flask(__name__)
app.secret_key = os.environ.get("SECRET_KEY", os.urandom(24).hex())

# Configuration - override via environment variables or edit directly
TAX_RATE = float(os.environ.get("TAX_RATE", "0.0"))
LOW_STOCK_THRESHOLD = int(os.environ.get("LOW_STOCK_THRESHOLD", "10"))
EXPIRY_WARNING_DAYS = int(os.environ.get("EXPIRY_WARNING_DAYS", "30"))
BUSINESS_NAME = os.environ.get("BUSINESS_NAME", "My Pharmacy")
BUSINESS_ADDRESS = os.environ.get("BUSINESS_ADDRESS", "123 Main Street")
BUSINESS_PHONE = os.environ.get("BUSINESS_PHONE", "+1 000 000 0000")
CURRENCY = os.environ.get("CURRENCY", "USD")
RECEIPT_FOOTER = os.environ.get("RECEIPT_FOOTER", "")

# Initialize Excel workbook on startup
excel_db.init_workbook()


@app.context_processor
def inject_globals():
    return {
        "business_name": BUSINESS_NAME,
        "currency": CURRENCY,
        "today": date.today(),
    }


# ── Dashboard ────────────────────────────────────────────────────────

@app.route("/")
def dashboard():
    products = excel_db.get_all_products()
    low_stock = excel_db.get_low_stock_products(LOW_STOCK_THRESHOLD)
    expiring = excel_db.get_expiry_products(EXPIRY_WARNING_DAYS)
    sales_count, sales_total = excel_db.get_today_sales()
    balances = excel_db.get_all_credit_balances()
    credit_outstanding = round(sum(b["balance"] for b in balances if b["balance"] > 0), 2)
    credit_customers = sum(1 for b in balances if b["balance"] > 0)
    return render_template("dashboard.html",
                           total_products=len(products),
                           low_stock_count=len(low_stock),
                           expiring_count=len(expiring),
                           sales_count=sales_count,
                           sales_total=sales_total,
                           credit_outstanding=credit_outstanding,
                           credit_customers=credit_customers)


# ── Products ─────────────────────────────────────────────────────────

@app.route("/products")
def products():
    all_products = excel_db.get_all_products()
    return render_template("products.html",
                           products=all_products,
                           threshold=LOW_STOCK_THRESHOLD)


@app.route("/products/add", methods=["GET", "POST"])
def product_add():
    if request.method == "POST":
        data = {
            "name": request.form["name"],
            "purchase_price": request.form["purchase_price"],
            "counter_price": request.form["counter_price"],
            "retail_price": request.form.get("retail_price", "0"),
            "quantity": request.form["quantity"],
            "barcode": request.form.get("barcode", ""),
            "expiry_date": request.form.get("expiry_date", ""),
            "category": request.form.get("category", ""),
        }
        excel_db.add_product(data)
        flash("Product added successfully!", "success")
        return redirect(url_for("products"))
    categories = sorted(set(
        p["category"] for p in excel_db.get_all_products()
        if p.get("category")
    ))
    return render_template("product_form.html",
                           product=None, categories=categories)


@app.route("/products/<int:product_id>/edit", methods=["GET", "POST"])
def product_edit(product_id):
    product = excel_db.get_product(product_id)
    if not product:
        flash("Product not found.", "danger")
        return redirect(url_for("products"))
    if request.method == "POST":
        data = {
            "name": request.form["name"],
            "purchase_price": request.form["purchase_price"],
            "counter_price": request.form["counter_price"],
            "retail_price": request.form.get("retail_price", "0"),
            "quantity": request.form["quantity"],
            "barcode": request.form.get("barcode", ""),
            "expiry_date": request.form.get("expiry_date", ""),
            "category": request.form.get("category", ""),
        }
        excel_db.update_product(product_id, data)
        flash("Product updated successfully!", "success")
        return redirect(url_for("products"))
    categories = sorted(set(
        p["category"] for p in excel_db.get_all_products()
        if p.get("category")
    ))
    return render_template("product_form.html",
                           product=product, categories=categories)


@app.route("/products/<int:product_id>/delete", methods=["POST"])
def product_delete(product_id):
    excel_db.delete_product(product_id)
    flash("Product deleted.", "success")
    return redirect(url_for("products"))


# ── Reports ──────────────────────────────────────────────────────────

@app.route("/stock-report")
def stock_report():
    products = excel_db.get_all_products()
    products.sort(key=lambda p: int(p["quantity"]))
    return render_template("stock_report.html",
                           products=products,
                           threshold=LOW_STOCK_THRESHOLD)


@app.route("/expiry-report")
def expiry_report():
    products = excel_db.get_expiry_products(EXPIRY_WARNING_DAYS)
    return render_template("expiry_report.html", products=products)


# ── Invoices ─────────────────────────────────────────────────────────

def _attach_customer(invoice, cmap):
    """Mutate invoice dict with optional ``customer`` profile (or None)."""
    cid = excel_db.normalize_customer_id(invoice.get("customer_id"))
    invoice["customer"] = cmap.get(cid) if cid is not None else None


@app.route("/invoices")
def invoices():
    cmap = excel_db.customer_lookup()
    all_invoices = []
    for inv in excel_db.get_all_invoices():
        inv = dict(inv)
        _attach_customer(inv, cmap)
        all_invoices.append(inv)
    return render_template("invoices.html", invoices=all_invoices)


@app.route("/invoices/create", methods=["GET", "POST"])
def invoice_create():
    if request.method == "POST":
        data = request.get_json()
        items = data.get("items", [])
        payment_method = data.get("payment_method", "Cash")
        if not items:
            return jsonify({"error": "No items in invoice"}), 400
        try:
            invoice_id = excel_db.create_invoice(
                items,
                TAX_RATE,
                payment_method,
                customer_id=data.get("customer_id"),
            )
            return jsonify({"invoice_id": invoice_id})
        except ValueError as e:
            return jsonify({"error": str(e)}), 400
    products = excel_db.get_all_products()
    products = [p for p in products if int(p["quantity"]) > 0]
    return render_template("invoice_create.html",
                           products=products, tax_rate=TAX_RATE)


@app.route("/invoices/<int:invoice_id>")
def invoice_detail(invoice_id):
    invoice = excel_db.get_invoice(invoice_id)
    if not invoice:
        flash("Invoice not found.", "danger")
        return redirect(url_for("invoices"))
    invoice = dict(invoice)
    cmap = excel_db.customer_lookup()
    _attach_customer(invoice, cmap)
    items = excel_db.get_invoice_items(invoice_id)
    return render_template("invoice_detail.html",
                           invoice=invoice, items=items)


@app.route("/invoices/<int:invoice_id>/receipt")
def invoice_receipt(invoice_id):
    invoice = excel_db.get_invoice(invoice_id)
    if not invoice:
        flash("Invoice not found.", "danger")
        return redirect(url_for("invoices"))
    invoice = dict(invoice)
    cmap = excel_db.customer_lookup()
    _attach_customer(invoice, cmap)
    items = excel_db.get_invoice_items(invoice_id)
    return render_template("receipt.html",
                           invoice=invoice, items=items,
                           business_name=BUSINESS_NAME,
                           business_address=BUSINESS_ADDRESS,
                           business_phone=BUSINESS_PHONE,
                           receipt_footer=RECEIPT_FOOTER)


# ── API Endpoints ────────────────────────────────────────────────────

# ── Customers ────────────────────────────────────────────────────────


@app.route("/customers")
def customers_list():
    customers = excel_db.get_all_customers()
    cmap = excel_db.customer_lookup()
    inv_counts = {cid: 0 for cid in cmap}
    for inv in excel_db.get_all_invoices():
        cid = excel_db.normalize_customer_id(inv.get("customer_id"))
        if cid is not None and cid in inv_counts:
            inv_counts[cid] += 1
    return render_template(
        "customers.html",
        customers=customers,
        inv_counts=inv_counts,
    )


@app.route("/customers/add", methods=["GET", "POST"])
def customer_add():
    if request.method == "POST":
        data = {
            "name": request.form.get("name", ""),
            "phone": request.form.get("phone", ""),
            "email": request.form.get("email", ""),
            "address": request.form.get("address", ""),
            "tax_id": request.form.get("tax_id", ""),
            "notes": request.form.get("notes", ""),
        }
        if not (data["name"] or "").strip():
            flash("Name is required.", "danger")
            return render_template("customer_form.html", customer=None, data=data)
        excel_db.add_customer(data)
        flash("Customer added.", "success")
        return redirect(url_for("customers_list"))
    return render_template("customer_form.html", customer=None, data=None)


@app.route("/customers/<int:customer_id>/edit", methods=["GET", "POST"])
def customer_edit(customer_id):
    customer = excel_db.get_customer(customer_id)
    if not customer:
        flash("Customer not found.", "danger")
        return redirect(url_for("customers_list"))
    if request.method == "POST":
        data = {
            "name": request.form.get("name", ""),
            "phone": request.form.get("phone", ""),
            "email": request.form.get("email", ""),
            "address": request.form.get("address", ""),
            "tax_id": request.form.get("tax_id", ""),
            "notes": request.form.get("notes", ""),
        }
        if not (data["name"] or "").strip():
            flash("Name is required.", "danger")
            return render_template("customer_form.html", customer=customer, data=data)
        excel_db.update_customer(customer_id, data)
        flash("Customer updated.", "success")
        return redirect(url_for("customers_list"))
    return render_template("customer_form.html", customer=customer, data=None)


@app.route("/customers/<int:customer_id>/delete", methods=["POST"])
def customer_delete(customer_id):
    try:
        excel_db.delete_customer(customer_id)
        flash("Customer deleted.", "success")
    except ValueError as e:
        flash(str(e), "danger")
    return redirect(url_for("customers_list"))


@app.route("/customers/<int:customer_id>")
def customer_detail(customer_id):
    customer = excel_db.get_customer(customer_id)
    if not customer:
        flash("Customer not found.", "danger")
        return redirect(url_for("customers_list"))
    invoices = excel_db.get_invoices_for_customer(customer_id)
    aggregates = excel_db.get_customer_product_aggregates(customer_id)
    total_qty = sum(a["total_qty"] for a in aggregates)
    total_lines_amount = sum(a["total_amount"] for a in aggregates)
    revenue = sum(float(i["total"] or 0) for i in invoices)
    credit_debt, credit_paid, credit_balance = excel_db.get_customer_balance(customer_id)
    ledger_entries = excel_db.get_credit_ledger(customer_id=customer_id)
    return render_template(
        "customer_detail.html",
        customer=customer,
        invoices=invoices,
        aggregates=aggregates,
        total_qty=total_qty,
        total_lines_amount=round(total_lines_amount, 2),
        revenue=round(revenue, 2),
        credit_debt=credit_debt,
        credit_paid=credit_paid,
        credit_balance=credit_balance,
        ledger_entries=ledger_entries,
    )


@app.route("/customers/sales-summary")
def customers_sales_summary():
    rows, walk_in = excel_db.get_sales_summary_by_customer()
    return render_template(
        "customers_sales_summary.html",
        rows=rows,
        walk_in=walk_in,
    )


@app.route("/api/products/search")
def api_product_search():
    q = request.args.get("q", "")
    if len(q) < 1:
        return jsonify([])
    results = excel_db.search_products(q)
    for r in results:
        if r.get("expiry_date"):
            r["expiry_date"] = str(r["expiry_date"])
        if r.get("created_at"):
            r["created_at"] = str(r["created_at"])
    return jsonify(results)


def _customer_to_json(c):
    if not c:
        return None
    out = dict(c)
    ca = out.get("created_at")
    if hasattr(ca, "isoformat"):
        out["created_at"] = ca.isoformat()
    return out


@app.route("/api/customers/search")
def api_customers_search():
    q = request.args.get("q", "")
    if len(q) < 1:
        return jsonify([])
    results = excel_db.search_customers(q)
    return jsonify([_customer_to_json(c) for c in results])


@app.route("/api/customers", methods=["POST"])
def api_customers_create():
    data = request.get_json(silent=True) or {}
    name = (data.get("name") or "").strip()
    if not name:
        return jsonify({"error": "Name is required"}), 400
    cid = excel_db.add_customer({
        "name": name,
        "phone": data.get("phone", ""),
        "email": data.get("email", ""),
        "address": data.get("address", ""),
        "tax_id": data.get("tax_id", ""),
        "notes": data.get("notes", ""),
    })
    c = excel_db.get_customer(cid)
    return jsonify({"customer": _customer_to_json(c)})


@app.route("/credit-ledger")
def credit_ledger():
    balances = excel_db.get_all_credit_balances()
    total_outstanding = round(sum(b["balance"] for b in balances if b["balance"] > 0), 2)
    return render_template("credit_ledger.html",
                           balances=balances,
                           total_outstanding=total_outstanding)


@app.route("/credit-ledger/<int:customer_id>/pay", methods=["POST"])
def record_credit_payment(customer_id):
    amount_str = request.form.get("amount", "").strip()
    note = request.form.get("note", "").strip()
    try:
        amount = float(amount_str)
    except (ValueError, TypeError):
        flash("Invalid amount.", "danger")
        return redirect(url_for("credit_ledger"))
    try:
        excel_db.add_ledger_payment(customer_id, amount, note)
        flash(f"Payment of {CURRENCY} {amount:.2f} recorded.", "success")
    except ValueError as e:
        flash(str(e), "danger")
    return redirect(request.referrer or url_for("credit_ledger"))


@app.route("/products/import", methods=["GET", "POST"])
def product_import():
    if request.method == "POST":
        if "file" not in request.files:
            flash("No file selected.", "danger")
            return redirect(url_for("product_import"))
        f = request.files["file"]
        if f.filename == "":
            flash("No file selected.", "danger")
            return redirect(url_for("product_import"))
        if not f.filename.endswith((".xlsx", ".xls")):
            flash("Please upload an Excel file (.xlsx).", "danger")
            return redirect(url_for("product_import"))
        import tempfile, os
        tmp = tempfile.NamedTemporaryFile(delete=False, suffix=".xlsx")
        f.save(tmp.name)
        tmp.close()
        try:
            imported, skipped, errors = excel_db.import_from_excel(tmp.name)
            flash(f"Imported {imported} products, skipped {skipped}.", "success")
            if errors:
                flash(f"Errors: {'; '.join(errors[:5])}", "warning")
        except Exception as e:
            flash(f"Import failed: {e}", "danger")
        finally:
            os.unlink(tmp.name)
        return redirect(url_for("products"))
    return render_template("product_import.html")


if __name__ == "__main__":
    app.run(debug=True, port=5000)
