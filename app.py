"""Flask POS Application - Point of Sale System."""

import os
from datetime import date
from functools import wraps
from dotenv import load_dotenv

load_dotenv()

from flask import (
    Flask, render_template, request, redirect, url_for,
    flash, jsonify,
)
from flask_login import (
    LoginManager, UserMixin, login_user, logout_user,
    login_required, current_user,
)
from werkzeug.security import generate_password_hash, check_password_hash

import db as excel_db
import tenant

app = Flask(__name__)
app.secret_key = os.environ.get("SECRET_KEY", os.urandom(24).hex())

# Configuration
TAX_RATE = float(os.environ.get("TAX_RATE", "0.0"))
LOW_STOCK_THRESHOLD = int(os.environ.get("LOW_STOCK_THRESHOLD", "10"))
EXPIRY_WARNING_DAYS = int(os.environ.get("EXPIRY_WARNING_DAYS", "30"))
BUSINESS_NAME = os.environ.get("BUSINESS_NAME", "My Pharmacy")
BUSINESS_ADDRESS = os.environ.get("BUSINESS_ADDRESS", "123 Main Street")
BUSINESS_PHONE = os.environ.get("BUSINESS_PHONE", "+1 000 000 0000")
CURRENCY = os.environ.get("CURRENCY", "USD")
RECEIPT_FOOTER = os.environ.get("RECEIPT_FOOTER", "")

excel_db.init_workbook()

# ── Auth ──────────────────────────────────────────────────────────────

login_manager = LoginManager(app)
login_manager.login_view = "login"


class User(UserMixin):
    def __init__(self, data):
        self.id = str(data["user_id"])
        self.username = data["username"]
        self.role = data["role"]
        tid = data.get("tenant_id")
        self.tenant_id = int(tid) if tid not in (None, "") else None
        self._is_active = bool(data.get("is_active", True))

    @property
    def is_active(self):
        return self._is_active

    @property
    def is_super_admin(self):
        return self.role == "super_admin"


@login_manager.user_loader
def load_user(user_id):
    u = excel_db.get_user_by_id(int(user_id))
    return User(u) if u else None


def admin_required(f):
    @wraps(f)
    def decorated(*args, **kwargs):
        if not current_user.is_authenticated or current_user.role not in ("admin", "super_admin"):
            flash("Admin access required.", "danger")
            return redirect(url_for("dashboard"))
        return f(*args, **kwargs)
    return decorated


def super_admin_required(f):
    @wraps(f)
    def decorated(*args, **kwargs):
        if not current_user.is_authenticated or not current_user.is_super_admin:
            flash("Platform admin access required.", "danger")
            return redirect(url_for("dashboard"))
        return f(*args, **kwargs)
    return decorated


def _user_in_my_tenant(user_id):
    """True if the target user belongs to the current admin's business."""
    target = excel_db.get_user_by_id(user_id)
    tid = target.get("tenant_id") if target else None
    tid = int(tid) if tid not in (None, "") else None
    return target is not None and tid == current_user.tenant_id


_PUBLIC_ENDPOINTS = {"login", "setup", "static"}
# Endpoints a super-admin (no tenant) is allowed to use.
_SUPERADMIN_ENDPOINTS = {"accounts", "account_create", "account_toggle",
                         "account_add_user", "account_reset_password", "logout"}


@app.before_request
def auth_gate():
    if request.endpoint in _PUBLIC_ENDPOINTS:
        return
    if not excel_db.has_any_super_admin():
        return redirect(url_for("setup"))
    if not current_user.is_authenticated:
        return redirect(url_for("login"))
    # Scope every downstream data call to this user's tenant.
    tenant.set_current_tenant(current_user.tenant_id)
    # Super-admin has no shop data — confine them to platform screens.
    if current_user.is_super_admin:
        if request.endpoint not in _SUPERADMIN_ENDPOINTS:
            return redirect(url_for("accounts"))
    elif current_user.tenant_id is None:
        # A non-super-admin with no tenant is misconfigured; block.
        logout_user()
        flash("Your account is not linked to a business. Contact the administrator.", "danger")
        return redirect(url_for("login"))


@app.route("/setup", methods=["GET", "POST"])
def setup():
    """First-run: create the platform super-admin (who then provisions accounts)."""
    if excel_db.has_any_super_admin():
        return redirect(url_for("login"))
    if request.method == "POST":
        username = request.form.get("username", "").strip()
        password = request.form.get("password", "")
        confirm = request.form.get("confirm", "")
        if not username or not password:
            flash("Username and password are required.", "danger")
            return render_template("setup.html")
        if password != confirm:
            flash("Passwords do not match.", "danger")
            return render_template("setup.html")
        if len(password) < 6:
            flash("Password must be at least 6 characters.", "danger")
            return render_template("setup.html")
        uid = excel_db.add_user(username, generate_password_hash(password),
                                role="super_admin", tenant_id=None)
        u = excel_db.get_user_by_id(uid)
        login_user(User(u))
        flash(f"Welcome, {username}! You are the platform admin. Create a business to begin.",
              "success")
        return redirect(url_for("accounts"))
    return render_template("setup.html")


