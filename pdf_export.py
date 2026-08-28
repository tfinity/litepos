"""A4 PDF generation for receipts and quotations, built directly with fpdf2's
drawing API rather than an HTML/CSS renderer -- avoids heavy, fragile
transitive dependency chains (e.g. xhtml2pdf pulls in pyHanko -> cryptography,
a compiled Rust extension that isn't guaranteed to have a prebuilt wheel on
every deploy target). fpdf2 is pure-Python and installs anywhere.
"""

from fpdf import FPDF

_MARGIN = 18
_PAGE_W = 210  # A4, mm
_USABLE_W = _PAGE_W - 2 * _MARGIN

_COL_QTY_W = 20
_COL_PRICE_W = 32
_COL_TOTAL_W = 32
_COL_NAME_W = _USABLE_W - _COL_QTY_W - _COL_PRICE_W - _COL_TOTAL_W


def _new_pdf():
    pdf = FPDF(format="A4", unit="mm")
    pdf.set_margins(_MARGIN, _MARGIN, _MARGIN)
    pdf.set_auto_page_break(auto=True, margin=_MARGIN)
    pdf.add_page()
    return pdf


def _header(pdf, business_name, business_address, business_phone, title, meta_lines):
    left_w = _USABLE_W * 0.6
    right_w = _USABLE_W - left_w
    y0 = pdf.get_y()

    pdf.set_xy(_MARGIN, y0)
    pdf.set_font("Helvetica", "B", 16)
    pdf.multi_cell(left_w, 7, business_name)
    pdf.set_font("Helvetica", "", 9)
    pdf.set_text_color(90, 90, 90)
    for line in (business_address, f"Tel: {business_phone}"):
        if not line.strip():
            continue
        pdf.set_x(_MARGIN)
        pdf.multi_cell(left_w, 5, line)
    pdf.set_text_color(0, 0, 0)
    left_bottom = pdf.get_y()

    pdf.set_xy(_MARGIN + left_w, y0)
    pdf.set_font("Helvetica", "B", 18)
    pdf.cell(right_w, 8, title, align="R", new_x="LMARGIN", new_y="NEXT")
    pdf.set_font("Helvetica", "", 9)
    pdf.set_text_color(90, 90, 90)
    for line in meta_lines:
        pdf.set_x(_MARGIN + left_w)
        pdf.cell(right_w, 5, line, align="R", new_x="LMARGIN", new_y="NEXT")
    pdf.set_text_color(0, 0, 0)
    right_bottom = pdf.get_y()

    pdf.set_y(max(left_bottom, right_bottom) + 3)
    pdf.set_draw_color(50, 50, 50)
    pdf.set_line_width(0.6)
    y = pdf.get_y()
    pdf.line(_MARGIN, y, _PAGE_W - _MARGIN, y)
    pdf.ln(6)


def _bill_to(pdf, label, name, phone, extra_right=None):
    left_w = _USABLE_W * 0.6
    y0 = pdf.get_y()
    pdf.set_font("Helvetica", "B", 9)
    pdf.cell(left_w, 6, label, new_x="LMARGIN", new_y="NEXT")
    pdf.set_font("Helvetica", "", 10)
    pdf.cell(left_w, 6, name, new_x="LMARGIN", new_y="NEXT")
    if phone:
        pdf.set_font("Helvetica", "", 9)
        pdf.cell(left_w, 6, f"Tel: {phone}", new_x="LMARGIN", new_y="NEXT")
    left_bottom = pdf.get_y()

    right_bottom = left_bottom
    if extra_right:
        pdf.set_xy(_MARGIN + left_w, y0)
        pdf.set_font("Helvetica", "", 9)
        right_w = _USABLE_W - left_w
        pdf.multi_cell(right_w, 5, extra_right, align="R")
        right_bottom = pdf.get_y()

    pdf.set_y(max(left_bottom, right_bottom) + 4)


def _items_table(pdf, items):
    """items: iterable of dicts with product_name, quantity, counter_price,
    discount_amount, line_total (same shape used by the HTML receipts)."""
    pdf.set_font("Helvetica", "B", 9)
    pdf.set_fill_color(242, 242, 242)
    x0 = _MARGIN
    y0 = pdf.get_y()
    pdf.set_xy(x0, y0)
    pdf.cell(_COL_NAME_W, 7, "Item", border=0, fill=True)
    pdf.cell(_COL_QTY_W, 7, "Qty", border=0, fill=True, align="C")
    pdf.cell(_COL_PRICE_W, 7, "Price", border=0, fill=True, align="R")
    pdf.cell(_COL_TOTAL_W, 7, "Total", border=0, fill=True, align="R", new_x="LMARGIN", new_y="NEXT")
    pdf.set_draw_color(200, 200, 200)
    pdf.line(x0, pdf.get_y(), x0 + _USABLE_W, pdf.get_y())

    pdf.set_font("Helvetica", "", 9.5)
    for item in items:
        name = str(item["product_name"])
        qty = item["quantity"]
        price = item["counter_price"]
        line_total = item["line_total"]
        discount = item.get("discount_amount") or 0

        row_y = pdf.get_y()
        pdf.set_xy(x0, row_y)
        pdf.multi_cell(_COL_NAME_W, 5, name)
        if discount:
            pdf.set_x(x0)
            pdf.set_font("Helvetica", "", 8)
            pdf.set_text_color(140, 140, 140)
            pdf.multi_cell(_COL_NAME_W, 4, f"Disc: -{discount:,.2f}")
            pdf.set_text_color(0, 0, 0)
            pdf.set_font("Helvetica", "", 9.5)
        row_h = pdf.get_y() - row_y
        row_h = max(row_h, 6)

        pdf.set_xy(x0 + _COL_NAME_W, row_y)
        pdf.cell(_COL_QTY_W, row_h, str(qty), align="C")
        pdf.cell(_COL_PRICE_W, row_h, f"{price:,.2f}", align="R")
        pdf.cell(_COL_TOTAL_W, row_h, f"{line_total:,.2f}", align="R")

        pdf.set_y(row_y + row_h)
        pdf.set_draw_color(230, 230, 230)
        pdf.line(x0, pdf.get_y(), x0 + _USABLE_W, pdf.get_y())
        pdf.ln(1)


