import json
import re
from io import BytesIO
from frappe.utils import scrub_urls
import frappe
from frappe import _
from pypdf import PdfReader, PdfWriter

from frappe.utils.pdf import get_pdf

DEFAULT_FORMAT = "ITO Hall ticket"  # <- must match your Print Format's exact name/case
LITTLE_CHAMP_FORMAT = "Little Champ Hall Ticket"

POINTS_PER_MM = 72 / 25.4

# --- sizing math (A3 portrait = 297mm x 420mm) ------------------------------
# Every ticket is placed at an EXPLICIT top/left offset inside a page-sized
# container, instead of letting content flow and hoping it breaks in the
# right place. This is what actually guarantees a fixed count-per-page:
# there is no overflow calculation left for the PDF engine to get wrong.
#
# The two formats have different aspect ratios and so get different card
# heights and a different number-per-page. Each format's tickets are
# grouped onto their own pages rather than mixed on one page.
PAGE_WIDTH_MM = 297
PAGE_HEIGHT_MM = 418  # Safe height within 420mm to prevent subpixel page overflow
CARD_WIDTH_MM = 287
LEFT_OFFSET_MM = (PAGE_WIDTH_MM - CARD_WIDTH_MM) / 2  # centers the card horizontally

FORMAT_SIZING = {
    DEFAULT_FORMAT: {
        "card_height_mm": 131.2,
        "gap_mm": 8.2,
        "per_page": 3,
        "top_margin_mm": 5,       # fixed - matches the originally tuned layout
        "single_crop_mm": 141.2,  # crop height for the single-ticket download
    },
    LITTLE_CHAMP_FORMAT: {
        "card_height_mm": 143.5,
        "gap_mm": 8.0,
        "per_page": 2,            # taller template - 3 doesn't fit an A3 page
        "top_margin_mm": None,    # None -> vertically center the group instead
        "single_crop_mm": 153.5,  # same +10mm buffer convention as the ITO one
    },
}

# --- backward-compatible aliases -------------------------------------------
# These flat names existed before the multi-format refactor. Kept here,
# pointing at the ITO (default) sizing, in case anything else in the app
# imports them directly by name.
PRINT_FORMAT = DEFAULT_FORMAT
PER_PAGE = FORMAT_SIZING[DEFAULT_FORMAT]["per_page"]
CARD_HEIGHT_MM = FORMAT_SIZING[DEFAULT_FORMAT]["card_height_mm"]
GAP_MM = FORMAT_SIZING[DEFAULT_FORMAT]["gap_mm"]
TOP_MARGIN_MM = FORMAT_SIZING[DEFAULT_FORMAT]["top_margin_mm"]
SINGLE_PAGE_HEIGHT_MM = FORMAT_SIZING[DEFAULT_FORMAT]["single_crop_mm"]


def _resolve_format(doc):
    """Little Champ format if the student's school (Customer) has
    custom_is_little_champ checked, else the default ITO format."""
    school = doc.get("school")
    if not school:
        return DEFAULT_FORMAT
    try:
        is_little_champ = frappe.db.get_value(
            "Customer", school, "custom_is_little_champ"
        )
    except Exception:
        frappe.log_error(
            frappe.get_traceback(), "Hall Ticket: custom_is_little_champ lookup failed"
        )
        is_little_champ = 0
    return LITTLE_CHAMP_FORMAT if is_little_champ else DEFAULT_FORMAT


def _separator_html(top_mm, gap_mm):
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
        '</div>'.format(LEFT_OFFSET_MM, top_mm, CARD_WIDTH_MM, gap_mm)
    )


