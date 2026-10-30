from types import SimpleNamespace

import frappe
from frappe.tests import UnitTestCase

from ito_customization.ito_customization.manual_registration_invoice import (
    _set_payment_fields,
    _validate_payment,
)


class TestManualRegistrationInvoice(UnitTestCase):
    def setUp(self):
        self.details = {
            "dd_no": "DD-123",
            "bank_name": "Test Bank",
            "date": "2026-10-30",
            "amount": "500.00",
        }

    def test_partial_payment_is_valid(self):
        amount_paid = _validate_payment(self.details, "dd", 1000)
        self.assertEqual(amount_paid, 500)

        invoice = SimpleNamespace()
        _set_payment_fields(invoice, "dd", self.details, amount_paid)
        self.assertEqual(invoice.custom_amount, 500)

    def test_full_payment_is_valid(self):
        amount_paid = _validate_payment({**self.details, "amount": "1000.00"}, "dd", 1000)
        self.assertEqual(amount_paid, 1000)

    def test_zero_payment_is_rejected(self):
        with self.assertRaises(frappe.ValidationError):
            _validate_payment({**self.details, "amount": "0"}, "dd", 1000)

    def test_overpayment_is_rejected(self):
        with self.assertRaises(frappe.ValidationError):
            _validate_payment({**self.details, "amount": "1000.01"}, "dd", 1000)
