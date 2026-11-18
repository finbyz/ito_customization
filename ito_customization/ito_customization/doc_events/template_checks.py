"""Check event templates when they are saved, so a mistake shows up in the desk and not as a
failed or half-empty email on the day.

Frappe already refuses Jinja syntax errors. On top of that, for an Email Template or Print Format
used by an ITO Event output, this:
  1. finds placeholders that do not exist, e.g. {{ event.venu_name }} or {{ event.details.dress_cod }},
     which would otherwise print nothing, and suggests the closest correct name;
  2. renders the template for a real registration of that event, and reports any error.

Saving a template with a problem is refused. Saving an ITO Event shows the same problems as a
warning (e.g. a Details row a template still uses was removed), but still saves.
"""

import difflib

import frappe
from frappe.model.document import Document
from frappe.utils import cint
from frappe.utils.jinja import safe_render_flags
from jinja2 import nodes

from ito_customization.ito_customization.doc_events.event_outputs import (
	DOCTYPE,
	EVENT_DOCTYPE,
	EVENT_FIELDS,
	render_context,
)

EVENT_EXTRA = {"name", "details", "logo_url", "date_long", "date_short", "weekday", "time_display", "when_short"}
LINK_FIELD = {"Email Template": "email_template", "Print Format": "print_format"}


# ----------------------------------------------------------------------------
# doc_events hooks
# ----------------------------------------------------------------------------


def check_email_template(doc, method=None):
	_check_and_throw("Email Template", doc.name, _email_sources(doc))


def check_print_format(doc, method=None):
	if doc.print_format_type != "Jinja" or not cint(doc.custom_format) or not doc.html:
		return
	_check_and_throw("Print Format", doc.name, {"HTML": doc.html})


def warn_for_event(event_doc):
	"""Called from ITO Event.on_update: show problems in its outputs' templates, but do not block."""
	if frappe.flags.in_install or frappe.flags.in_migrate or frappe.flags.in_patch:
		return
	problems = []
	for row in event_doc.outputs:
		if not cint(row.enabled):
			continue
		if row.email_template and frappe.db.exists("Email Template", row.email_template):
			tpl = frappe.get_doc("Email Template", row.email_template)
			problems += _problems("Email Template", tpl.name, _email_sources(tpl), [event_doc], row.output_name)
		if row.print_format and frappe.db.exists("Print Format", row.print_format):
			pf = frappe.get_doc("Print Format", row.print_format)
			if pf.print_format_type == "Jinja" and cint(pf.custom_format) and pf.html:
				problems += _problems("Print Format", pf.name, {"HTML": pf.html}, [event_doc], row.output_name)
	if problems:
		frappe.msgprint(
			_problem_list(problems)
			+ "<p>The event was saved. Fix the template, or add the missing row to Extra Details.</p>",
			title="Some templates need attention",
			indicator="orange",
		)


# ----------------------------------------------------------------------------
# Checks
# ----------------------------------------------------------------------------


def _email_sources(tpl):
	return {"Subject": tpl.subject or "", "Body": (tpl.response_html if cint(tpl.use_html) else tpl.response) or ""}


def _events_using(doctype, name):
	parents = frappe.get_all(
		"ITO Event Output",
		filters={LINK_FIELD[doctype]: name, "parenttype": EVENT_DOCTYPE},
		pluck="parent",
		distinct=True,
	)
	return [frappe.get_doc(EVENT_DOCTYPE, p) for p in parents]


def _check_and_throw(doctype, name, sources):
	if frappe.flags.in_install or frappe.flags.in_migrate or frappe.flags.in_patch:
		return
	if not name or not frappe.db.table_exists("ITO Event Output"):
		return
	events = _events_using(doctype, name)
	if not events:
		return  # not an event template: leave it alone
	problems = []
	for event in events:
		output = next(r.output_name for r in event.outputs if r.get(LINK_FIELD[doctype]) == name)
		problems += _problems(doctype, name, sources, [event], output)
	if problems:
		frappe.throw(
			_problem_list(problems) + "<p>Nothing was saved. Fix these and save again.</p>",
			title="This template has problems",
		)


