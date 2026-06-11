# Copyright (c) 2026, FinByz Tech Pvt Ltd and contributors
# For license information, please see license.txt

import frappe
from frappe.model.document import Document


class SchoolSubject(Document):

	def validate(self):
		self.set_item_prices()

	def set_item_prices(self):

		child_tables = [
			"practice_workbook_110",
			"student_guide_220",
			"prev_year_paper_160"
		]

		for table in child_tables:

			for row in self.get(table):

				if not row.item:
					row.item_price = 0
					continue

				price = frappe.db.get_value(
					"Item Price",
					{
						"item_code": row.item,
						"selling": 1
					},
					"price_list_rate"
				)

				row.item_price = price or 0