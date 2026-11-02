from types import SimpleNamespace
from unittest.mock import patch

from frappe.tests import UnitTestCase

from ito_customization.ito_customization.case_format import (
    _format_doc,
    to_camel_case,
    to_title_case,
)


class TestCaseFormat(UnitTestCase):
    def test_title_case(self):
        self.assertEqual(to_title_case("jOHN o'NEIL"), "John O'neil")

    def test_camel_case(self):
        self.assertEqual(to_camel_case("student LIST-name"), "studentListName")

    def test_recursive_formatting_respects_exclusions(self):
        class FakeDocument:
            def __init__(self, doctype, **values):
                self.doctype = doctype
                self.values = values

            def get(self, fieldname):
                return self.values.get(fieldname)

            def set(self, fieldname, value):
                self.values[fieldname] = value

        parent = FakeDocument(
            "Parent",
            full_name="jANE DOE",
            email="USER@EXAMPLE.COM",
            notes="keep this lowercase",
            children=[FakeDocument("Child", name_text="rAVI SHAH", ignored="leave me")],
        )
        metadata = {
            "Parent": SimpleNamespace(
                fields=[
                    SimpleNamespace(fieldname="full_name", fieldtype="Data", read_only=0, options=None),
                    SimpleNamespace(fieldname="email", fieldtype="Data", read_only=0, options="Email"),
                    SimpleNamespace(fieldname="notes", fieldtype="Small Text", read_only=0, options=None),
                    SimpleNamespace(fieldname="children", fieldtype="Table", read_only=0, options="Child"),
                ]
            ),
            "Child": SimpleNamespace(
                fields=[
                    SimpleNamespace(fieldname="name_text", fieldtype="Data", read_only=0, options=None),
                    SimpleNamespace(fieldname="ignored", fieldtype="Data", read_only=0, options=None),
                ]
            ),
        }
        config = {
            "format": "Title Case",
            "small_text": False,
            "skip_doctypes": [],
            "skip_fields": {"Child": ["ignored"]},
        }

        with patch(
            "ito_customization.ito_customization.case_format.frappe.get_meta",
            side_effect=lambda doctype: metadata[doctype],
        ):
            _format_doc(parent, config)

        self.assertEqual(parent.get("full_name"), "Jane Doe")
        self.assertEqual(parent.get("email"), "USER@EXAMPLE.COM")
        self.assertEqual(parent.get("notes"), "keep this lowercase")
        self.assertEqual(parent.get("children")[0].get("name_text"), "Ravi Shah")
        self.assertEqual(parent.get("children")[0].get("ignored"), "leave me")
