# Deployment & Operations

Practical runbooks for deploying LitePOS and onboarding shops. Replace every
`<...>` placeholder with your real values. **Never commit real passwords** — they
belong only in the server's `.env` (which is gitignored).

---

## A. First-time / routine deploy (server)

```bash
cd /var/www/pos/app
git pull                                   # normal pull
# if history was rewritten (e.g. after a secret purge), use instead:
#   git fetch origin && git reset --hard origin/main
/var/www/pos/venv/bin/pip install -r requirements.txt
systemctl restart pos
```

The MySQL backend auto-creates any new tables/columns on restart (the app calls
`init_workbook()` on startup). No manual SQL needed for schema changes.

---

## B. Going multi-tenant on an existing single-shop server

If the production database already holds the shop's real data:

```bash
cd /var/www/pos/app
git pull
/var/www/pos/venv/bin/pip install -r requirements.txt
# tag existing data as Account #1 and create the platform super-admin:
/var/www/pos/venv/bin/python3 migrate_to_multitenant.py \
    --business "<Business Name>" \
    --superadmin-user <superadmin-username> \
    --superadmin-pass '<strong-password>'
systemctl restart pos
```

---

## C. Moving a shop from local Excel onto the MySQL server

Use this when the real data lives in a local `data.xlsx` and the server has
only test/false data.

```bash
# 1) deploy the latest code
cd /var/www/pos/app
git fetch origin && git reset --hard origin/main
/var/www/pos/venv/bin/pip install -r requirements.txt

# 2) (optional) back up the throwaway data, then wipe it
mysqldump -u root -p pos > ~/pos_false_$(date +%F).sql
mysql -u root -p -e "DROP DATABASE pos; CREATE DATABASE pos;"

# 3) upload the client's final data.xlsx to the server (e.g. incoming.xlsx), then:
/var/www/pos/venv/bin/python3 import_legacy_to_mysql.py \
    --excel incoming.xlsx \
    --business "<Business Name>" \
    --superadmin-user <superadmin-username> \
    --superadmin-pass '<strong-password>'

# 4) restart
systemctl restart pos
```

The importer creates the business + super-admin, brings the shop's existing
logins across (same username/password), and loads products, customers,
invoices, ledgers, suppliers, purchases, chart of accounts, journal and
partners with original IDs.

**Verify:** the shop's admin logs in with their existing credentials and sees
their data; the super-admin lands on the Businesses screen; do one test sale.

---

## D. Adding more businesses later

No scripts needed. Log in as the **super-admin** → **Businesses** → **New
Business** (creates the business + its first admin login). Use the 🔑 / "Add
login" controls there to reset passwords or add logins per business.

---

## E. Password recovery

- **Staff** forgot → their business **admin** resets it (Users screen).
- **Business admin** forgot → **super-admin** resets it (Businesses screen, 🔑).
- **Super-admin** forgot → CLI on the server:
  ```bash
  /var/www/pos/venv/bin/python3 reset_password.py --username <user> --password <new>
  ```

---

## F. Rotating leaked / changed credentials

```bash
mysql -u root -p
```
```sql
ALTER USER 'root'@'localhost'        IDENTIFIED BY '<new-root-pass>';
ALTER USER 'pos_user'@'localhost'    IDENTIFIED BY '<new-pos-pass>';
FLUSH PRIVILEGES;
```
Then update `/var/www/pos/app/.env` (`MYSQL_PASSWORD`, and rotate `SECRET_KEY`)
and `systemctl restart pos`.

---

## Notes

- `.env`, `*.xlsx`, and `memory/` are gitignored — keep all secrets and data out
  of git.
- Nginx blocks `/.git`, dotfiles, `/backups`, and `/data.xlsx`.
- Backups of the Excel workbook are written to `backups/` (last 7 kept) on every
  write when using the Excel backend.
