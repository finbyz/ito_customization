"""Numbers for the Overview section of the ITO Event form."""

import frappe
from frappe.utils import cint

from ito_customization.ito_customization.doc_events.event_outputs import (
	DOCTYPE,
	EVENT_DOCTYPE,
	LEGACY_OUTPUT,
	get_outputs,
)


@frappe.whitelist()
def get_event_stats(event):
	frappe.has_permission(EVENT_DOCTYPE, "read", doc=event, throw=True)
	frappe.has_permission(DOCTYPE, "read", throw=True)

	regs = frappe.get_all(
		DOCTYPE,
		filters={"event": event},
		fields=["name", "status", "email", "event_attendance", "epass_email_sent", "mail_sent"],
	)
	approved = [r for r in regs if (r.status or "").strip().lower() == "approve"]
	approved_names = {r.name for r in approved}

	return {
		"registrations": {
			"total": len(regs),
			"approved": len(approved),
			"rejected": sum(1 for r in regs if (r.status or "").strip().lower() == "reject"),
			"pending": sum(1 for r in regs if not (r.status or "").strip()),
		},
		"attendance": {
			"present": sum(1 for r in approved if r.event_attendance == "Present"),
			"absent": sum(1 for r in approved if r.event_attendance == "Absent"),
			"not_marked": sum(1 for r in approved if r.event_attendance not in ("Present", "Absent")),
		},
		"outputs": _output_stats(event, regs, approved_names),
	}


def _output_stats(event, regs, approved_names):
	logs = frappe.get_all(
		"ITO Output Log",
		filters={"parenttype": DOCTYPE, "event": event},
		fields=["parent", "output"],
	)
	out = []
	for row in get_outputs(event):
		sent = {l.parent for l in logs if l.output == row.output_name}
		if row.output_name == LEGACY_OUTPUT:
			sent |= {r.name for r in regs if cint(r.epass_email_sent) or cint(r.mail_sent)}
		due = approved_names if cint(row.requires_approval) else {r.name for r in regs}
		due = {r.name for r in regs if r.name in due and r.email}
		out.append(
			{
				"output": row.output_name,
				"send_on": row.send_on,
				"sent": len(sent),
				"not_sent": sorted(due - sent),
			}
		)
	return out
