# LitePOS

A lightweight, open-source Point of Sale system for small businesses — pharmacies, retail shops, general stores. Built with Python Flask, runs on any machine with a browser. No expensive POS hardware or subscription required.

**Live demo:** https://pos.animalnexus.com.pk

![Python](https://img.shields.io/badge/Python-3.10+-blue)
![Flask](https://img.shields.io/badge/Flask-3.1-green)
![License](https://img.shields.io/badge/License-MIT-yellow)
![Backend](https://img.shields.io/badge/Backend-Excel%20%7C%20MySQL-orange)

---

## Features

- **Sales & Invoicing** — Product search, quantity, per-unit discounts, tax, receipt printing
- **Quotation Mode** — Print quotes without recording a sale or decrementing stock
- **Edit Invoices** — Swap products, change quantities, update payment method on finalized bills
- **Customer Profiles** — Track walk-in and registered customers
- **Credit Ledger** — Credit sales, payment tracking, outstanding balance per customer
- **Product Management** — Purchase price, counter price, MRP, stock levels, expiry tracking
- **Bulk Import** — Import products from `.xlsx` files
- **Stock Alerts** — Low stock and expiry warnings on dashboard
- **Thermal Receipt Printing** — 76mm/80mm thermal printer compatible via browser print
- **Multi-device** — Run on a server, access from any device on the network or internet
- **Dual Database** — Works with a local Excel file (zero setup) or MySQL (multi-device / production)

---

## Quick Start (Local / Excel mode)

No database setup required — data is stored in a local `data.xlsx` file.

```bash
git clone https://github.com/tfinity/litepos
cd litepos
pip install -r requirements.txt
cp .env.example .env        # edit with your business details
python3 app.py
```

Open http://localhost:5000

---

## Production Setup (MySQL + Server)

For multi-device access or deploying to a VPS:

### 1. Install dependencies
```bash
pip install -r requirements.txt
```

### 2. Create MySQL database
```sql
CREATE DATABASE pos CHARACTER SET utf8mb4 COLLATE utf8mb4_unicode_ci;
CREATE USER 'pos_user'@'localhost' IDENTIFIED BY 'your_password';
GRANT ALL PRIVILEGES ON pos.* TO 'pos_user'@'localhost';
```

### 3. Configure .env
```bash
cp .env.example .env
```
Set `DB_BACKEND=mysql` and fill in your MySQL credentials.

### 4. Initialise database
```bash
python3 -c "import db; db.init_workbook()"
```

### 5. Migrate existing Excel data (optional)
If you have existing data in `data.xlsx`:
```bash
python3 migrate_to_mysql.py
```

### 6. Run with gunicorn
```bash
gunicorn -w 2 -b 127.0.0.1:5001 app:app
```

### 7. Nginx reverse proxy
Point your domain to gunicorn. Example config:
```nginx
server {
    listen 443 ssl;
    server_name pos.yourdomain.com;

    location / {
        proxy_pass http://127.0.0.1:5001;
        proxy_set_header Host $host;
        proxy_set_header X-Forwarded-Proto $scheme;
    }
}
```

---

## Configuration

Copy `.env.example` to `.env` and set your values:

| Variable | Description | Default |
|----------|-------------|---------|
| `DB_BACKEND` | `excel` or `mysql` | `excel` |
| `MYSQL_HOST` | MySQL server host | `localhost` |
| `MYSQL_PORT` | MySQL server port | `3306` |
| `MYSQL_USER` | MySQL username | `pos_user` |
| `MYSQL_PASSWORD` | MySQL password | _(empty)_ |
| `MYSQL_DATABASE` | MySQL database name | `pos` |
| `SECRET_KEY` | Flask session secret | `change-me-in-production` |
| `BUSINESS_NAME` | Store name on receipts | `My Pharmacy` |
| `BUSINESS_ADDRESS` | Address on receipts | — |
| `BUSINESS_PHONE` | Phone on receipts | — |
| `CURRENCY` | Currency symbol | `USD` |
| `TAX_RATE` | Tax rate as decimal (0.16 = 16%) | `0.0` |
| `LOW_STOCK_THRESHOLD` | Units below this triggers alert | `10` |
| `EXPIRY_WARNING_DAYS` | Days ahead to warn on expiry | `30` |
| `RECEIPT_FOOTER` | Custom message on receipt | _(empty)_ |

---

## Tech Stack

| Layer | Technology |
|-------|-----------|
| Backend | Python 3.10+, Flask 3.1 |
| Database | Excel/openpyxl (default) or MySQL via PyMySQL |
| Frontend | Bootstrap 5, Vanilla JS |
| Production | Gunicorn, Nginx |

---

## Contributing

Contributions are welcome. Here's how to get started:

1. Fork the repository
2. Create a branch: `git checkout -b feature/your-feature`
3. Make your changes and test locally
4. Open a Pull Request with a clear description of what you changed and why

### Good first issues to work on
- Add PostgreSQL / SQLite backend support
- Add barcode scanner support
- Add multi-currency support
- Add user authentication / staff accounts
- Add sales reports and charts
- Add REST API for integration with other systems
- Add dark mode

### Code style
- Python: follow PEP8, keep functions focused
- No unnecessary comments — code should be self-explanatory
- Keep the zero-setup Excel backend working — don't break it for MySQL features

---

## License

MIT — free to use, modify, and distribute. See [LICENSE](LICENSE).

---

*Built with [Claude AI](https://claude.ai)*
