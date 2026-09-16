"""
Customer -> Industry Proxy

x_studio_type_of_industry lives on sale.order (per-order), not
on res.partner (the customer record) - and one customer can have
multiple orders across different industries. This script computes,
per customer, the MOST COMMON industry across all their orders -
an approximation usable for aggregate industry-wise analysis, not
a precise per-document fact.

Input:
    Quotation_Data/05_odoo_export/sale_orders.csv

Output:
    Quotation_Data/05_odoo_export/customer_industry_proxy.csv

Columns:
    partner_id, industry, industry_specify_others, order_count,
    industry_order_count

    order_count         - total orders for this customer
    industry_order_count - how many of those orders had this
                           (winning) industry value, so the
                           proxy's confidence is visible
                           downstream (e.g. "3 of 5 orders")
    industry_specify_others - x_studio_specify_others from the most
                           recently dated order among those that had
                           the winning industry value. Only meaningful
                           when industry is "Others" (that's the only
                           case Odoo populates this field); carried
                           through as-is otherwise blank. Not itself
                           used to decide the winning industry - two
                           "Others" orders with different specify-text
                           still count as the same industry, per project
                           convention (raw stays beside derived, not
                           folded into the matched value).

Orders with no partner_id or no industry value are excluded from
the counts. Ties are broken by picking the industry seen on the
most recently dated order.
"""

import csv
from collections import defaultdict
from pathlib import Path


PROJECT_ROOT = Path(__file__).resolve().parent.parent

SALE_ORDERS_CSV = (
    PROJECT_ROOT / "Quotation_Data/05_odoo_export/sale_orders.csv"
)

OUTPUT_CSV = (
    PROJECT_ROOT / "Quotation_Data/05_odoo_export/customer_industry_proxy.csv"
)


def read_csv(path):

    with open(
        path,
        "r",
        encoding="utf-8-sig",
        newline="",
    ) as file:

        return list(
            csv.DictReader(file)
        )


def main():

    print("=" * 70)
    print("CUSTOMER -> INDUSTRY PROXY")
    print("=" * 70)

    if not SALE_ORDERS_CSV.exists():

        print("\nERROR: sale_orders.csv not found:")
        print(SALE_ORDERS_CSV)
        return

    orders = read_csv(
        SALE_ORDERS_CSV
    )

    print(
        f"\nSale orders read: {len(orders)}"
    )

    # ----------------------------------------------------------
    # GROUP BY CUSTOMER
    # ----------------------------------------------------------

    # partner_id -> list of (industry, date_order)
    by_customer = defaultdict(list)

    skipped_no_partner = 0

    for order in orders:

        partner_id = (
            order.get("partner_id_id", "")
            or ""
        ).strip()

        if not partner_id:

            skipped_no_partner += 1
            continue

        by_customer[partner_id].append(order)

    print(
        f"Customers with at least one order: {len(by_customer)}"
    )

    print(
        f"Orders skipped (no partner_id)   : {skipped_no_partner}"
    )

    # ----------------------------------------------------------
    # COMPUTE MODE INDUSTRY PER CUSTOMER
    # ----------------------------------------------------------

    rows = []

    customers_with_no_industry = 0

    for partner_id, customer_orders in by_customer.items():

        order_count = len(customer_orders)

        industry_counts = defaultdict(int)

        # Track the most recent date seen per industry, for
        # tie-breaking, and the specify_others text that came with
        # that same most-recent order (only meaningful when the
        # industry is "Others" - blank otherwise).
        industry_latest_date = defaultdict(str)
        industry_latest_specify_others = defaultdict(str)

        for order in customer_orders:

            industry = (
                order.get("x_studio_type_of_industry", "")
                or ""
            ).strip()

            if not industry:
                continue

            industry_counts[industry] += 1

            date_order = (
                order.get("date_order", "")
                or ""
            )

            if date_order > industry_latest_date[industry]:

                industry_latest_date[industry] = date_order

                industry_latest_specify_others[industry] = (
                    order.get("x_studio_specify_others", "")
                    or ""
                ).strip()

        if not industry_counts:

            customers_with_no_industry += 1

            rows.append({
                "partner_id": partner_id,
                "industry": "",
                "industry_specify_others": "",
                "order_count": order_count,
                "industry_order_count": 0,
            })

            continue

        max_count = max(
            industry_counts.values()
        )

        tied_industries = [
            industry
            for industry, count in industry_counts.items()
            if count == max_count
        ]

        if len(tied_industries) == 1:

            winning_industry = tied_industries[0]

        else:

            # Tie-break: most recently dated order among the
            # tied industries.
            winning_industry = max(
                tied_industries,
                key=lambda industry: industry_latest_date[industry],
            )

        rows.append({
            "partner_id": partner_id,
            "industry": winning_industry,
            "industry_specify_others":
                industry_latest_specify_others[winning_industry],
            "order_count": order_count,
            "industry_order_count": max_count,
        })

    print(
        f"Customers with no industry on any order: "
        f"{customers_with_no_industry}"
    )

    # ----------------------------------------------------------
    # WRITE OUTPUT
    # ----------------------------------------------------------

    OUTPUT_CSV.parent.mkdir(
        parents=True,
        exist_ok=True,
    )

    with open(
        OUTPUT_CSV,
        "w",
        newline="",
        encoding="utf-8-sig",
    ) as file:

        writer = csv.DictWriter(
            file,
            fieldnames=[
                "partner_id",
                "industry",
                "industry_specify_others",
                "order_count",
                "industry_order_count",
            ],
        )

        writer.writeheader()
        writer.writerows(rows)

    print("\n")
    print("=" * 70)
    print("DONE")
    print("=" * 70)

    print(
        f"Customers written : {len(rows)}"
    )

    print(f"\nOutput:\n{OUTPUT_CSV}")


if __name__ == "__main__":
    main()
