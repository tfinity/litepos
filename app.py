"""Flask POS Application - Excel-based Point of Sale System."""

from datetime import date

from flask import (
    Flask, render_template, request, redirect, url_for,
    flash, jsonify,
)

import excel_db

app = Flask(__name__)
app.secret_key = "pos-system-secret-key-change-in-production"

# Configuration
TAX_RATE = 0.0   # 0% tax (adjust if needed)
LOW_STOCK_THRESHOLD = 10
EXPIRY_WARNING_DAYS = 30
BUSINESS_NAME = "ANIMAL NEXUS PHARMACY"
BUSINESS_ADDRESS = "KOT ARIAN STOP JIA BAGGA, ROAD, Lahore, 54000"
BUSINESS_PHONE = "+923299406159"
CURRENCY = "PKR"

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
    return render_template("dashboard.html",
                           total_products=len(products),
                           low_stock_count=len(low_stock),
                           expiring_count=len(expiring),
                           sales_count=sales_count,
                           sales_total=sales_total)


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

@app.route("/invoices")
def invoices():
    all_invoices = excel_db.get_all_invoices()
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
                items, TAX_RATE, payment_method
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
    items = excel_db.get_invoice_items(invoice_id)
    return render_template("invoice_detail.html",
                           invoice=invoice, items=items)


@app.route("/invoices/<int:invoice_id>/receipt")
def invoice_receipt(invoice_id):
    invoice = excel_db.get_invoice(invoice_id)
    if not invoice:
        flash("Invoice not found.", "danger")
        return redirect(url_for("invoices"))
    items = excel_db.get_invoice_items(invoice_id)
    return render_template("receipt.html",
                           invoice=invoice, items=items,
                           business_name=BUSINESS_NAME,
                           business_address=BUSINESS_ADDRESS,
                           business_phone=BUSINESS_PHONE)


# ── API Endpoints ────────────────────────────────────────────────────

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
