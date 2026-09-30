import frappe

LITTLE_CHAMP_CODES = ["LCEVSO", "LCMO", "LCHO", "LCEO", "LCDO", "LCAO"]

_ORDINAL_EXCEPTIONS = {11: "th", 12: "th", 13: "th"}


def _ordinal(n):
    if n in _ORDINAL_EXCEPTIONS:
        return _ORDINAL_EXCEPTIONS[n]
    return {1: "st", 2: "nd", 3: "rd"}.get(n % 10, "th")


def _format_date(d):
    """2026-08-01 -> '1st Aug., 2026' (matches the sample ticket style)."""
    if not d:
        return ""
    dt = frappe.utils.getdate(d)
    return "{0}{1} {2}., {3}".format(dt.day, _ordinal(dt.day), dt.strftime("%b"), dt.year)


def _extract_code(subject_text):
    """'Little Champ Maths Olympiad (LCMO)' -> 'LCMO'."""
    if not subject_text:
        return ""
    subject_text = str(subject_text)
    if "(" in subject_text and ")" in subject_text:
        return subject_text.split("(")[-1].split(")")[0].strip().upper()
    return subject_text.strip().upper()


def get_little_champ_exam_dates(doc):
    """Return {"LCEVSO": "1st Aug., 2026", "LCMO": "", ...} — one entry per
    Little Champ subject, empty string if the student isn't registered for
    it or no date could be resolved.

    Same relational chain as the main ITO ticket, but scoped to
    is_little_champ = 1 throughout:

        Registered Students.school (Customer)
            -> Exams Summary.customer + is_little_champ=1
                -> Exams Summary.exam_detail  (Yearly Exam Date cycle)
                    -> Yearly Exam Date CT.tg_date_1/2/3
                -> Exam Summary CT.slot_date  (per class + subject)
    """
    school = doc.get("school")
    student_class = str(doc.get("class") or "")

    summary_name = None
    exam_detail = None
    try:
        summary_name = frappe.db.get_value(
            "Exams Summary",
            {"customer": school, "is_little_champ": 1},
            "name",
            order_by="modified desc",
        )
        if summary_name:
            exam_detail = frappe.db.get_value(
                "Exams Summary", summary_name, "exam_detail"
            )
    except Exception:
        frappe.log_error(
            frappe.get_traceback(),
            "Little Champ Hall Ticket: Exams Summary lookup failed",
        )

    # (subject code) -> slot_date this class sat it on
    class_subject_date = {}
    if summary_name:
        try:
            rows = frappe.db.sql(
                """
                select exam_row.subject, exam_row.slot_date, school_subject.abbr
                from `tabExam Summary CT` as exam_row
                left join `tabSchool Subject` as school_subject
                    on school_subject.name = exam_row.subject
                where exam_row.parent = %s and exam_row.class = %s
                """,
                (summary_name, student_class),
                as_dict=True,
            )
        except Exception:
            frappe.log_error(
                frappe.get_traceback(),
                "Little Champ Hall Ticket: Exam Summary CT lookup failed",
            )
            rows = []
        for row in rows:
            # Exam Summary CT.subject stores a School Subject link name; use
            # its abbreviation to match Registered Students.subjects_registered.
            code = str(row.abbr or "").strip().upper() or _extract_code(row.subject)
            if code:
                class_subject_date[code] = row.slot_date

    # Not currently used to cross-check against a candidate list (unlike the
    # main ticket's A/B/C letters) - Little Champ just shows the one actual
    # date. Kept here in case you want the same 3-candidate validation later.
    # code_dates would come from Yearly Exam Date CT filtered by exam_detail.

    result = {}
    for code in LITTLE_CHAMP_CODES:
        matched = False
        for r in (doc.get("subjects_registered") or []):
            abbr = str(r.get("abbr") or "").strip().upper()
            if not abbr:
                abbr = _extract_code(r.get("subject"))
            if abbr == code:
                matched = True
                break

        if matched and class_subject_date.get(code):
            result[code] = _format_date(class_subject_date[code])
        else:
            result[code] = ""

    return result