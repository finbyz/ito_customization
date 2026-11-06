import frappe


def execute():
	"""Mark existing portal-link students as such. Other rows stay blank: nothing on them says whether the
	school or the student registered them, so the origin is not guessed."""
	frappe.reload_doc("ito_customization", "doctype", "registered_students")

	frappe.db.sql(
		"""
		update `tabRegistered Students`
		set registration_source = 'Student Portal Link'
		where from_student_portal = 1 and ifnull(registration_source, '') = ''
		"""
	)
	unknown = frappe.db.sql(
		"""
		select count(*) from `tabRegistered Students`
		where ifnull(from_student_portal, 0) = 0 and ifnull(registration_source, '') = ''
		"""
	)[0][0]
	if unknown:
		print(f"registration_source: {unknown} Registered Students have no recorded origin and were left blank.")
