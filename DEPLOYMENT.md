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

> Need to get the `data.xlsx` onto the server first? See section **G**.

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

## G. Transferring a file (e.g. `data.xlsx`) to the server

Pick whichever works on your network. The server here is reachable at
`<server-ip>` (the real VPS IP, not the Cloudflare-proxied domain), and the app
lives in `/var/www/pos/app`.

### Option 1 — `scp` (simplest, if SSH works)
Run from your **local** machine, in the folder that has `data.xlsx`:
```bash
scp data.xlsx root@<server-ip>:/var/www/pos/app/incoming.xlsx
```
If your ISP/network blocks port 22 (the `scp` just times out), use the alternate
SSH port if you configured one:
```bash
scp -P 2222 data.xlsx root@<server-ip>:/var/www/pos/app/incoming.xlsx
```

### Option 2 — `rsync` (resumable, good for big files)
```bash
rsync -avz -e "ssh -p 22" data.xlsx root@<server-ip>:/var/www/pos/app/incoming.xlsx
```

### Option 3 — SFTP GUI (FileZilla / Cyberduck — no terminal)
- Host: `sftp://<server-ip>`  ·  User: `root`  ·  Port: `22` (or `2222`)
- Drag `data.xlsx` into `/var/www/pos/app/` and rename to `incoming.xlsx`.

### Option 4 — Upload link + `wget` (works even when SSH/SFTP is blocked)
This is the reliable fallback when port 22 is blocked on your network:
1. Upload `data.xlsx` somewhere that gives a **direct download link**
   (Google Drive → "anyone with link", Dropbox, WeTransfer, `0x0.st`, etc.).
2. On the server, pull it down:
   ```bash
   cd /var/www/pos/app
   wget "https://<direct-download-link>" -O incoming.xlsx
   ```
   (For Google Drive, use a direct-download URL, or `gdown <file-id>`.)

### Option 5 — Hostinger panel
Hostinger's **File Manager** (or the browser-based VPS console) can upload the
file straight into `/var/www/pos/app/` without any terminal.

**After upload**, confirm it landed and is intact:
```bash
ls -lh /var/www/pos/app/incoming.xlsx
```

---

## Notes

- `.env`, `*.xlsx`, and `memory/` are gitignored — keep all secrets and data out
  of git.
- Nginx blocks `/.git`, dotfiles, `/backups`, and `/data.xlsx`.
- Backups of the Excel workbook are written to `backups/` (last 7 kept) on every
  write when using the Excel backend.
