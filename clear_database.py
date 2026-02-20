"""Clear the database file so the app starts fresh. Run this to reset for testing."""
import os

SCRIPT_DIR = os.path.dirname(os.path.abspath(__file__))
DATABASE_PATH = os.path.join(SCRIPT_DIR, "database.db")

if os.path.exists(DATABASE_PATH):
    os.remove(DATABASE_PATH)
    print("Database cleared. Restart the server to recreate tables.")
else:
    print("No database file found.")