@app.route("/login", methods=["GET", "POST"])
def login():
    if current_user.is_authenticated:
        return redirect(url_for("dashboard"))
    if request.method == "POST":
        username = request.form.get("username", "").strip()
        password = request.form.get("password", "")
        u = excel_db.get_user_by_username(username)
        if u and u.get("is_active") and check_password_hash(u["password_hash"], password):
            user = User(u)
            login_user(user)
            if user.is_super_admin:
                return redirect(url_for("accounts"))
            return redirect(request.args.get("next") or url_for("dashboard"))
        flash("Invalid username or password.", "danger")
    return render_template("login.html")


@app.route("/logout")
def logout():
    logout_user()
    return redirect(url_for("login"))


# ── Platform: business accounts (super-admin only) ───────────────────

@app.route("/accounts")
@login_required
@super_admin_required
def accounts():
    tenants = excel_db.get_all_tenants()
    for t in tenants:
        t["users"] = excel_db.get_all_users(tenant_id=t["tenant_id"])
    return render_template("accounts.html", tenants=tenants)


@app.route("/accounts/create", methods=["POST"])
@login_required
@super_admin_required
def account_create():
    name = request.form.get("name", "").strip()
    admin_user = request.form.get("admin_username", "").strip()
    admin_pass = request.form.get("admin_password", "")
    if not name or not admin_user or not admin_pass:
        flash("Business name, admin username and password are all required.", "danger")
        return redirect(url_for("accounts"))
    if len(admin_pass) < 6:
        flash("Admin password must be at least 6 characters.", "danger")
        return redirect(url_for("accounts"))
    if excel_db.get_user_by_username(admin_user):
        flash("That admin username is already taken.", "danger")
        return redirect(url_for("accounts"))

    tid = excel_db.create_tenant(name)
    # Initialise the new business's data store and seed its own chart of accounts.
    tenant.set_current_tenant(tid)
    try:
        excel_db.init_workbook()
        excel_db.seed_chart_of_accounts()
    finally:
        tenant.set_current_tenant(current_user.tenant_id)
    excel_db.add_user(admin_user, generate_password_hash(admin_pass),
                      role="admin", tenant_id=tid)
    flash(f"Business '{name}' created with admin login '{admin_user}'.", "success")
    return redirect(url_for("accounts"))


@app.route("/accounts/<int:tenant_id>/toggle", methods=["POST"])
@login_required
@super_admin_required
def account_toggle(tenant_id):
    t = excel_db.get_tenant(tenant_id)
    if t:
        excel_db.set_tenant_active(tenant_id, not t["is_active"])
        flash(f"Business '{t['name']}' {'disabled' if t['is_active'] else 'enabled'}.", "success")
    return redirect(url_for("accounts"))


@app.route("/accounts/<int:tenant_id>/add-user", methods=["POST"])
@login_required
@super_admin_required
def account_add_user(tenant_id):
    t = excel_db.get_tenant(tenant_id)
    if not t:
        flash("Business not found.", "danger")
        return redirect(url_for("accounts"))
    username = request.form.get("username", "").strip()
    password = request.form.get("password", "")
    role = request.form.get("role", "admin")
    role = role if role in ("admin", "staff") else "admin"
    if not username or len(password) < 6:
        flash("Username and a password of at least 6 characters are required.", "danger")
        return redirect(url_for("accounts"))
    if excel_db.get_user_by_username(username):
        flash("That username is already taken.", "danger")
        return redirect(url_for("accounts"))
    excel_db.add_user(username, generate_password_hash(password), role=role, tenant_id=tenant_id)
    flash(f"Login '{username}' added to {t['name']}.", "success")
    return redirect(url_for("accounts"))


@app.route("/accounts/users/<int:user_id>/reset-password", methods=["POST"])
@login_required
@super_admin_required
def account_reset_password(user_id):
    target = excel_db.get_user_by_id(user_id)
    if not target or target.get("role") == "super_admin":
        flash("User not found.", "danger")
        return redirect(url_for("accounts"))
    password = request.form.get("password", "")
    if len(password) < 6:
        flash("Password must be at least 6 characters.", "danger")
        return redirect(url_for("accounts"))
    excel_db.update_user_password(user_id, generate_password_hash(password))
    flash(f"Password reset for '{target['username']}'.", "success")
    return redirect(url_for("accounts"))


@app.route("/users")
@login_required
@admin_required
def users_list():
    users = excel_db.get_all_users(tenant_id=current_user.tenant_id)
    return render_template("users.html", users=users)


@app.route("/users/add", methods=["POST"])
@login_required
@admin_required
def user_add():
    username = request.form.get("username", "").strip()
    password = request.form.get("password", "")
    role = request.form.get("role", "staff")
    if not username or not password:
        flash("Username and password are required.", "danger")
        return redirect(url_for("users_list"))
    if len(password) < 6:
        flash("Password must be at least 6 characters.", "danger")
        return redirect(url_for("users_list"))
    if excel_db.get_user_by_username(username):
        flash("Username already exists.", "danger")
        return redirect(url_for("users_list"))
    role = role if role in ("admin", "staff") else "staff"
    excel_db.add_user(username, generate_password_hash(password), role=role,
                      tenant_id=current_user.tenant_id)
    flash(f"User '{username}' added.", "success")
    return redirect(url_for("users_list"))