def _totals(pdf, rows, currency, total_label, total_value):
    """rows: list of (label, value_str) plain lines; grand total drawn separately."""
    box_w = 80
    x = _PAGE_W - _MARGIN - box_w
    pdf.ln(2)
    pdf.set_font("Helvetica", "", 9.5)
    for label, value in rows:
        pdf.set_x(x)
        pdf.cell(box_w * 0.55, 6, label)
        pdf.cell(box_w * 0.45, 6, value, align="R", new_x="LMARGIN", new_y="NEXT")
    pdf.set_x(x)
    pdf.set_draw_color(50, 50, 50)
    pdf.set_line_width(0.5)
    y = pdf.get_y()
    pdf.line(x, y, x + box_w, y)
    pdf.ln(1)
    pdf.set_x(x)
    pdf.set_font("Helvetica", "B", 12)
    pdf.cell(box_w * 0.55, 8, total_label)
    pdf.cell(box_w * 0.45, 8, f"{currency} {total_value:,.2f}", align="R", new_x="LMARGIN", new_y="NEXT")


def _footer_note(pdf, lines):
    pdf.ln(10)
    pdf.set_draw_color(220, 220, 220)
    pdf.set_line_width(0.3)
    y = pdf.get_y()
    pdf.line(_MARGIN, y, _PAGE_W - _MARGIN, y)
    pdf.ln(4)
    pdf.set_font("Helvetica", "", 8.5)
    pdf.set_text_color(120, 120, 120)
    for line in lines:
        if not line:
            continue
        pdf.set_x(_MARGIN)
        pdf.multi_cell(_USABLE_W, 4.5, line, align="C")
    pdf.set_text_color(0, 0, 0)


def build_receipt_pdf(invoice, items, currency, business_name, business_address,
                       business_phone, receipt_footer):
    pdf = _new_pdf()
    created_at = invoice.get("created_at")
    date_str = created_at.strftime("%Y-%m-%d %H:%M") if hasattr(created_at, "strftime") else str(created_at or "-")
    meta = [f"INV-{int(invoice['invoice_id']):04d}", date_str]
    _header(pdf, business_name, business_address, business_phone, "RECEIPT", meta)

    if str(invoice.get("payment_method") or "").strip().lower() == "credit":
        pdf.set_font("Helvetica", "B", 9)
        pdf.set_text_color(176, 0, 32)
        pdf.set_draw_color(176, 0, 32)
        pdf.cell(0, 7, "  UNPAID / CREDIT  ", border=1, new_x="LMARGIN", new_y="NEXT")
        pdf.set_text_color(0, 0, 0)
        pdf.ln(3)

    customer = invoice.get("customer")
    _bill_to(pdf, "Bill To",
             customer["name"] if customer else "Walk-in customer",
             customer.get("phone") if customer else None,
             extra_right=f"Payment Method: {invoice.get('payment_method')}")

    _items_table(pdf, items)

    rows = [("Subtotal:", f"{invoice['subtotal']:,.2f}")]
    if invoice.get("discount_total"):
        rows.append(("Discount:", f"-{invoice['discount_total']:,.2f}"))
    if invoice.get("tax_rate") and invoice.get("tax_amount"):
        rows.append((f"Tax ({int(invoice['tax_rate'] * 100)}%):", f"{invoice['tax_amount']:,.2f}"))
    if invoice.get("delivery_charges"):
        rows.append(("Delivery:", f"{invoice['delivery_charges']:,.2f}"))
    _totals(pdf, rows, currency, "TOTAL:", invoice["total"])

    _footer_note(pdf, ["Thank you for your purchase!", receipt_footer])
    return bytes(pdf.output())


def build_quotation_pdf(quotation_id, items, subtotal, discount_total, tax_rate,
                         tax_amount, delivery_charges, total, customer, generated_at,
                         currency, business_name, business_address, business_phone,
                         receipt_footer):
    pdf = _new_pdf()
    date_str = generated_at.strftime("%Y-%m-%d %H:%M") if hasattr(generated_at, "strftime") else str(generated_at)
    meta = [f"Q-{int(quotation_id):04d}", date_str] if quotation_id else [date_str]
    _header(pdf, business_name, business_address, business_phone, "QUOTATION", meta)

    if customer:
        _bill_to(pdf, "Prepared For", customer["name"], customer.get("phone"))
    else:
        pdf.ln(2)

    _items_table(pdf, items)

    rows = [("Subtotal:", f"{subtotal:,.2f}")]
    if discount_total:
        rows.append(("Discount:", f"-{discount_total:,.2f}"))
    if tax_rate and tax_amount:
        rows.append((f"Tax ({int(tax_rate * 100)}%):", f"{tax_amount:,.2f}"))
    if delivery_charges:
        rows.append(("Delivery:", f"{delivery_charges:,.2f}"))
    _totals(pdf, rows, currency, "TOTAL:", total)

    _footer_note(pdf, ["This is a quotation only, not a tax invoice.",
                        "Prices subject to change.", receipt_footer])
    return bytes(pdf.output())
