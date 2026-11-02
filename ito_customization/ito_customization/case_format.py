import re

import frappe


CACHE_KEY = "case_format_config"
SYSTEM_SKIP = {
    "DocType",
    "Customize Form",
    "User",
    "Role",
    "Error Log",
    "Version",
    "Email Queue",
    "Communication",
    "Comment",
    "File",
    "Report",
    "Server Script",
    "Custom Field",
    "Property Setter",
    "Workflow",
    "Notification",
    "Case Format Settings",
    "Scheduled Job Log",
    "Activity Log",
    "Access Log",
    "Deleted Document",
}
SKIP_FIELDNAMES = {
    "name",
    "naming_series",
    "email",
    "email_id",
    "mobile_no",
    "phone",
    "website",
    "password",
    "username",
}
# Framework modules whose doctypes (and their child tables) must never be re-cased.
SKIP_MODULES = {
    "Core",
    "Custom",
    "Desk",
    "Email",
    "Automation",
    "Workflow",
    "Website",
    "Printing",
    "Integrations",
    "Social",
    "Data Migration",
    "Geo",
}
SKIP_OPTIONS = {"Email", "Phone", "URL", "Name", "Barcode", "Password"}
TABLE_FIELDS = {"Table", "Table MultiSelect"}


def to_title_case(value):
    return re.sub(
        r"[A-Za-z]+('[A-Za-z]+)?",
        lambda match: match.group(0)[:1].upper() + match.group(0)[1:].lower(),
        value,
    )


def to_camel_case(value):
    words = [word for word in re.split(r"[\s_-]+", value.strip()) if word]
    return words[0].lower() + "".join(word.capitalize() for word in words[1:]) if words else value


FORMATTERS = {"Title Case": to_title_case, "camelCase": to_camel_case}


def get_config():
    config = frappe.cache().get_value(CACHE_KEY)
    if config is None:
        config = _build_config()
        frappe.cache().set_value(CACHE_KEY, config)
    return config


def clear_config_cache():
    frappe.cache().delete_value(CACHE_KEY)


def apply_case_format(doc, method=None):
    if frappe.flags.in_install or frappe.flags.in_migrate or frappe.flags.in_patch:
        return

    config = get_config()
    if config["enabled"]:
        _format_doc(doc, config)


def _build_config():
    settings = frappe.get_single("Case Format Settings")
    skip_doctypes = set()
    skip_fields = {}

    for row in settings.exclusions or []:
        if row.field_name:
            skip_fields.setdefault(row.document_type, set()).add(row.field_name)
        else:
            skip_doctypes.add(row.document_type)

    return {
        "enabled": bool(settings.enabled),
        "format": settings.case_format or "Title Case",
        "small_text": bool(settings.include_small_text),
        "skip_doctypes": sorted(skip_doctypes),
        "skip_fields": {doctype: sorted(fields) for doctype, fields in skip_fields.items()},
    }


def _is_skipped_doctype(meta, doctype, config):
    return (
        doctype in SYSTEM_SKIP
        or doctype in config["skip_doctypes"]
        or bool(getattr(meta, "issingle", 0))
        or getattr(meta, "module", None) in SKIP_MODULES
    )


def _format_doc(doc, config):
    doctype = doc.doctype
    meta = frappe.get_meta(doctype)
    skip_this = _is_skipped_doctype(meta, doctype, config)
    formatter = FORMATTERS.get(config["format"])
    if formatter is None:
        frappe.throw(f"Unsupported case format: {config['format']}")

    for field in meta.fields:
        if field.fieldtype in TABLE_FIELDS:
            for row in doc.get(field.fieldname) or []:
                _format_doc(row, config)
        elif not skip_this and _is_eligible(field, doctype, config):
            value = doc.get(field.fieldname)
            if isinstance(value, str) and value:
                doc.set(field.fieldname, formatter(value))


def _is_eligible(field, doctype, config):
    allowed_types = {"Data", "Small Text"} if config["small_text"] else {"Data"}
    return (
        field.fieldtype in allowed_types
        and not field.read_only
        and not getattr(field, "is_virtual", 0)
        and not getattr(field, "unique", 0)
        and not getattr(field, "fetch_from", None)
        and field.fieldname not in SKIP_FIELDNAMES
        and (field.options or "") not in SKIP_OPTIONS
        and field.fieldname not in config["skip_fields"].get(doctype, [])
    )
