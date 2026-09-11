import os
from pathlib import Path

from dotenv import load_dotenv

load_dotenv()

ODOO_URL = os.getenv("ODOO_URL")
ODOO_DB = os.getenv("ODOO_DB")
ODOO_USERNAME = os.getenv("ODOO_USERNAME")
ODOO_PASSWORD = os.getenv("ODOO_PASSWORD")

# Anchored to the project root (one level up from this file's own
# folder) rather than a plain relative path, so the output always
# lands in the same place regardless of the working directory the
# script happens to be run from.
PROJECT_ROOT = Path(__file__).resolve().parent.parent

OUTPUT_FOLDER = str(
    PROJECT_ROOT / "Quotation_Data" / "05_odoo_export"
)
