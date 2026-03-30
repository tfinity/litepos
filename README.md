# ANIMAL NEXUS PHARMACY - POS System

A Point of Sale system built with Python Flask and Excel (openpyxl) as the data backend.

## Features

- **Product Management** - Add, edit, delete products with purchase price, counter price, and MRP
- **Excel Import** - Bulk import products from `.xlsx` files
- **Invoice & Sales** - Create sales with product search, quantity selection, and direct-amount discount
- **Discount Guard** - Prevents discounts that would drop the price below purchase cost
- **Stock Report** - Color-coded stock levels with low-stock alerts
- **Expiry Report** - Tracks expired and soon-to-expire products
- **Receipt Printing** - Thermal printer compatible (80mm) receipts via browser print

## Tech Stack

- **Backend:** Python, Flask
- **Data Storage:** Excel (openpyxl) - no database required
- **Frontend:** HTML, CSS, JavaScript, Bootstrap 5

## Setup

```bash
pip install -r requirements.txt
python3 app.py
```

Open http://localhost:5000

## Configuration

Edit the top of `app.py` to change:

- `BUSINESS_NAME` - Store name
- `BUSINESS_ADDRESS` - Store address
- `BUSINESS_PHONE` - Contact number
- `CURRENCY` - Currency code (default: PKR)
- `TAX_RATE` - Tax percentage (default: 0)
- `LOW_STOCK_THRESHOLD` - Low stock alert level (default: 10)

## License

MIT

---

*Vibe Coded with Claude AI*
