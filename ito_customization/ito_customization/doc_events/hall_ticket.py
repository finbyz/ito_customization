import json
import re

import frappe
from frappe import _
from frappe.utils.pdf import get_pdf

PRINT_FORMAT = "ITO Hall ticket"  # <- must match your Print Format's exact name/case
PER_PAGE = 3

# --- sizing math (A3 portrait = 297mm x 420mm) ------------------------------
# Every ticket is placed at an EXPLICIT top/left offset inside a page-sized
# container, instead of letting content flow and hoping it breaks in the
# right place. This is what actually guarantees 3-per-page: there is no
# overflow calculation left for the PDF engine to get wrong.
#
#   top margin + 3 x card height + 2 x gap + bottom margin = page height
#   5 + 3x131.2 + 2x8.2 + 5 = 420  ->  3 cards end at 415mm
PAGE_WIDTH_MM = 297
PAGE_HEIGHT_MM = 418  # Safe height within 420mm to prevent subpixel page overflow
CARD_WIDTH_MM = 287
CARD_HEIGHT_MM = 131.2
TOP_MARGIN_MM = 5
GAP_MM = 8.2
LEFT_OFFSET_MM = (PAGE_WIDTH_MM - CARD_WIDTH_MM) / 2  # centers the card horizontally


def _separator_html(top_mm):
    """Dashed cut-line with '>% ... %<' marks, positioned in a gap band."""
    return (
        '<div style="position:absolute;left:{0}mm;top:{1}mm;'
        'width:{2}mm;height:{3}mm;box-sizing:border-box;'
        'display:table;table-layout:fixed;">'
        '<div style="display:table-cell;width:16px;'
        'font-size:9px;color:#888;vertical-align:middle;">&gt;%</div>'
        '<div style="display:table-cell;'
        'border-top:1px dashed #999;"></div>'
        '<div style="display:table-cell;width:16px;'
        'font-size:9px;color:#888;text-align:right;'
        'vertical-align:middle;">%&lt;</div>'
        '</div>'.format(LEFT_OFFSET_MM, top_mm, CARD_WIDTH_MM, GAP_MM)
    )


@frappe.whitelist()
def download_hall_tickets(names):
    """Render the ITO Hall Ticket print format for many students,
    exactly 3 per A3 page, and return one PDF.

    Each page is a fixed container. The 3 tickets on it are
    placed with explicit top/left offsets (absolute positioning) rather
    than being stacked in normal document flow.
    """
    if isinstance(names, str):
        names = json.loads(names)

    if not names:
        frappe.throw(_("No students selected."))

    template = frappe.db.get_value("Print Format", PRINT_FORMAT, "html")
    if not template:
        frappe.throw(_("Print Format {0} not found.").format(PRINT_FORMAT))

    # Fetch student documents
    docs = []
    for name in names:
        doc = frappe.get_doc("Registered Students", name)
        doc.check_permission("read")
        docs.append(doc)

    # Sort in ascending order by roll_no / name (natural sort: e.g. 001 before 002)
    def _sort_key(d):
        val = str(d.roll_no or d.name or "")
        return [int(part) if part.isdigit() else part.lower() for part in re.split(r"(\d+)", val)]

    docs.sort(key=_sort_key)

    total = len(docs)

    # Render every card's inner HTML first.
    card_htmls = []
    for i, doc in enumerate(docs):
        card_htmls.append(
            frappe.render_template(
                template,
                {
                    "doc": doc,
                    "frappe": frappe,
                    "page_label": _("Page {0} of {1}").format(i + 1, total),
                },
            )
        )

    # Group into pages of exactly PER_PAGE cards each.
    pages = [card_htmls[i:i + PER_PAGE] for i in range(0, len(card_htmls), PER_PAGE)]

    page_blocks = []
    for page_index, page_cards in enumerate(pages):
        is_last_page = page_index == len(pages) - 1

        positioned = []
        for k, card_html in enumerate(page_cards):
            top = TOP_MARGIN_MM + k * (CARD_HEIGHT_MM + GAP_MM)
            positioned.append(
                '<div style="position:absolute;left:{0}mm;top:{1}mm;'
                'width:{2}mm;height:{3}mm;overflow:hidden;">{4}</div>'.format(
                    LEFT_OFFSET_MM, top, CARD_WIDTH_MM, CARD_HEIGHT_MM, card_html
                )
            )
            if k < len(page_cards) - 1:
                gap_top = TOP_MARGIN_MM + (k + 1) * CARD_HEIGHT_MM + k * GAP_MM
                positioned.append(_separator_html(gap_top))

        page_style = (
            "position:relative;width:{0}mm;height:{1}mm;"
            "box-sizing:border-box;overflow:hidden;"
        ).format(PAGE_WIDTH_MM, PAGE_HEIGHT_MM)
        if not is_last_page:
            page_style += "page-break-after:always;"

        page_blocks.append(
            '<div class="page-container" style="{0}">{1}</div>'.format(page_style, "".join(positioned))
        )

    html = (
        "<!DOCTYPE html><html><head><meta charset='utf-8'>"
        "<style>"
        "@page { size: 297mm 420mm; margin: 0; }"
        "html, body { margin: 0; padding: 0; width: 297mm; }"
        ".print-format { margin: 0mm !important; }"
        ".page-container:last-child { page-break-after: avoid !important; }"
        "</style>"
        "</head>"
        "<body style='margin:0;padding:0;'>"
        "<div id='header-html' style='display:none;'></div>"
        "<div id='footer-html' style='display:none;'></div>"
        + "".join(page_blocks) +
        "</body></html>"
    )

    pdf_options = {
        "page-size": "A3",
        "orientation": "Portrait",
        "margin-top": "0mm",
        "margin-bottom": "0mm",
        "margin-left": "0mm",
        "margin-right": "0mm",
        "encoding": "UTF-8",
        "disable-smart-shrinking": "",
        "print-media-type": "",
    }

    try:
        import pdfkit
        pdf_content = pdfkit.from_string(html, False, options=pdf_options)
    except Exception:
        pdf_content = get_pdf(html, options=pdf_options)

    frappe.local.response.filename = "hall-tickets.pdf"
    frappe.local.response.filecontent = pdf_content
    frappe.local.response.type = "pdf"