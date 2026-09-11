"""
Odoo Customer / Industry Data Exporter

Pulls sale.order records (customer link, industry, RFQ/PO
fields) plus the res.partner records they reference, and writes
them to local CSVs - matching the file-based convention every
other stage of this pipeline uses.

Read-only against Odoo. Progress/summary output below is
aggregate (counts) only - never a customer name - matching the
confidentiality discipline used throughout this project. Redirect
this script's own stdout to a file when running it rather than
letting it print to a shared terminal/chat.
"""

import csv
import os

from odoo_api import OdooAPI
from config import OUTPUT_FOLDER


# ============================================================
# FIELDS TO FETCH
# ============================================================

SALE_ORDER_FIELDS = [
    "id",
    "name",
    "partner_id",
    "x_studio_adage_customer",
    "x_studio_end_user",
    "x_studio_type_of_industry",
    "x_studio_customer_type",
    "x_studio_rfq_reference_number",
    "x_studio_internal_rfq_assignment_number",
    "x_studio_po_number",
    "x_studio_po_value",
    "date_order",
    "state",
    "x_studio_responsible_region",
    "x_studio_control_no",
    "x_studio_adage_jobcontrol_no",
    "x_studio_firm_or_budgetary",
    "x_studio_quote_status",
]

PARTNER_FIELDS = [
    "id",
    "name",
    "ref",
    "email",
    "phone",
    "street",
    "city",
    "state_id",
    "country_id",
]

MANY2ONE_SALE_ORDER_FIELDS = [
    "partner_id",
    "x_studio_adage_customer",
    "x_studio_end_user",
]

MANY2ONE_PARTNER_FIELDS = [
    "state_id",
    "country_id",
]


# ============================================================
# HELPERS
# ============================================================

def many2one_id(value):
    """
    Odoo many2one values normally look like:

        [123, "John Smith"]

    Return the numeric id, or "" if unset.
    """

    if isinstance(value, list):

        if value:
            return value[0]

        return ""

    return value or ""


def many2one_name(value):
    """
    Same as above, but return the readable name.
    """

    if isinstance(value, list):

        if len(value) > 1:
            return value[1]

        return ""

    return value or ""


def flatten_sale_order(order):
    """
    Convert one raw sale.order record into a flat CSV row,
    splitting each many2one field into <field>_id / <field>_name
    columns.
    """

    row = {}

    for field, value in order.items():

        if field in MANY2ONE_SALE_ORDER_FIELDS:

            row[f"{field}_id"] = many2one_id(value)
            row[f"{field}_name"] = many2one_name(value)

        else:

            row[field] = value if value is not False else ""

    return row


def flatten_partner(partner):
    """
    Same idea for res.partner rows.
    """

    row = {}

    for field, value in partner.items():

        if field in MANY2ONE_PARTNER_FIELDS:

            row[f"{field}_id"] = many2one_id(value)
            row[f"{field}_name"] = many2one_name(value)

        else:

            row[field] = value if value is not False else ""

    return row


def sale_order_csv_fields():

    fields = []

    for field in SALE_ORDER_FIELDS:

        if field in MANY2ONE_SALE_ORDER_FIELDS:

            fields.append(f"{field}_id")
            fields.append(f"{field}_name")

        else:

            fields.append(field)

    return fields


def partner_csv_fields():

    fields = []

    for field in PARTNER_FIELDS:

        if field in MANY2ONE_PARTNER_FIELDS:

            fields.append(f"{field}_id")
            fields.append(f"{field}_name")

        else:

            fields.append(field)

    return fields


# ============================================================
# MAIN
# ============================================================

