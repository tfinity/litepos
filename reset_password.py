"""
Emergency password reset utility.
Run from the project directory:

    python3 reset_password.py --username admin --password newpass123

Works for both Excel and MySQL backends (reads .env automatically).
"""

import argparse
import sys
from werkzeug.security import generate_password_hash


def main():
    parser = argparse.ArgumentParser(description="Reset a POS user password")
    parser.add_argument("--username", required=False, help="Username to reset")
    parser.add_argument("--password", required=False, help="New password")
    parser.add_argument("--list-users", action="store_true", help="List all users and exit")
    args = parser.parse_args()

    try:
        import db as store
    except ImportError:
        import excel_db as store

    if args.list_users:
        users = store.get_all_users()
        if not users:
            print("No users found.")
        else:
            print(f"{'ID':<6} {'Username':<20} {'Role':<10} {'Active'}")
            print("-" * 50)
            for u in users:
                print(f"{u['user_id']:<6} {u['username']:<20} {u['role']:<10} {u['is_active']}")
        sys.exit(0)

    if not args.username or not args.password:
        parser.error("--username and --password are required unless using --list-users")

    user = store.get_user_by_username(args.username)
    if not user:
        print(f"Error: user '{args.username}' not found.")
        print("Run with --list-users to see all usernames.")
        sys.exit(1)

    if len(args.password) < 4:
        print("Error: password must be at least 4 characters.")
        sys.exit(1)

    new_hash = generate_password_hash(args.password)
    store.update_user_password(user["user_id"], new_hash)
    print(f"Password reset successfully for user '{args.username}'.")


if __name__ == "__main__":
    main()
