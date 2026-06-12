import frappe


def validate_item_price(self,method):
    if not self.selling:
        return

    frappe.db.sql("""
        UPDATE `tabSchool Subject CT`
        SET item_price = %s
        WHERE item = %s
    """, (self.price_list_rate, self.item_code))