@app.route("/users/<int:user_id>/password", methods=["POST"])
@login_required
@admin_required
def user_change_password(user_id):
    if not _user_in_my_tenant(user_id):
        flash("User not found.", "danger")
        return redirect(url_for("users_list"))
    password = request.form.get("password", "")
    if len(password) < 6:
        flash("Password must be at least 6 characters.", "danger")
        return redirect(url_for("users_list"))
    excel_db.update_user_password(user_id, generate_password_hash(password))
    flash("Password updated.", "success")
    return redirect(url_for("users_list"))


@app.route("/users/<int:user_id>/role", methods=["POST"])
@login_required
@admin_required
def user_change_role(user_id):
    if not _user_in_my_tenant(user_id):
        flash("User not found.", "danger")
        return redirect(url_for("users_list"))
    if str(user_id) == current_user.id:
        flash("Cannot change your own role.", "danger")
        return redirect(url_for("users_list"))
    role = request.form.get("role", "staff")
    role = role if role in ("admin", "staff") else "staff"
    excel_db.set_user_role(user_id, role)
    flash("Role updated.", "success")
    return redirect(url_for("users_list"))


@app.route("/users/<int:user_id>/toggle", methods=["POST"])
@login_required
@admin_required
def user_toggle(user_id):
    if not _user_in_my_tenant(user_id):
        flash("User not found.", "danger")
        return redirect(url_for("users_list"))
    if str(user_id) == current_user.id:
        flash("Cannot deactivate your own account.", "danger")
        return redirect(url_for("users_list"))
    excel_db.toggle_user_active(user_id)
    return redirect(url_for("users_list"))


@app.route("/users/<int:user_id>/delete", methods=["POST"])
@login_required
@admin_required
def user_delete(user_id):
    if not _user_in_my_tenant(user_id):
        flash("User not found.", "danger")
        return redirect(url_for("users_list"))
    if str(user_id) == current_user.id:
        flash("Cannot delete your own account.", "danger")
        return redirect(url_for("users_list"))
    excel_db.delete_user(user_id)
    flash("User deleted.", "success")
    return redirect(url_for("users_list"))


# ── Globals ───────────────────────────────────────────────────────────

@app.context_processor
def inject_globals():
    return {
        "business_name": BUSINESS_NAME,
        "currency": CURRENCY,
        "today": date.today(),
    }


# ── Dashboard ────────────────────────────────────────────────────────

@app.route("/")
@login_required
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
@login_required
def products():
    all_products = excel_db.get_all_products()
    return render_template("products.html",
                           products=all_products,
                           threshold=LOW_STOCK_THRESHOLD)


@app.route("/products/add", methods=["GET", "POST"])
@login_required
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
@login_required
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
@login_required
def product_delete(product_id):
    excel_db.delete_product(product_id)
    flash("Product deleted.", "success")
    return redirect(url_for("products"))


# ── Reports ──────────────────────────────────────────────────────────

@app.route("/stock-report")
@login_required
def stock_report():
    products = excel_db.get_all_products()
    products.sort(key=lambda p: int(p["quantity"]))
    return render_template("stock_report.html",
                           products=products,
                           threshold=LOW_STOCK_THRESHOLD)


@app.route("/expiry-report")
@login_required
def expiry_report():
    products = excel_db.get_expiry_products(EXPIRY_WARNING_DAYS)
    return render_template("expiry_report.html", products=products)


# ── Invoices ─────────────────────────────────────────────────────────

def _attach_customer(invoice, cmap):
    cid = excel_db.normalize_customer_id(invoice.get("customer_id"))
    invoice["customer"] = cmap.get(cid) if cid is not None else None


@app.route("/invoices")
@login_required
def invoices():
    cmap = excel_db.customer_lookup()
    all_invoices = []
    for inv in excel_db.get_all_invoices():
        inv = dict(inv)
        _attach_customer(inv, cmap)
        all_invoices.append(inv)
    return render_template("invoices.html", invoices=all_invoices)


