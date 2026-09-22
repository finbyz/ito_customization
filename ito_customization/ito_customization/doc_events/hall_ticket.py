import json

import frappe
from frappe import _
from frappe.utils.pdf import get_pdf

PRINT_FORMAT = "ITO Hall Ticket"
PER_PAGE = 3

# --- sizing math (A3 = 297mm x 420mm) --------------------------------------
# Page margins: 6mm all sides  ->  usable height = 420 - 12 = 408mm
# 3 cards + 2 cut-line gaps must fit inside 408mm:
#   3 x 130mm = 390mm
#   2 x 6mm   =  12mm
#   total     = 402mm   (6mm spare)
PAGE_SIZE = "A3"
CARD_HEIGHT_MM = 130
GAP_MM = 6
PAGE_MARGIN_MM = 6


@frappe.whitelist()
def download_hall_tickets(names):
    """Render the ITO Hall Ticket print format for many students,
    exactly 3 per A4 page (fixed height + cut-line separators,
    matching the sample sheet), and return one PDF."""
    if isinstance(names, str):
        names = json.loads(names)

    if not names:
        frappe.throw(_("No students selected."))

    template = frappe.db.get_value("Print Format", PRINT_FORMAT, "html")
    if not template:
        frappe.throw(_("Print Format {0} not found.").format(PRINT_FORMAT))

    total = len(names)
    parts = []

    for i, name in enumerate(names):
        doc = frappe.get_doc("Registered Students", name)
        doc.check_permission("read")

        card_html = frappe.render_template(
            template,
            {
                "doc": doc,
                "frappe": frappe,
                "page_label": _("Page {0} of {1}").format(i + 1, total),
            },
        )

        # Fixed-height, clipped wrapper - this is what guarantees 3-per-page.
        # overflow:hidden is a safety net only; the template's own content
        # already sits at ~92mm, so nothing should actually get clipped.
        parts.append(
            '<div style="width:100%;height:{0}mm;'
            'overflow:hidden;box-sizing:border-box;">{1}</div>'.format(
                CARD_HEIGHT_MM, card_html
            )
        )

        is_last = i + 1 == total
        ends_page = (i + 1) % PER_PAGE == 0

        if is_last:
            continue
        elif ends_page:
            parts.append('<div style="page-break-after:always;"></div>')
        else:
            # cut-line separator, matching the ">%  ---  %<" mark on the sample
            parts.append(
                '<div style="height:{0}mm;box-sizing:border-box;'
                'display:table;width:100%;table-layout:fixed;">'
                '<div style="display:table-cell;width:16px;'
                'font-size:9px;color:#888;vertical-align:middle;">&gt;%</div>'
                '<div style="display:table-cell;'
                'border-top:1px dashed #999;"></div>'
                '<div style="display:table-cell;width:16px;'
                'font-size:9px;color:#888;text-align:right;'
                'vertical-align:middle;">%&lt;</div>'
                '</div>'.format(GAP_MM)
            )

    html = (
        "<!DOCTYPE html><html><head><meta charset='utf-8'></head>"
        "<body style='margin:0;padding:0;'>" + "".join(parts) + "</body></html>"
    )

    frappe.local.response.filename = "hall-tickets.pdf"
    frappe.local.response.filecontent = get_pdf(
        html,
        options={
            "page-size": PAGE_SIZE,
            "margin-top": "{0}mm".format(PAGE_MARGIN_MM),
            "margin-bottom": "{0}mm".format(PAGE_MARGIN_MM),
            "margin-left": "{0}mm".format(PAGE_MARGIN_MM),
            "margin-right": "{0}mm".format(PAGE_MARGIN_MM),
        },
    )
    frappe.local.response.type = "pdf"