def main():

    print("=" * 70)
    print("ODOO CUSTOMER / INDUSTRY DATA EXPORTER")
    print("=" * 70)

    # --------------------------------------------------------
    # CONNECT
    # --------------------------------------------------------

    api = OdooAPI()

    print("\nConnected Successfully")
    print(f"User ID : {api.test_connection()}")

    # --------------------------------------------------------
    # FETCH SALE ORDERS
    # --------------------------------------------------------

    print("\nFetching sale.order records...")

    sale_orders, skipped_fields = api.get_sale_orders(
        SALE_ORDER_FIELDS
    )

    print(
        f"Sale orders fetched : {len(sale_orders)}"
    )

    if skipped_fields:

        print(
            "Fields not present on this database "
            "(skipped, not fatal):"
        )

        for field in skipped_fields:

            print(f"  - {field}")

    if not sale_orders:

        print("\nNo sale.order records found.")
        return

    # --------------------------------------------------------
    # COLLECT REFERENCED PARTNER IDS
    # --------------------------------------------------------

    partner_ids = set()

    for order in sale_orders:

        for field in MANY2ONE_SALE_ORDER_FIELDS:

            if field not in order:
                continue

            pid = many2one_id(
                order[field]
            )

            if pid:
                partner_ids.add(pid)

    print(
        f"Unique partner ids referenced : {len(partner_ids)}"
    )

    # --------------------------------------------------------
    # FETCH PARTNERS
    # --------------------------------------------------------

    print("\nResolving res.partner records...")

    partners = api.get_partners_by_ids(
        partner_ids,
        PARTNER_FIELDS,
    )

    print(
        f"Partners resolved : {len(partners)}"
    )

    # --------------------------------------------------------
    # OUTPUT FOLDER
    # --------------------------------------------------------

    os.makedirs(
        OUTPUT_FOLDER,
        exist_ok=True,
    )

    sale_orders_csv = os.path.join(
        OUTPUT_FOLDER,
        "sale_orders.csv",
    )

    partners_csv = os.path.join(
        OUTPUT_FOLDER,
        "res_partners.csv",
    )

    summary_txt = os.path.join(
        OUTPUT_FOLDER,
        "export_summary.txt",
    )

    # --------------------------------------------------------
    # WRITE SALE ORDERS CSV
    # --------------------------------------------------------

    print("\nWriting sale_orders.csv...")

    sale_order_fields = sale_order_csv_fields()

    with open(
        sale_orders_csv,
        "w",
        newline="",
        encoding="utf-8-sig",
    ) as file:

        writer = csv.DictWriter(
            file,
            fieldnames=sale_order_fields,
        )

        writer.writeheader()

        for order in sale_orders:

            writer.writerow(
                flatten_sale_order(order)
            )

    # --------------------------------------------------------
    # WRITE PARTNERS CSV
    # --------------------------------------------------------

    print("Writing res_partners.csv...")

    partner_fields = partner_csv_fields()

    with open(
        partners_csv,
        "w",
        newline="",
        encoding="utf-8-sig",
    ) as file:

        writer = csv.DictWriter(
            file,
            fieldnames=partner_fields,
        )

        writer.writeheader()

        for partner in partners:

            writer.writerow(
                flatten_partner(partner)
            )

    # --------------------------------------------------------
    # WRITE SUMMARY
    # --------------------------------------------------------

    with open(
        summary_txt,
        "w",
        encoding="utf-8",
    ) as file:

        file.write(
            "ODOO CUSTOMER / INDUSTRY EXPORT SUMMARY\n"
        )

        file.write(
            "=" * 70 + "\n\n"
        )

        file.write(
            f"Sale orders fetched      : {len(sale_orders)}\n"
        )

        file.write(
            f"Unique partners referenced: {len(partner_ids)}\n"
        )

        file.write(
            f"Partners resolved        : {len(partners)}\n\n"
        )

        if skipped_fields:

            file.write(
                "Fields not present on this database "
                "(skipped):\n"
            )

            for field in skipped_fields:

                file.write(f"  - {field}\n")

            file.write("\n")

        file.write("OUTPUTS\n")
        file.write(f"{sale_orders_csv}\n")
        file.write(f"{partners_csv}\n")

    # --------------------------------------------------------
    # FINAL CONSOLE
    # --------------------------------------------------------

    print("\n")
    print("=" * 70)
    print("EXPORT COMPLETE")
    print("=" * 70)

    print(f"Sale orders fetched       : {len(sale_orders)}")
    print(f"Unique partners referenced: {len(partner_ids)}")
    print(f"Partners resolved         : {len(partners)}")

    print("\nFiles created:")
    print(f"  {sale_orders_csv}")
    print(f"  {partners_csv}")
    print(f"  {summary_txt}")


if __name__ == "__main__":
    main()
