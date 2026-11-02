import frappe
from frappe.model.document import Document

from ito_customization.ito_customization.case_format import clear_config_cache


class CaseFormatSettings(Document):
    def validate(self):
        if self.case_format not in {"Title Case", "camelCase"}:
            frappe.throw(f"Unsupported case format: {self.case_format}")

        seen = set()
        for row in self.exclusions or []:
            field_name = row.field_name or ""
            key = (row.document_type, field_name)
            if key in seen:
                frappe.throw(f"Row {row.idx}: duplicate exclusion")
            seen.add(key)

            if field_name:
                field = frappe.get_meta(row.document_type).get_field(field_name)
                if not field or field.fieldtype not in {"Data", "Small Text"}:
                    frappe.throw(
                        f"Row {row.idx}: select a Data or Small Text field from {row.document_type}"
                    )

    def on_update(self):
        clear_config_cache()
