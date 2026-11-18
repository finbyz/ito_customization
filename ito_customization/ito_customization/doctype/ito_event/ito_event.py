import re

import frappe
from frappe.model.document import Document


def make_key(text):
	"""'Entry Note' -> 'entry_note', so it can be used as {{ event.details.entry_note }}."""
	return re.sub(r"[^a-z0-9]+", "_", (text or "").strip().lower()).strip("_")


class ITOEvent(Document):
	def validate(self):
		self._clean_details()
		self._check_outputs()
		self._check_single_active()
		self._check_web_forms()

	def on_update(self):
		from ito_customization.ito_customization.doc_events.template_checks import warn_for_event

		warn_for_event(self)

	def _clean_details(self):
		seen = set()
		for row in self.details:
			row.key = make_key(row.key or row.label)
			if not row.key:
				frappe.throw(f"Row {row.idx} in Details needs a Label.")
			if row.key in seen:
				frappe.throw(f"Details has the key <b>{row.key}</b> more than once (row {row.idx}).")
			seen.add(row.key)

	def _check_outputs(self):
		seen = set()
		for row in self.outputs:
			name = (row.output_name or "").strip()
			row.output_name = name
			if name.lower() in seen:
				frappe.throw(f"Outputs has <b>{name}</b> more than once (row {row.idx}).")
			seen.add(name.lower())

	def _check_single_active(self):
		if self.status != "Active":
			return
		other = frappe.db.get_value(
			"ITO Event", {"status": "Active", "name": ["!=", self.name or ""]}, "name"
		)
		if other:
			frappe.throw(
				f"<b>{other}</b> is already Active. Set it to Closed or Draft first, "
				"so new registrations know which event they belong to.",
				title="Another event is Active",
			)

	def _check_web_forms(self):
		"""A web form may belong to only one open (Draft or Active) event, so its registrations are never ambiguous."""
		if self.status == "Closed":
			return
		for row in self.web_forms:
			other = frappe.db.sql(
				"""select e.name from `tabITO Event` e
				join `tabITO Event Web Form` w on w.parent = e.name and w.parenttype = 'ITO Event'
				where w.web_form = %s and e.name != %s and e.status != 'Closed' limit 1""",
				(row.web_form, self.name or ""),
			)
			if other:
				frappe.throw(
					f"Web Form <b>{row.web_form}</b> already belongs to <b>{other[0][0]}</b>. "
					"Remove it there, or close that event first.",
					title="Web Form already in use",
				)
