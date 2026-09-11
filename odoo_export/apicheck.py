"""
Run this first, before trusting the field list in
export_customers.py.

Model Overview.pdf is a schema snapshot, not guaranteed to
exactly match the live database's current Studio field names -
this prints the real ones for a given model.

Usage:
    python apicheck.py sale.order
    python apicheck.py res.partner
"""

import sys

from odoo_api import OdooAPI


MODEL_NAME = (
    sys.argv[1]
    if len(sys.argv) > 1
    else "sale.order"
)

api = OdooAPI()

fields = api.get_model_fields(
    MODEL_NAME
)

print("=" * 80)
print(f"MODEL FIELDS: {MODEL_NAME}")
print("=" * 80)

for field_name, info in fields.items():

    print(
        f"{field_name:55} "
        f"type={str(info.get('type')):12} "
        f"label={str(info.get('string')):30} "
        f"relation={info.get('relation')}"
    )