def _build_pages(card_htmls, sizing):
    """Group card_htmls into pages of sizing['per_page'], each card placed
    at an explicit absolute position inside a fixed-size page container.
    Returns a list of page HTML strings (no page-break markup - that is
    added once, across all groups, by the caller)."""
    per_page = sizing["per_page"]
    card_height_mm = sizing["card_height_mm"]
    gap_mm = sizing["gap_mm"]

    pages = [card_htmls[i:i + per_page] for i in range(0, len(card_htmls), per_page)]

    total_content_height = per_page * card_height_mm + (per_page - 1) * gap_mm
    if sizing["top_margin_mm"] is not None:
        top_margin = sizing["top_margin_mm"]
    else:
        top_margin = (PAGE_HEIGHT_MM - total_content_height) / 2

    page_blocks = []
    for page_cards in pages:
        positioned = []
        for k, card_html in enumerate(page_cards):
            top = top_margin + k * (card_height_mm + gap_mm)
            positioned.append(
                '<div style="position:absolute;left:{0}mm;top:{1}mm;'
                'width:{2}mm;height:{3}mm;overflow:hidden;">{4}</div>'.format(
                    LEFT_OFFSET_MM, top, CARD_WIDTH_MM, card_height_mm, card_html
                )
            )
            if k < len(page_cards) - 1:
                gap_top = top_margin + (k + 1) * card_height_mm + k * gap_mm
                positioned.append(_separator_html(gap_top, gap_mm))

        page_style = (
            "position:relative;width:{0}mm;height:{1}mm;"
            "box-sizing:border-box;overflow:hidden;"
        ).format(PAGE_WIDTH_MM, PAGE_HEIGHT_MM)

        page_blocks.append(
            '<div class="page-container" style="{0}">{1}</div>'.format(
                page_style, "".join(positioned)
            )
        )

    return page_blocks


@frappe.whitelist()
def download_hall_ticket(name):
    doc = frappe.get_doc("Registered Students", name)
    fmt = _resolve_format(doc)
    sizing = FORMAT_SIZING[fmt]

    pdf_content = frappe.get_print(
        "Registered Students",
        name,
        fmt,
        as_pdf=True,
        no_letterhead=True,
    )

    reader = PdfReader(BytesIO(pdf_content))
    writer = PdfWriter()
    target_height = sizing["single_crop_mm"] * POINTS_PER_MM

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
    """Render hall tickets for many students and return one PDF.

    Each student is routed to the ITO or Little Champ print format based
    on their school's custom_is_little_champ flag. Tickets are grouped by
    format (ITO: 3/page, Little Champ: 2/page - it's a taller template) so
    every page only ever contains same-size cards.
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

    # Cache each print format's HTML so we only fetch it once.
    template_cache = {}

    def _get_template(fmt):
        if fmt not in template_cache:
            html = frappe.db.get_value("Print Format", fmt, "html")
            if not html:
                frappe.throw(_("Print Format {0} not found.").format(fmt))
            template_cache[fmt] = html
        return template_cache[fmt]

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

    # Split into format groups, preserving the sorted order within each.
    grouped = {DEFAULT_FORMAT: [], LITTLE_CHAMP_FORMAT: []}
    for doc in docs:
        grouped[_resolve_format(doc)].append(doc)

    printed_so_far = 0
    all_page_blocks = []

    # ITO tickets first, then Little Champ - swap this tuple's order if you
    # want Little Champ tickets to print first.
    for fmt in (DEFAULT_FORMAT, LITTLE_CHAMP_FORMAT):
        fmt_docs = grouped[fmt]
        if not fmt_docs:
            continue

        template = _get_template(fmt)
        sizing = FORMAT_SIZING[fmt]

        card_htmls = []
        for doc in fmt_docs:
            printed_so_far += 1
            card_htmls.append(
                frappe.render_template(
                    template,
                    {
                        "doc": doc,
                        "frappe": frappe,
                        "page_label": _("Page {0} of {1}").format(printed_so_far, total),
                    },
                )
            )

        all_page_blocks.extend(_build_pages(card_htmls, sizing))

    # Join pages with an explicit page-break between them (not on the last).
    # Each page div is already exactly one full physical page, so this
    # break always lands cleanly - no flow/overflow math involved.
    joined_pages = ""
    for i, block in enumerate(all_page_blocks):
        joined_pages += block
        if i < len(all_page_blocks) - 1:
            joined_pages += '<div style="page-break-after:always;"></div>'

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
        + joined_pages +
        "</body></html>"
    )
    html = scrub_urls(html)
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