import frappe
from frappe import _
from customer_portal.webshop_compat.doctype.override_doctype.item import WebshopItem as _Item


class CustomItem(_Item):

    def validate(self):
        self.validate_barcode_required()

    def validate_barcode_required(self):
        if not self.item_group:
            return

        barcode_required = frappe.db.get_value(
            "Item Group",
            self.item_group,
            "custom_barcode_required"
        )

        if barcode_required and not self.barcodes:
            frappe.throw(
                _("At least one Barcode is required for Item Group '{0}'.").format(
                    self.item_group
                )
            )

    def autoname(self):
        company_prefix = self.get_company_prefix()

        if self.variant_of:
            # Variant item
            self.item_code = self.get_variant_base_code(company_prefix)
        else:
            # Template item or normal item
            self.item_code = self.get_non_variant_base_code(company_prefix)

        self.name = self.item_code

    def get_company_prefix(self):
        company = (
            getattr(self, "company", None)
            or frappe.defaults.get_user_default("Company")
            or frappe.db.get_single_value("Global Defaults", "default_company")
        )

        if not company:
            frappe.throw("Company is required to generate Item Code.")

        company_prefix = frappe.db.get_value("Company", company, "custom_company_prefix")

        if not company_prefix:
            frappe.throw(f"Company prefix is not set for Company: {company}")

        return company_prefix.strip()

    def get_non_variant_base_code(self, company_prefix):
        if not self.item_name:
            frappe.throw("Item Name is required to generate Item Code.")

        return f"{company_prefix}-{self.item_name.strip()}"

    def get_variant_base_code(self, company_prefix):
        abbreviations = []

        for attr in self.attributes or []:
            row = frappe.db.get_value(
                "Item Attribute Value",
                {
                    "parent": attr.attribute,
                    "attribute_value": attr.attribute_value,
                },
                ["abbr"],
                as_dict=True,
            )

            if not row or not row.abbr:
                frappe.throw(
                    f"Abbreviation not found for attribute '{attr.attribute}' "
                    f"with value '{attr.attribute_value}'."
                )

            abbreviations.append(row.abbr.strip())

        if not abbreviations:
            frappe.throw("Variant attributes are required to generate variant Item Code.")

        return f"{company_prefix}-{'-'.join(abbreviations)}"