def _problems(doctype, name, sources, events, output):
	"""List of (where, message) for this template and these events."""
	found = []
	jenv = frappe.get_jenv()
	for part, source in sources.items():
		if not source:
			continue
		try:
			tree = jenv.parse(source)
		except Exception:
			continue  # syntax errors are reported by Frappe itself
		for event in events:
			for lineno, message in _unknown_placeholders(tree, event):
				found.append((f"{doctype} <b>{frappe.utils.escape_html(name)}</b>, {part}, line {lineno}", message))

	if not found:
		for event in events:
			found += _render_errors(doctype, name, sources, event, output)
	# same message for several events: keep once
	return list(dict.fromkeys(found))


def _chain(node):
	"""event.details.entry_note -> ("event", "details", "entry_note"); None if it does not start with a name."""
	attrs = []
	while isinstance(node, nodes.Getattr):
		attrs.append(node.attr)
		node = node.node
	if not isinstance(node, nodes.Name):
		return None
	return (node.name, *reversed(attrs))


def _registration_fields():
	from frappe.model import default_fields

	meta = frappe.get_meta(DOCTYPE)
	return {f.fieldname for f in meta.fields} | set(default_fields) | {"name"}


def _unknown_placeholders(tree, event):
	event_keys = set(EVENT_FIELDS) | EVENT_EXTRA
	detail_keys = {row.key for row in event.details if row.key}
	reg_fields = _registration_fields()
	esc = frappe.utils.escape_html

	seen = set()
	for node in tree.find_all(nodes.Getattr):
		chain = _chain(node)
		if not chain or len(chain) < 2 or chain in seen:
			continue
		seen.add(chain)
		root, attr = chain[0], chain[1]

		if root == "event":
			if attr == "details":
				if len(chain) >= 3 and chain[2] not in detail_keys:
					key = chain[2]
					hint = _suggest(key, detail_keys, "event.details.")
					yield node.lineno, (
						f"<code>{{{{ event.details.{esc(key)} }}}}</code> has no row with key <b>{esc(key)}</b> "
						f"in Extra Details of <b>{esc(event.name)}</b>, so it would print nothing.{hint}"
					)
			elif attr not in event_keys and not hasattr(dict, attr):
				yield node.lineno, (
					f"<code>{{{{ event.{esc(attr)} }}}}</code> is not an event placeholder.{_suggest(attr, event_keys, 'event.')}"
				)
		elif root == "doc":
			if attr not in reg_fields and not hasattr(Document, attr):
				yield node.lineno, (
					f"<code>{{{{ doc.{esc(attr)} }}}}</code> is not a field of {DOCTYPE}.{_suggest(attr, reg_fields, 'doc.')}"
				)


def _suggest(word, options, prefix):
	close = difflib.get_close_matches(word, list(options), n=1, cutoff=0.6)
	return f" Did you mean <code>{{{{ {prefix}{frappe.utils.escape_html(close[0])} }}}}</code>?" if close else ""


def _sample_registration(event):
	return frappe.db.get_value(DOCTYPE, {"event": event, "status": "Approve"}, "name") or frappe.db.get_value(
		DOCTYPE, {"event": event}, "name"
	)


def _render_errors(doctype, name, sources, event, output):
	docname = _sample_registration(event.name)
	if not docname:
		return []
	doc = frappe.get_doc(DOCTYPE, docname)
	if doctype == "Email Template":
		ctx = render_context(doc, event.name, output)
	else:
		ctx = {"doc": doc}
	errors = []
	for part, source in sources.items():
		try:
			# Jinja directly (not frappe.render_template), so the error is the real one, not a traceback dump
			with safe_render_flags():
				frappe.get_jenv().from_string(source).render(ctx)
		except Exception as e:
			errors.append(
				(
					f"{doctype} <b>{frappe.utils.escape_html(name)}</b>, {part}",
					f"Fails for registration {docname} of <b>{frappe.utils.escape_html(event.name)}</b>: "
					f"{frappe.utils.escape_html(str(e) or type(e).__name__)}",
				)
			)
	return errors


def _problem_list(problems):
	items = "".join(f"<li>{where}: {message}</li>" for where, message in problems)
	return f"<ul style='padding-left:18px'>{items}</ul>"