@app.route("/invoices/create", methods=["GET", "POST"])
@login_required
def invoice_create():
    if request.method == "POST":
        data = request.get_json()
        items = data.get("items", [])
        payment_method = data.get("payment_method", "Cash")
        if not items:
            return jsonify({"error": "No items in invoice"}), 400
        try:
            invoice_id = excel_db.create_invoice(
                items, TAX_RATE, payment_method,
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
@login_required
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


@app.route("/invoices/<int:invoice_id>/delete", methods=["POST"])
@login_required
def invoice_delete(invoice_id):
    password = request.form.get("password", "").strip()
    reason = request.form.get("reason", "").strip()
    u = excel_db.get_user_by_username(current_user.username)
    if not u or not check_password_hash(u["password_hash"], password):
        flash("Incorrect password. Receipt not deleted.", "danger")
        return redirect(url_for("invoice_detail", invoice_id=invoice_id))
    try:
        excel_db.delete_invoice(invoice_id, current_user.username, reason)
        flash(f"Receipt #{invoice_id} deleted and stock reversed.", "success")
    except ValueError as e:
        flash(str(e), "danger")
    return redirect(url_for("invoices"))


@app.route("/invoices/deleted")
@login_required
def deleted_invoices():
    invoices = excel_db.get_deleted_invoices()
    cmap = excel_db.customer_lookup()
    for inv in invoices:
        _attach_customer(inv, cmap)
    return render_template("deleted_invoices.html", invoices=invoices)


@app.route("/quotation/preview", methods=["POST"])
@login_required
def quotation_preview():
    import json
    raw = request.form.get("data") or ""
    try:
        data = json.loads(raw)
    except (ValueError, TypeError):
        return "Invalid quotation data.", 400

    cart_items = data.get("items", [])
    if not cart_items:
        return "No items in quotation.", 400

    customer = None
    cid = excel_db.normalize_customer_id(data.get("customer_id"))
    if cid is not None:
        cmap = excel_db.customer_lookup()
        customer = cmap.get(cid)

    line_items = []
    subtotal = 0.0
    discount_total = 0.0

    for item in cart_items:
        pid = int(item["product_id"])
        qty = int(item["quantity"])
        discount_per_unit = float(item.get("discount_amount", 0))
        product = excel_db.get_product(pid)
        if not product:
            return f"Product {pid} not found.", 400
        unit_price = float(item.get("unit_price") or product["counter_price"])
        line_discount = discount_per_unit * qty
        line_total = (unit_price - discount_per_unit) * qty
        line_items.append({
            "product_name": product["name"],
            "quantity": qty,
            "counter_price": unit_price,
            "discount_amount": line_discount,
            "line_total": round(line_total, 2),
        })
        subtotal += unit_price * qty
        discount_total += line_discount

    net = subtotal - discount_total
    tax_amount = round(net * TAX_RATE, 2)
    total = round(net + tax_amount, 2)

    from datetime import datetime as _dt
    return render_template(
        "quotation_receipt.html",
        items=line_items,
        subtotal=round(subtotal, 2),
        discount_total=round(discount_total, 2),
        tax_rate=TAX_RATE,
        tax_amount=tax_amount,
        total=total,
        customer=customer,
        generated_at=_dt.now(),
        business_name=BUSINESS_NAME,
        business_address=BUSINESS_ADDRESS,
        business_phone=BUSINESS_PHONE,
        receipt_footer=RECEIPT_FOOTER,
    )


@app.route("/invoices/<int:invoice_id>/edit", methods=["GET", "POST"])
@login_required
def invoice_edit(invoice_id):
    invoice = excel_db.get_invoice(invoice_id)
    if not invoice:
        flash("Invoice not found.", "danger")
        return redirect(url_for("invoices"))
    if request.method == "POST":
        data = request.get_json()
        items = data.get("items", [])
        if not items:
            return jsonify({"error": "No items in invoice"}), 400
        try:
            excel_db.update_invoice(
                invoice_id, items, TAX_RATE,
                payment_method=data.get("payment_method"),
                customer_id=data.get("customer_id"),
            )
            return jsonify({"invoice_id": invoice_id})
        except ValueError as e:
            return jsonify({"error": str(e)}), 400
    invoice = dict(invoice)
    cmap = excel_db.customer_lookup()
    _attach_customer(invoice, cmap)
    old_items = excel_db.get_invoice_items(invoice_id)
    stock_adj = {}
    for item in old_items:
        pid = int(item["product_id"])
        stock_adj[pid] = stock_adj.get(pid, 0) + int(item["quantity"])
    products = excel_db.get_all_products()
    for p in products:
        adj = stock_adj.get(int(p["product_id"]), 0)
        p["quantity"] = int(p["quantity"]) + adj
    products = [p for p in products if int(p["quantity"]) > 0]
    return render_template("invoice_edit.html",
                           invoice=invoice,
                           old_items=old_items,
                           products=products,
                           tax_rate=TAX_RATE)


@app.route("/invoices/<int:invoice_id>/receipt")
@login_required
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


# ── Customers ────────────────────────────────────────────────────────

@app.route("/customers")
@login_required
def customers_list():
    customers = excel_db.get_all_customers()
    cmap = excel_db.customer_lookup()
    inv_counts = {cid: 0 for cid in cmap}
    for inv in excel_db.get_all_invoices():
        cid = excel_db.normalize_customer_id(inv.get("customer_id"))
        if cid is not None and cid in inv_counts:
            inv_counts[cid] += 1
    return render_template("customers.html",
                           customers=customers, inv_counts=inv_counts)


@app.route("/customers/add", methods=["GET", "POST"])
@login_required
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
@login_required
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
@login_required
def customer_delete(customer_id):
    try:
        excel_db.delete_customer(customer_id)
        flash("Customer deleted.", "success")
    except ValueError as e:
        flash(str(e), "danger")
    return redirect(url_for("customers_list"))


@app.route("/customers/<int:customer_id>")
@login_required
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
@login_required
def customers_sales_summary():
    rows, walk_in = excel_db.get_sales_summary_by_customer()
    return render_template("customers_sales_summary.html",
                           rows=rows, walk_in=walk_in)


# ── API ───────────────────────────────────────────────────────────────

@app.route("/api/products/search")
@login_required
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
@login_required
def api_customers_search():
    q = request.args.get("q", "")
    if len(q) < 1:
        return jsonify([])
    results = excel_db.search_customers(q)
    return jsonify([_customer_to_json(c) for c in results])


@app.route("/api/customers", methods=["POST"])
@login_required
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


@app.route("/api/suppliers", methods=["POST"])
@login_required
def api_suppliers_create():
    data = request.get_json(silent=True) or {}
    name = (data.get("name") or "").strip()
    if not name:
        return jsonify({"error": "Name is required"}), 400
    sid = excel_db.add_supplier({
        "name": name,
        "phone": data.get("phone", ""),
        "email": data.get("email", ""),
        "address": data.get("address", ""),
        "notes": data.get("notes", ""),
    })
    s = excel_db.get_supplier(sid)
    return jsonify({"supplier": {"supplier_id": s["supplier_id"], "name": s["name"]}})


@app.route("/api/products", methods=["POST"])
@login_required
def api_products_create():
    data = request.get_json(silent=True) or {}
    name = (data.get("name") or "").strip()
    if not name:
        return jsonify({"error": "Name is required"}), 400
    pid = excel_db.add_product({
        "name": name,
        "purchase_price": data.get("purchase_price", 0),
        "counter_price": data.get("counter_price", 0),
        "retail_price": data.get("retail_price", 0),
        "quantity": data.get("quantity", 0),
        "barcode": data.get("barcode", ""),
        "category": data.get("category", ""),
    })
    p = excel_db.get_product(pid)
    return jsonify({"product": {
        "product_id": p["product_id"], "name": p["name"],
        "purchase_price": float(p.get("purchase_price") or 0),
        "counter_price": float(p.get("counter_price") or 0),
        "retail_price": float(p.get("retail_price") or 0),
    }})


# ── Credit Ledger ─────────────────────────────────────────────────────

@app.route("/credit-ledger")
@login_required
def credit_ledger():
    balances = excel_db.get_all_credit_balances()
    total_outstanding = round(sum(b["balance"] for b in balances if b["balance"] > 0), 2)
    return render_template("credit_ledger.html",
                           balances=balances,
                           total_outstanding=total_outstanding)


@app.route("/credit-ledger/<int:customer_id>/pay", methods=["POST"])
@login_required
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


@app.route("/credit-ledger/<int:customer_id>/charge", methods=["POST"])
@login_required
def record_credit_charge(customer_id):
    amount_str = request.form.get("amount", "").strip()
    note = request.form.get("note", "").strip()
    try:
        amount = float(amount_str)
    except (ValueError, TypeError):
        flash("Invalid amount.", "danger")
        return redirect(url_for("credit_ledger"))
    try:
        excel_db.add_ledger_debit(customer_id, amount, note)
        flash(f"Charge of {CURRENCY} {amount:.2f} added to outstanding balance.", "warning")
    except ValueError as e:
        flash(str(e), "danger")
    return redirect(request.referrer or url_for("credit_ledger"))


# ── Product Import ────────────────────────────────────────────────────

@app.route("/products/import", methods=["GET", "POST"])
@login_required
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
        import tempfile, os as _os
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
            _os.unlink(tmp.name)
        return redirect(url_for("products"))
    return render_template("product_import.html")


# ── Suppliers ────────────────────────────────────────────────────────

@app.route("/suppliers")
@login_required
def suppliers_list():
    all_suppliers = excel_db.get_all_suppliers()
    balance_map = {b["supplier_id"]: b for b in excel_db.get_all_supplier_balances()}
    balances = []
    for s in all_suppliers:
        sid = s["supplier_id"]
        b = balance_map.get(sid, {"total_debt": 0.0, "total_paid": 0.0, "balance": 0.0})
        balances.append({
            "supplier_id": sid,
            "supplier": s,
            "total_debt": b["total_debt"],
            "total_paid": b["total_paid"],
            "balance": b["balance"],
        })
    balances.sort(key=lambda x: x["balance"], reverse=True)
    total_owed = round(sum(b["balance"] for b in balances if b["balance"] > 0), 2)
    return render_template("suppliers.html", balances=balances, total_owed=total_owed)


@app.route("/suppliers/add", methods=["GET", "POST"])
@login_required
def supplier_add():
    if request.method == "POST":
        data = {k: request.form.get(k, "") for k in ("name", "phone", "email", "address", "notes")}
        if not data["name"].strip():
            flash("Name is required.", "danger")
            return render_template("supplier_form.html", supplier=None, data=data)
        excel_db.add_supplier(data)
        flash("Supplier added.", "success")
        return redirect(url_for("suppliers_list"))
    return render_template("supplier_form.html", supplier=None, data=None)


@app.route("/suppliers/<int:supplier_id>/edit", methods=["GET", "POST"])
@login_required
def supplier_edit(supplier_id):
    supplier = excel_db.get_supplier(supplier_id)
    if not supplier:
        flash("Supplier not found.", "danger")
        return redirect(url_for("suppliers_list"))
    if request.method == "POST":
        data = {k: request.form.get(k, "") for k in ("name", "phone", "email", "address", "notes")}
        if not data["name"].strip():
            flash("Name is required.", "danger")
            return render_template("supplier_form.html", supplier=supplier, data=data)
        excel_db.update_supplier(supplier_id, data)
        flash("Supplier updated.", "success")
        return redirect(url_for("supplier_detail", supplier_id=supplier_id))
    return render_template("supplier_form.html", supplier=supplier, data=None)


@app.route("/suppliers/<int:supplier_id>/delete", methods=["POST"])
@login_required
@admin_required
def supplier_delete(supplier_id):
    try:
        excel_db.delete_supplier(supplier_id)
        flash("Supplier deleted.", "success")
    except ValueError as e:
        flash(str(e), "danger")
    return redirect(url_for("suppliers_list"))


@app.route("/suppliers/<int:supplier_id>")
@login_required
def supplier_detail(supplier_id):
    supplier = excel_db.get_supplier(supplier_id)
    if not supplier:
        flash("Supplier not found.", "danger")
        return redirect(url_for("suppliers_list"))
    entries = excel_db.get_supplier_ledger_entries(supplier_id)
    total_debt, total_paid, balance = excel_db.get_supplier_balance(supplier_id)
    # Running balance per entry
    running = 0.0
    for e in entries:
        amt = float(e["amount"] or 0)
        if e["type"] == "debit":
            running += amt
        else:
            running -= amt
        e["running_balance"] = round(running, 2)
    return render_template("supplier_detail.html",
                           supplier=supplier,
                           entries=entries,
                           total_debt=total_debt,
                           total_paid=total_paid,
                           balance=balance)


@app.route("/suppliers/<int:supplier_id>/pay", methods=["POST"])
@login_required
def supplier_pay(supplier_id):
    amount_str = request.form.get("amount", "").strip()
    note = request.form.get("note", "").strip()
    try:
        amount = float(amount_str)
    except (ValueError, TypeError):
        flash("Invalid amount.", "danger")
        return redirect(url_for("supplier_detail", supplier_id=supplier_id))
    try:
        excel_db.add_supplier_payment(supplier_id, amount, note)
        flash(f"Payment of {CURRENCY} {amount:.2f} recorded.", "success")
    except ValueError as e:
        flash(str(e), "danger")
    return redirect(url_for("supplier_detail", supplier_id=supplier_id))


# ── Purchase Invoices ─────────────────────────────────────────────────

@app.route("/purchases")
@login_required
def purchases_list():
    purchases = excel_db.get_all_purchase_invoices()
    smap = excel_db.supplier_lookup()
    for p in purchases:
        p["supplier"] = smap.get(int(p["supplier_id"])) if p.get("supplier_id") else None
    return render_template("purchases.html", purchases=purchases)


@app.route("/purchases/create", methods=["GET", "POST"])
@login_required
def purchase_create():
    if request.method == "POST":
        data = request.get_json()
        supplier_id = data.get("supplier_id")
        items = data.get("items", [])
        notes = data.get("notes", "")
        if not supplier_id:
            return jsonify({"error": "Supplier is required"}), 400
        if not items:
            return jsonify({"error": "No items added"}), 400
        try:
            purchase_id = excel_db.create_purchase_invoice(supplier_id, items, notes)
            return jsonify({"purchase_id": purchase_id})
        except ValueError as e:
            return jsonify({"error": str(e)}), 400
    suppliers = excel_db.get_all_suppliers()
    products = excel_db.get_all_products()
    return render_template("purchase_create.html", suppliers=suppliers, products=products)


@app.route("/purchases/<int:purchase_id>")
@login_required
def purchase_detail(purchase_id):
    purchase = excel_db.get_purchase_invoice(purchase_id)
    if not purchase:
        flash("Purchase invoice not found.", "danger")
        return redirect(url_for("purchases_list"))
    supplier = excel_db.get_supplier(int(purchase["supplier_id"])) if purchase.get("supplier_id") else None
    items = excel_db.get_purchase_invoice_items(purchase_id)
    return render_template("purchase_detail.html", purchase=purchase, supplier=supplier, items=items)


# ── P&L Report ────────────────────────────────────────────────────────

@app.route("/pl-report")
@login_required
def pl_report():
    from datetime import datetime as _dt
    today = date.today()
    start_str = request.args.get("start", today.replace(day=1).isoformat())
    end_str = request.args.get("end", today.isoformat())
    supplier_id = request.args.get("supplier_id", "")
    try:
        start_date = date.fromisoformat(start_str)
        end_date = date.fromisoformat(end_str)
    except ValueError:
        start_date = today.replace(day=1)
        end_date = today

    suppliers = excel_db.get_all_suppliers()

    credit_outstanding = 0.0
    if supplier_id:
        supplier = excel_db.get_supplier(int(supplier_id))
        rows, totals = excel_db.get_supplier_sales_pl(int(supplier_id), start_date, end_date)
        report_type = "supplier"
    else:
        supplier = None
        rows, totals = excel_db.get_sales_pl_report(start_date, end_date)
        report_type = "overall"
        balances = excel_db.get_all_credit_balances()
        credit_outstanding = round(sum(b["balance"] for b in balances if b["balance"] > 0), 2)

    return render_template("pl_report.html",
                           rows=rows,
                           totals=totals,
                           suppliers=suppliers,
                           supplier=supplier,
                           supplier_id=supplier_id,
                           start_date=start_date,
                           end_date=end_date,
                           report_type=report_type,
                           credit_outstanding=credit_outstanding)


# ── Accounting: Expenses ─────────────────────────────────────────────

@app.route("/accounting/expenses", methods=["GET", "POST"])
@login_required
def expenses():
    # Categories = expense accounts, excluding auto-posted COGS (5000)
    expense_accounts = [a for a in excel_db.get_accounts_by_type("expense")
                        if str(a["code"]) != "5000"]
    # Paid-from = Cash / Bank
    pay_accounts = [a for a in excel_db.get_accounts_by_type("asset")
                    if str(a["code"]) in ("1000", "1010")]

    if request.method == "POST":
        try:
            excel_db.record_expense(
                int(request.form["expense_account_id"]),
                request.form.get("amount", ""),
                int(request.form["paid_from_account_id"]),
                request.form.get("description", ""),
                created_by=current_user.username,
            )
            flash("Expense recorded.", "success")
        except (ValueError, KeyError) as e:
            flash(str(e) or "Invalid expense.", "danger")
        return redirect(url_for("expenses"))

    today = date.today()
    start_str = request.args.get("start", today.replace(day=1).isoformat())
    end_str = request.args.get("end", today.isoformat())
    try:
        start_date = date.fromisoformat(start_str)
        end_date = date.fromisoformat(end_str)
    except ValueError:
        start_date = today.replace(day=1)
        end_date = today

    items = excel_db.get_expense_entries(start_date, end_date)
    total = round(sum(e["amount"] for e in items), 2)
    return render_template("expenses.html",
                           expense_accounts=expense_accounts,
                           pay_accounts=pay_accounts,
                           items=items, total=total,
                           start_date=start_date, end_date=end_date)


def _as_date(val):
    """Coerce a stored created_at (datetime/date/str) to a date, or None."""
    from datetime import datetime as _dt
    if val is None or val == "":
        return None
    if isinstance(val, _dt):
        return val.date()
    if isinstance(val, date):
        return val
    try:
        return _dt.fromisoformat(str(val)[:19]).date()
    except ValueError:
        return None


@app.route("/accounting/cash-flow")
@login_required
def cash_flow():
    today = date.today()
    start_str = request.args.get("start", today.replace(day=1).isoformat())
    end_str = request.args.get("end", today.isoformat())
    try:
        start_date = date.fromisoformat(start_str)
        end_date = date.fromisoformat(end_str)
    except ValueError:
        start_date = today.replace(day=1)
        end_date = today

    def in_range(d):
        return d is not None and start_date <= d <= end_date

    # Cash in — sales paid at point of sale (non-credit), excludes deleted
    sales_cash = 0.0
    for inv in excel_db.get_all_invoices():
        if str(inv.get("payment_method") or "").strip().lower() == "credit":
            continue
        if in_range(_as_date(inv.get("created_at"))):
            sales_cash += float(inv.get("total") or 0)
    sales_cash = round(sales_cash, 2)

    # Cash in — customer credit repayments
    customer_payments = 0.0
    for e in excel_db.get_credit_ledger():
        if e.get("type") == "credit" and in_range(_as_date(e.get("created_at"))):
            customer_payments += float(e.get("amount") or 0)
    customer_payments = round(customer_payments, 2)

    # Cash out — payments to suppliers
    supplier_payments = 0.0
    for e in excel_db.get_supplier_ledger_entries():
        if e.get("type") == "credit" and in_range(_as_date(e.get("created_at"))):
            supplier_payments += float(e.get("amount") or 0)
    supplier_payments = round(supplier_payments, 2)

    # Cash out — operating expenses
    expenses_total = round(sum(e["amount"] for e in
                              excel_db.get_expense_entries(start_date, end_date)), 2)

    cash_in = round(sales_cash + customer_payments, 2)
    cash_out = round(supplier_payments + expenses_total, 2)
    net = round(cash_in - cash_out, 2)

    return render_template("cash_flow.html",
                           start_date=start_date, end_date=end_date,
                           sales_cash=sales_cash, customer_payments=customer_payments,
                           supplier_payments=supplier_payments, expenses_total=expenses_total,
                           cash_in=cash_in, cash_out=cash_out, net=net)


@app.route("/inventory/valuation")
@login_required
def inventory_valuation():
    products = excel_db.get_all_products()
    rows = []
    total_value = 0.0
    total_units = 0
    for p in products:
        qty = int(p.get("quantity") or 0)
        cost = float(p.get("purchase_price") or 0)
        value = round(qty * cost, 2)
        if qty == 0 and value == 0:
            continue
        rows.append({"name": p.get("name"), "quantity": qty,
                     "cost": cost, "value": value,
                     "zero_cost": cost == 0 and qty > 0})
        total_value += value
        total_units += qty
    rows.sort(key=lambda r: r["value"], reverse=True)
    zero_cost_count = sum(1 for r in rows if r["zero_cost"])
    return render_template("inventory_valuation.html", rows=rows,
                           total_value=round(total_value, 2),
                           total_units=total_units,
                           zero_cost_count=zero_cost_count)


@app.route("/accounting/partners")
@login_required
def partners():
    excel_db.sync_journal_from_operations()
    rows, totals = excel_db.get_partner_equity()
    bs = excel_db.get_balance_sheet()
    unallocated = round(bs["total_equity"] - totals["equity"], 2)
    return render_template("partners.html", rows=rows, totals=totals,
                           unallocated=unallocated, bs_equity=bs["total_equity"],
                           today_iso=date.today().isoformat())


@app.route("/accounting/partners/add", methods=["POST"])
@login_required
def partner_add():
    try:
        excel_db.add_partner(request.form.get("name", ""),
                             request.form.get("share_pct", 0))
        flash("Partner added.", "success")
    except ValueError as e:
        flash(str(e), "danger")
    return redirect(url_for("partners"))


@app.route("/accounting/partners/<int:partner_id>/edit", methods=["POST"])
@login_required
def partner_edit(partner_id):
    try:
        excel_db.update_partner(partner_id, request.form.get("name", ""),
                                request.form.get("share_pct", 0),
                                is_active=bool(request.form.get("is_active")))
        flash("Partner updated.", "success")
    except ValueError as e:
        flash(str(e), "danger")
    return redirect(url_for("partners"))


@app.route("/accounting/partners/<int:partner_id>/txn", methods=["POST"])
@login_required
def partner_txn(partner_id):
    txn_type = request.form.get("type", "")
    try:
        excel_db.add_partner_transaction(
            partner_id, txn_type, request.form.get("amount", 0),
            note=request.form.get("note", ""),
            txn_date=request.form.get("date") or None,
            created_by=current_user.username)
        flash(f"{'Capital' if txn_type == 'capital' else 'Drawing'} recorded.", "success")
    except ValueError as e:
        flash(str(e), "danger")
    return redirect(url_for("partners"))


@app.route("/accounting/opening-balances", methods=["GET", "POST"])
@login_required
def opening_balances():
    if request.method == "POST":
        start = request.form.get("start_date", "").strip()
        try:
            start_date = date.fromisoformat(start)
        except ValueError:
            flash("Enter a valid start date.", "danger")
            return redirect(url_for("opening_balances"))

        def amt(field):
            try:
                return float(request.form.get(field, "") or 0)
            except ValueError:
                return 0.0

        balances = {
            "1000": amt("cash"),
            "1010": amt("bank"),
            "1200": amt("inventory"),
            "1100": amt("receivable"),
            "2000": amt("payable"),
        }
        try:
            excel_db.set_opening_balances(start_date, balances,
                                          created_by=current_user.username)
            flash("Opening balances saved. The Balance Sheet now starts from "
                  f"{start_date.isoformat()}.", "success")
        except ValueError as e:
            flash(str(e), "danger")
        return redirect(url_for("balance_sheet"))

    existing = excel_db.get_opening_balances()
    books_start = excel_db.get_books_start()
    return render_template("opening_balances.html",
                           existing=existing, books_start=books_start)


@app.route("/accounting/balance-sheet")
@login_required
def balance_sheet():
    # Bring the ledger up to date with any new sales/purchases/payments
    try:
        excel_db.sync_journal_from_operations()
    except Exception as e:
        app.logger.warning("journal sync failed: %s", e)
    bs = excel_db.get_balance_sheet()
    books_start = excel_db.get_books_start()
    return render_template("balance_sheet.html", bs=bs, books_start=books_start,
                           as_of=date.today())


@app.route("/accounting/categories/add", methods=["POST"])
@login_required
def category_add():
    name = request.form.get("name", "").strip()
    if not name:
        flash("Category name is required.", "danger")
        return redirect(url_for("expenses"))
    try:
        excel_db.add_account(excel_db.next_expense_code(), name, "expense", is_system=False)
        flash(f"Category '{name}' added.", "success")
    except ValueError as e:
        flash(str(e), "danger")
    return redirect(url_for("expenses"))


@app.route("/accounting/categories/<int:account_id>/rename", methods=["POST"])
@login_required
def category_rename(account_id):
    name = request.form.get("name", "").strip()
    try:
        excel_db.update_account_name(account_id, name)
        flash("Category renamed.", "success")
    except ValueError as e:
        flash(str(e), "danger")
    return redirect(url_for("expenses"))


@app.route("/accounting/income-statement")
@login_required
def income_statement():
    today = date.today()
    start_str = request.args.get("start", today.replace(day=1).isoformat())
    end_str = request.args.get("end", today.isoformat())
    try:
        start_date = date.fromisoformat(start_str)
        end_date = date.fromisoformat(end_str)
    except ValueError:
        start_date = today.replace(day=1)
        end_date = today

    # Trading (sales) section from invoices
    _, sales_totals = excel_db.get_sales_pl_report(start_date, end_date)
    revenue = sales_totals["revenue"]
    cogs = sales_totals["cogs"]
    gross_profit = sales_totals["profit"]

    # Operating expenses from the ledger, grouped by category
    exp_entries = excel_db.get_expense_entries(start_date, end_date)
    by_cat = {}
    for e in exp_entries:
        name = e["category"]["name"] if e["category"] else "Uncategorised"
        by_cat[name] = round(by_cat.get(name, 0.0) + e["amount"], 2)
    expense_rows = sorted(by_cat.items(), key=lambda x: x[1], reverse=True)
    total_expenses = round(sum(by_cat.values()), 2)

    net_profit = round(gross_profit - total_expenses, 2)

    return render_template("income_statement.html",
                           start_date=start_date, end_date=end_date,
                           revenue=revenue, cogs=cogs, gross_profit=gross_profit,
                           expense_rows=expense_rows, total_expenses=total_expenses,
                           net_profit=net_profit)


if __name__ == "__main__":
    app.run(debug=True, port=5000)
