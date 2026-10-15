"""Delivery-charge rules shared by every book-order checkout flow."""

import frappe
from frappe import _
from frappe.utils import flt


DELIVERY_CHARGE_DESCRIPTION = "Delivery Charge"


def get_delivery_charge_rate(customer: str, company: str) -> float:
	"""Return the customer's rate after their first completed book order."""
	configured_rate = flt(frappe.db.get_value("Customer", customer, "custom_delivery_charge"))
	if not configured_rate:
		return 0.0

	has_previous_order = frappe.db.exists(
		"Sales Order",
		{"customer": customer, "company": company, "docstatus": 1},
	)
	return configured_rate if has_previous_order else 0.0


def apply_delivery_charge(document, rate: float, clear_existing_taxes: bool = False) -> float:
	"""Apply the configured delivery percentage as a visible sales charge."""
	if clear_existing_taxes:
		document.set("taxes", [])
	else:
		document.set(
			"taxes",
			[tax for tax in document.taxes if tax.description != DELIVERY_CHARGE_DESCRIPTION],
		)

	if rate:
		account = frappe.db.get_value("Company", document.company, "default_income_account")
		if not account:
			frappe.throw(
				_("Set a Default Income Account for company {0} before applying delivery charges.").format(
					frappe.bold(document.company)
				)
			)

		document.append(
			"taxes",
			{
				"charge_type": "On Net Total",
				"account_head": account,
				"add_deduct_tax": "Add",
				"category": "Total",
				"description": DELIVERY_CHARGE_DESCRIPTION,
				"rate": rate,
			},
		)

	document.calculate_taxes_and_totals()
	return flt(document.total_taxes_and_charges)
