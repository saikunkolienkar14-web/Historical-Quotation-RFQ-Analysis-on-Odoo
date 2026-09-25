"""
Odoo XML-RPC API

Customer / Industry data extractor.

Pulls sale.order records (customer link, industry, RFQ/PO fields)
and the res.partner records they reference.

Read-only: only search_read / read / fields_get are ever called.
No create / write / unlink method exists on this class.
"""

import xmlrpc.client

from config import (
    ODOO_URL,
    ODOO_DB,
    ODOO_USERNAME,
    ODOO_PASSWORD,
)


class OdooAPI:

    def __init__(self):

        # --------------------------------------------------
        # COMMON API
        # --------------------------------------------------

        self.common = xmlrpc.client.ServerProxy(
            f"{ODOO_URL}/xmlrpc/2/common"
        )

        # --------------------------------------------------
        # AUTHENTICATION
        # --------------------------------------------------

        self.uid = self.common.authenticate(
            ODOO_DB,
            ODOO_USERNAME,
            ODOO_PASSWORD,
            {},
        )

        if not self.uid:
            raise Exception(
                "Odoo Authentication Failed"
            )

        # --------------------------------------------------
        # OBJECT API
        # --------------------------------------------------

        self.models = xmlrpc.client.ServerProxy(
            f"{ODOO_URL}/xmlrpc/2/object"
        )

    # ======================================================
    # CONNECTION
    # ======================================================

    def test_connection(self):
        """
        Return authenticated Odoo user ID.
        """

        return self.uid

    # ======================================================
    # MODEL INSPECTION
    # ======================================================

    def get_model_fields(self, model_name):
        """
        Return all fields available on an Odoo model.

        Useful for debugging / checking field names.
        """

        return self.models.execute_kw(
            ODOO_DB,
            self.uid,
            ODOO_PASSWORD,
            model_name,
            "fields_get",
            [],
            {
                "attributes": [
                    "string",
                    "type",
                    "relation",
                ]
            },
        )

    # ======================================================
    # GET SALE ORDERS (paginated)
    # ======================================================

    def get_sale_orders(
        self,
        fields,
        batch_size=500,
    ):
        """
        Fetch ALL sale.order records, paginated.

        Odoo XML-RPC search_read can time out or be truncated
        on a very large unpaged read, so this loops with
        offset/limit until exhausted rather than fetching
        everything in a single call.

        If a field name in `fields` doesn't exist on the live
        database (Studio field names can drift from a schema
        doc), Odoo raises a fault naming the bad field. That
        field is dropped and the whole fetch is retried once
        with the remaining fields, rather than failing outright.

        Returns:
            records, skipped_fields
        """

        skipped_fields = []

        active_fields = list(fields)

        while True:

            try:

                records = self._search_read_all(
                    "sale.order",
                    active_fields,
                    batch_size,
                )

                return records, skipped_fields

            except xmlrpc.client.Fault as fault:

                bad_field = self._extract_bad_field(
                    fault,
                    active_fields,
                )

                if not bad_field:
                    raise

                active_fields.remove(
                    bad_field
                )

                skipped_fields.append(
                    bad_field
                )

    def _search_read_all(
        self,
        model,
        fields,
        batch_size,
    ):

        records = []

        offset = 0

        while True:

            batch = self.models.execute_kw(
                ODOO_DB,
                self.uid,
                ODOO_PASSWORD,
                model,
                "search_read",
                [
                    []
                ],
                {
                    "fields": fields,
                    "offset": offset,
                    "limit": batch_size,
                    "order": "id asc",
                },
            )

            if not batch:
                break

            records.extend(
                batch
            )

            if len(batch) < batch_size:
                break

            offset += batch_size

        return records

    @staticmethod
    def _extract_bad_field(
        fault,
        active_fields,
    ):
        """
        Odoo's invalid-field fault message names the field it
        rejected. Match it against the fields we actually asked
        for, so we only ever drop a field we requested.
        """

        message = str(
            fault.faultString
            if hasattr(fault, "faultString")
            else fault
        )

        for field in active_fields:

            if field in message:
                return field

        return None

    # ======================================================
    # GET RECORDS BY IDS (batched, any model)
    # ======================================================

    def get_records_by_ids(
        self,
        model,
        ids,
        fields,
        batch_size=200,
    ):
        """
        Fetch records for a specific list of ids on any model, in small
        batches rather than one large `read` call. Used both for
        res.partner and for many2many relation targets (e.g. the models
        behind x_studio_spares_type / x_studio_reason_for_loss).
        """

        if not ids:
            return []

        records = []

        ids = list(
            ids
        )

        for start in range(
            0,
            len(ids),
            batch_size,
        ):

            batch_ids = ids[
                start:start + batch_size
            ]

            batch = self.models.execute_kw(
                ODOO_DB,
                self.uid,
                ODOO_PASSWORD,
                model,
                "read",
                [
                    batch_ids
                ],
                {
                    "fields": fields,
                },
            )

            records.extend(
                batch
            )

        return records

    # ======================================================
    # GET PARTNERS BY IDS (batched)
    # ======================================================

    def get_partners_by_ids(
        self,
        partner_ids,
        fields,
        batch_size=200,
    ):
        """
        Fetch res.partner records for a specific list of ids,
        in small batches rather than one large `read` call.
        """

        return self.get_records_by_ids(
            "res.partner",
            partner_ids,
            fields,
            batch_size,
        )
