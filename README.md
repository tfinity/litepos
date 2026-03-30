# POS System

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
- **Data Storage:** Excel via openpyxl - no database required
- **Frontend:** HTML, CSS, JavaScript, Bootstrap 5 (CDN)

## Setup

```bash
pip install -r requirements.txt
cp .env.example .env   # Edit with your business details
python3 app.py
```

Open http://localhost:5000

## Configuration

Copy `.env.example` to `.env` and set your values, or edit the defaults at the top of `app.py`:

| Variable | Description | Default |
|----------|-------------|---------|
| `SECRET_KEY` | Flask session secret | Random on each start |
| `BUSINESS_NAME` | Store name shown in UI and receipts | My Pharmacy |
| `BUSINESS_ADDRESS` | Address on receipts | 123 Main Street |
| `BUSINESS_PHONE` | Phone on receipts | +1 000 000 0000 |
| `CURRENCY` | Currency code | USD |
| `TAX_RATE` | Tax rate as decimal (e.g. 0.16 = 16%) | 0.0 |
| `LOW_STOCK_THRESHOLD` | Stock level for low-stock alerts | 10 |
| `EXPIRY_WARNING_DAYS` | Days before expiry to trigger alerts | 30 |
| `RECEIPT_FOOTER` | Custom message on receipt footer | (empty) |

## License

MIT - see [LICENSE](LICENSE)

---

*Vibe Coded with Claude AI*
