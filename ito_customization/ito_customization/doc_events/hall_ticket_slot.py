import frappe


EXAM_CODES = [
    "ISO", "IMO", "EIO", "GKIO", "ICO", "IDO",
    "NESO", "NSSO", "NLRO", "NHO", "AO", "CIO"
]

SLOT_LETTERS = ["A", "B", "C"]


def _extract_code(subject_text):
    """Extract exam code from subject text."""
    if not subject_text:
        return ""

    subject_text = str(subject_text)

    if "(" in subject_text and ")" in subject_text:
        return (
            subject_text
            .split("(")[-1]
            .split(")")[0]
            .strip()
            .upper()
        )

    return subject_text.strip().upper()


def get_exam_slot_summary(doc):
    """
    Return exam slot information and selected dates for the student.

    Example:

    {
        "slots": {
            "A": ["CIO"],
            "B": ["ICO"],
            "C": ["IDO"]
        },
        "dates": {
            "CIO": "2026-10-10",
            "ICO": "2026-10-12",
            "IDO": "2026-10-15"
        }
    }
    """

    school = doc.get("school")
    student_class = str(doc.get("class") or "")

    # ------------------------------------------------------------
    # Find Exams Summary for this school
    # ------------------------------------------------------------

    summary_name = None
    exam_detail = None

    try:
        summary_name = frappe.db.get_value(
            "Exams Summary",
            {"customer": school},
            "name",
            order_by="modified desc",
        )

        if summary_name:
            exam_detail = frappe.db.get_value(
                "Exams Summary",
                summary_name,
                "exam_detail",
            )

    except Exception:
        frappe.log_error(
            frappe.get_traceback(),
            "Hall Ticket: Exams Summary lookup failed",
        )


    # ------------------------------------------------------------
    # Get selected slot_date for this class
    # ------------------------------------------------------------

    class_subject_date = {}

    if summary_name:
        try:
            summary_rows = frappe.db.sql(
                """
                SELECT
                    subject,
                    slot_date
                FROM `tabExam Summary CT`
                WHERE
                    parent = %s
                    AND class = %s
                """,
                (summary_name, student_class),
                as_dict=True,
            )

        except Exception:
            frappe.log_error(
                frappe.get_traceback(),
                "Hall Ticket: Exam Summary CT lookup failed",
            )
            summary_rows = []

        for row in summary_rows:
            code = _extract_code(row.subject)

            if code:
                class_subject_date[code] = row.slot_date


    # ------------------------------------------------------------
    # Get candidate dates from Yearly Exam Date
    # ------------------------------------------------------------

    code_dates = {}

    if exam_detail:
        try:
            date_rows = frappe.db.sql(
                """
                SELECT
                    subject,
                    tg_date_1,
                    tg_date_2,
                    tg_date_3
                FROM `tabYearly Exam Date CT`
                WHERE parent = %s
                """,
                (exam_detail,),
                as_dict=True,
            )

        except Exception:
            frappe.log_error(
                frappe.get_traceback(),
                "Hall Ticket: Yearly Exam Date CT lookup failed",
            )
            date_rows = []

        for row in date_rows:
            code = _extract_code(row.subject)

            if code and code not in code_dates:
                code_dates[code] = [
                    row.tg_date_1,
                    row.tg_date_2,
                    row.tg_date_3,
                ]


    # ------------------------------------------------------------
    # Build slot groups
    # ------------------------------------------------------------

    slots = {
        "A": [],
        "B": [],
        "C": [],
    }

    unresolved = []

    registered_codes = set()

    for row in doc.get("subjects_registered") or []:

        abbr = str(row.get("abbr") or "").strip().upper()

        if not abbr:
            abbr = _extract_code(row.get("subject"))

        if abbr:
            registered_codes.add(abbr)


    for code in EXAM_CODES:

        if code not in registered_codes:
            continue

        selected_date = class_subject_date.get(code)

        targets = code_dates.get(code, [])

        letter = ""

        for idx, target_date in enumerate(targets):

            if (
                selected_date
                and target_date
                and str(selected_date)[:10]
                == str(target_date)[:10]
            ):
                letter = SLOT_LETTERS[idx]
                break

        if letter:
            slots[letter].append(code)
        else:
            unresolved.append(code)


    if unresolved:
        slots["Other"] = unresolved


    # ------------------------------------------------------------
    # Return both slot information and selected dates
    # ------------------------------------------------------------

    return {
        "slots": slots,
        "dates": class_subject_date,
    }