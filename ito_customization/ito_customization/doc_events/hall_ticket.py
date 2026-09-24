import json
import re
from io import BytesIO

import frappe
from frappe import _
from pypdf import PdfReader, PdfWriter

from frappe.utils.pdf import get_pdf

PRINT_FORMAT = "ITO Hall ticket"  # <- must match your Print Format's exact name/case
PER_PAGE = 3
SINGLE_PAGE_HEIGHT_MM = 141.2
POINTS_PER_MM = 72 / 25.4

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
def download_hall_ticket(name):
    pdf_content = frappe.get_print(
        "Registered Students",
        name,
        PRINT_FORMAT,
        as_pdf=True,
        no_letterhead=True,
    )

    reader = PdfReader(BytesIO(pdf_content))
    writer = PdfWriter()
    target_height = SINGLE_PAGE_HEIGHT_MM * POINTS_PER_MM

    for page in reader.pages:
        page_top = float(page.mediabox.top)
        crop_bottom = max(float(page.mediabox.bottom), page_top - target_height)
        lower_left = (float(page.mediabox.left), crop_bottom)
        upper_right = (float(page.mediabox.right), page_top)
        page.mediabox.lower_left = lower_left
        page.mediabox.upper_right = upper_right
        page.cropbox.lower_left = lower_left
        page.cropbox.upper_right = upper_right
        writer.add_page(page)

    output = BytesIO()
    writer.write(output)
    frappe.local.response.filename = f"{name}.pdf"
    frappe.local.response.filecontent = output.getvalue()
    frappe.local.response.type = "pdf"


@frappe.whitelist()
def get_registered_students_for_portal(selected_class=None):
    """Fetch distinct classes and student records from Registered Students
    for the currently logged in portal user (Customer).
    """
    if frappe.session.user == "Guest":
        frappe.throw(_("Please log in to view hall tickets."), frappe.PermissionError)

    customer_name = frappe.db.get_value("Portal User", {"user": frappe.session.user}, "parent")
    is_system_manager = "System Manager" in frappe.get_roles(frappe.session.user)

    if not customer_name and is_system_manager:
        customer_name = frappe.form_dict.get("school")
        if not customer_name:
            # Fallback to the first customer if in testing/admin mode
            customer_name = frappe.db.get_value("Registered Students", {"not_registered": ("!=", 1)}, "school")

    if not customer_name:
        return {"success": False, "message": "Customer account not found", "classes": [], "students": []}

    # 1. Fetch available classes for this school
    raw_classes = frappe.get_all(
        "Registered Students",
        filters={"school": customer_name, "not_registered": ("!=", 1)},
        distinct=True,
        pluck="class",
    )

    def _class_sort_key(c):
        digits = re.findall(r"\d+", str(c or ""))
        return (0, int(digits[0])) if digits else (1, str(c or "").lower())

    classes = sorted([str(c).strip() for c in raw_classes if c and str(c).strip()], key=_class_sort_key)

    # 2. If selected_class is provided, fetch student details
    students = []
    if selected_class:
        records = frappe.get_all(
            "Registered Students",
            filters={
                "school": customer_name,
                "class": str(selected_class).strip(),
                "not_registered": ("!=", 1),
            },
            fields=["name", "roll_no", "student_name", "class", "mobile_number"],
        )

        def _roll_sort_key(s):
            val = str(s.get("roll_no") or s.get("name") or "")
            return [int(part) if part.isdigit() else part.lower() for part in re.split(r"(\d+)", val)]

        records.sort(key=_roll_sort_key)

        # Retrieve subjects from child table
        for r in records:
            subjects = frappe.get_all(
                "Registered Students CT",
                filters={"parent": r.name, "parenttype": "Registered Students"},
                fields=["subject", "abbr"],
            )
            subj_list = []
            for sub in subjects:
                val = sub.get("abbr") or sub.get("subject")
                if val and val not in subj_list:
                    subj_list.append(val)
            r["subjects"] = subj_list
            students.append(r)

    return {
        "success": True,
        "school": customer_name,
        "classes": classes,
        "students": students,
    }


@frappe.whitelist()
def download_hall_tickets(names=None, selected_class=None):
    """Render the ITO Hall Ticket print format for many students,
    exactly 3 per A3 page, and return one PDF.

    Each page is a fixed container. The 3 tickets on it are
    placed with explicit top/left offsets (absolute positioning) rather
    than being stacked in normal document flow.
    """
    if isinstance(names, str):
        try:
            names = json.loads(names)
        except Exception:
            names = [s.strip() for s in names.split(",") if s.strip()]

    customer_name = frappe.db.get_value("Portal User", {"user": frappe.session.user}, "parent")
    is_system_manager = "System Manager" in frappe.get_roles(frappe.session.user)

    # If selected_class is passed without names, get all registered students for that class
    if not names and selected_class:
        filters = {"class": str(selected_class).strip(), "not_registered": ("!=", 1)}
        if not is_system_manager and customer_name:
            filters["school"] = customer_name
        elif customer_name:
            filters["school"] = customer_name
        names = frappe.get_all("Registered Students", filters=filters, pluck="name")

    if not names:
        frappe.throw(_("No students selected."))

    names_list = list(names) if names is not None else []

    template = frappe.db.get_value("Print Format", PRINT_FORMAT, "html")
    if not template:
        frappe.throw(_("Print Format {0} not found.").format(PRINT_FORMAT))

    # Fetch and validate student documents
    docs = []
    for name in names_list:
        doc = frappe.get_doc("Registered Students", name)
        # Security: portal user may only access their school's students
        if not is_system_manager and customer_name and doc.get("school") != customer_name:
            frappe.throw(
                _("You are not permitted to view hall tickets for student {0}").format(name),
                frappe.PermissionError,
            )
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
