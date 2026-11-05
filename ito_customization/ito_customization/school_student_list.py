"""School-wide student list for the ITO Registration wizard.

The school uploads one sheet holding every class. Registered Students (source "School") is the single
source of truth: the Exam Summary counts and the registration fee are derived from it on the server and
nothing the browser sends for them is trusted.
"""

import base64
import csv
import io
import re
from collections import defaultdict

import frappe
from frappe import _
from frappe.utils import cint

SOURCE_SCHOOL = "School"
SOURCE_LINK = "Student Portal Link"
SOURCE_LABELS = {SOURCE_SCHOOL: "Paid via School", SOURCE_LINK: "Paid individually via Link"}
# Records created before registration_source existed and not made through the portal link.
SOURCE_UNKNOWN = ""
SOURCE_UNKNOWN_LABEL = "Not recorded"

MAX_FILE_BYTES = 5 * 1024 * 1024
MAX_ROWS = 1500
# One subject in every four is free for a student (the "3 + 1 free" rule of the Student List grid).
SUBJECTS_PER_FREE = 4

CLASS_PATTERN = re.compile(r"^(?:class|std|standard)?\s*0*(\d{1,2})\s*(?:st|nd|rd|th)?$", re.IGNORECASE)
YES_VALUES = {"1", "y", "yes", "true", "x", "✓", "✔", "tick", "ticked"}
NO_VALUES = {"", "0", "n", "no", "false", "-", "–", "—", "na", "n/a"}
NAME_HEADERS = {"studentname", "name", "nameofstudent", "nameofthestudent"}
CLASS_HEADERS = {"class", "std", "standard", "classstd"}
MOBILE_HEADERS = {"mobileno", "mobile", "mobilenumber", "phone", "phoneno", "contact", "contactno", "whatsappno"}
SERIAL_HEADERS = {"srno", "sr", "serialno", "serial", "slno", "sno"}


# --------------------------------------------------------------------------------------------------
# School and year
# --------------------------------------------------------------------------------------------------

def get_session_school() -> frappe._dict:
    """The school (Customer) of the logged-in portal user. The client never names the school."""
    if frappe.session.user == "Guest":
        frappe.throw(_("Please log in to continue."), frappe.PermissionError)

    customer = frappe.db.get_value("Portal User", {"user": frappe.session.user}, "parent")
    if not customer:
        frappe.throw(_("No school is linked to this user."), frappe.PermissionError)

    school = frappe.db.get_value(
        "Customer",
        customer,
        [
            "name",
            "customer_name",
            "custom_ito_school_code",
            "custom_current_academic_year",
            "custom_registration_date",
            "custom_application_deadline",
        ],
        as_dict=True,
    )
    school.academic_year = resolve_academic_year(school.custom_current_academic_year)
    return school


def resolve_academic_year(raw_year: str | None) -> str | None:
    year = (raw_year or "").strip()
    if year and not year.startswith("AY-"):
        parts = year.replace("-", "/").split("/")
        year = f"AY-{parts[0]}/{parts[1]}" if len(parts) == 2 else f"AY-{year}"
    if year and frappe.db.exists("School Academic Year", year):
        return year
    # Same fallback as the Student List page: the newest academic year on record.
    return frappe.db.get_value("School Academic Year", {}, "name", order_by="creation desc")


def is_locked(school) -> bool:
    """The list can be uploaded or replaced until the application deadline (the whole deadline day is
    still open). Submitting the registration does not lock it."""
    deadline = school.get("custom_application_deadline")
    if not deadline:
        return False
    return frappe.utils.getdate(frappe.utils.nowdate()) > frappe.utils.getdate(deadline)


# --------------------------------------------------------------------------------------------------
# Reading the uploaded sheet
# --------------------------------------------------------------------------------------------------

def _normalise_header(value) -> str:
    return re.sub(r"[^a-z0-9]", "", str(value or "").lower())


def _clean(value) -> str:
    if value is None:
        return ""
    if isinstance(value, float) and value.is_integer():
        value = int(value)
    return str(value).strip()


def read_sheet(filedata: str, filename: str) -> list[list[str]]:
    """Return the first sheet of a CSV or XLSX upload as rows of strings."""
    if not filedata:
        frappe.throw(_("No file data provided."))

    if "," in filedata[:100] and filedata.startswith("data:"):
        filedata = filedata.split(",", 1)[1]
    try:
        content = base64.b64decode(filedata, validate=False)
    except Exception:
        frappe.throw(_("The uploaded file could not be read."))
    if not content:
        frappe.throw(_("The uploaded file is empty."))
    if len(content) > MAX_FILE_BYTES:
        frappe.throw(_("The file is larger than {0} MB.").format(MAX_FILE_BYTES // (1024 * 1024)))

    extension = (filename or "").rsplit(".", 1)[-1].lower() if "." in (filename or "") else "csv"
    if extension in ("csv", "txt"):
        try:
            text = content.decode("utf-8-sig")
        except UnicodeDecodeError:
            text = content.decode("latin-1")
        return [[_clean(cell) for cell in row] for row in csv.reader(io.StringIO(text))]

    if extension == "xlsx":
        from openpyxl import load_workbook

        try:
            workbook = load_workbook(io.BytesIO(content), read_only=True, data_only=True)
            sheet = workbook.worksheets[0]
            rows = [[_clean(cell) for cell in row] for row in sheet.iter_rows(values_only=True)]
            workbook.close()
            return rows
        except Exception:
            frappe.throw(_("The Excel file could not be read. Save it as .xlsx or .csv and upload it again."))

    frappe.throw(_("Upload an Excel (.xlsx) or CSV file."))


def get_subject_columns(school) -> list[dict]:
    """Subjects offered to this school, as {name, abbr}."""
    from ito_customization.ito_customization.api import get_subjects_for_year

    subjects = get_subjects_for_year(school.academic_year, 0, customer_name=school.name)
    seen, result = set(), []
    for subject in subjects:
        if subject["name"] in seen:
            continue
        seen.add(subject["name"])
        result.append({"name": subject["name"], "abbr": subject["abbr"]})
    return result


def parse_student_sheet(rows: list[list[str]], subjects: list[dict]) -> dict:
    """Validate a sheet. Nothing is saved; every row carries its own errors."""
    result = {"rows": [], "errors": [], "warnings": [], "subject_columns": []}

    header_index = next((i for i, row in enumerate(rows) if any(row)), None)
    if header_index is None:
        result["errors"].append(_("The file has no rows."))
        return result

    header = rows[header_index]
    lookup = {}
    for subject in subjects:
        for key in (subject["abbr"], subject["name"]):
            if key:
                lookup.setdefault(_normalise_header(key), subject)

    columns = {}
    subject_by_column = {}
    unknown = []
    for index, title in enumerate(header):
        key = _normalise_header(title)
        if not key:
            continue
        if key in NAME_HEADERS and "name" not in columns:
            columns["name"] = index
        elif key in CLASS_HEADERS and "class" not in columns:
            columns["class"] = index
        elif key in MOBILE_HEADERS and "mobile" not in columns:
            columns["mobile"] = index
        elif key in SERIAL_HEADERS and "serial" not in columns:
            columns["serial"] = index
        elif key in lookup:
            subject_by_column.setdefault(index, lookup[key])
        else:
            unknown.append(title)

    for required, label in (("name", "Student Name"), ("class", "Class")):
        if required not in columns:
            result["errors"].append(_("Required column missing: {0}.").format(label))
    if not subject_by_column:
        result["errors"].append(
            _("No subject columns found. Use these headers: {0}.").format(
                ", ".join(subject["abbr"] for subject in subjects)
            )
        )
    if unknown:
        result["warnings"].append(
            _("These columns are not recognised and were ignored: {0}.").format(", ".join(unknown))
        )
    if result["errors"]:
        return result

    result["subject_columns"] = [subject["abbr"] for subject in subject_by_column.values()]
    seen_students = {}

    for offset, row in enumerate(rows[header_index + 1:], start=header_index + 2):
        def cell(key):
            index = columns.get(key)
            return row[index] if index is not None and index < len(row) else ""

        marks = {
            index: (row[index].strip().lower() if index < len(row) else "")
            for index in subject_by_column
        }
        if not any(row) or not (cell("name") or cell("class") or any(m not in NO_VALUES for m in marks.values())):
            continue
        if len(result["rows"]) >= MAX_ROWS:
            result["errors"].append(_("The sheet has more than {0} students. Split it and upload in parts.").format(MAX_ROWS))
            break

        errors = []
        name = " ".join(cell("name").split()).upper()
        if not name:
            errors.append(_("Student name is missing."))
        elif len(name) > 100:
            errors.append(_("Student name is longer than 100 characters."))

        class_name = None
        raw_class = cell("class")
        match = CLASS_PATTERN.match(raw_class.strip()) if raw_class else None
        if match and 1 <= int(match.group(1)) <= 12:
            class_name = str(int(match.group(1)))
            if not frappe.db.exists("Class", class_name):
                errors.append(_("Class {0} is not set up.").format(class_name))
        else:
            errors.append(_("Class must be a number from 1 to 12 (found '{0}').").format(raw_class))

        mobile = cell("mobile")
        digits = re.sub(r"[\s+\-()]", "", mobile)
        if mobile and not (digits.isdigit() and 7 <= len(digits) <= 15):
            errors.append(_("Mobile number '{0}' is not valid.").format(mobile))

        chosen = []
        for index, mark in marks.items():
            if mark in YES_VALUES:
                chosen.append(subject_by_column[index])
            elif mark not in NO_VALUES:
                errors.append(
                    _("{0}: use Yes/No or 1/0 (found '{1}').").format(subject_by_column[index]["abbr"], row[index])
                )
        if not chosen:
            errors.append(_("Select at least one subject."))

        if name and class_name:
            key = (name, class_name, digits)
            if key in seen_students:
                errors.append(_("Duplicate of row {0} (same name, class and mobile).").format(seen_students[key]))
            else:
                seen_students[key] = offset

        result["rows"].append(
            {
                "row": offset,
                "serial_no": cell("serial"),
                "student_name": name,
                "class": class_name or raw_class,
                "mobile": mobile,
                "subjects": [subject["abbr"] for subject in chosen],
                "subject_names": [subject["name"] for subject in chosen],
                "errors": errors,
            }
        )

    if not result["rows"] and not result["errors"]:
        result["errors"].append(_("The file has no student rows."))
    return result


def _summarise_preview(parsed: dict, school) -> dict:
    invalid = [row for row in parsed["rows"] if row["errors"]]
    by_class = defaultdict(int)
    for row in parsed["rows"]:
        if not row["errors"]:
            by_class[row["class"]] += 1
    return {
        "rows": parsed["rows"],
        "errors": parsed["errors"],
        "warnings": parsed["warnings"],
        "subject_columns": parsed["subject_columns"],
        "total_rows": len(parsed["rows"]),
        "valid_rows": len(parsed["rows"]) - len(invalid),
        "invalid_rows": len(invalid),
        "by_class": dict(sorted(by_class.items(), key=lambda item: int(item[0]))),
        "can_save": bool(parsed["rows"]) and not invalid and not parsed["errors"] and not is_locked(school),
        "locked": is_locked(school),
        "existing_students": count_school_students(school),
    }


# --------------------------------------------------------------------------------------------------
# Stored students
# --------------------------------------------------------------------------------------------------

def _student_rows(school_name: str, academic_year: str | None) -> list[frappe._dict]:
    """One row per (student, subject) for the school's active students, with the student's source."""
    year_filter = "and rs.academic_year = %(year)s" if academic_year else ""
    return frappe.db.sql(
        f"""
        select rs.name, rs.student_name, rs.class, rs.roll_no, rs.mobile_number,
            case
                when ifnull(rs.registration_source, '') != '' then rs.registration_source
                when rs.from_student_portal = 1 then %(link)s
                else ''
            end as source,
            ct.subject, ct.abbr
        from `tabRegistered Students` rs
        left join `tabRegistered Students CT` ct
            on ct.parent = rs.name and ct.parenttype = 'Registered Students'
        where rs.school = %(school)s and rs.not_registered = 0 and rs.docstatus < 2 {year_filter}
        order by rs.creation, rs.name, ct.idx
        """,
        {"school": school_name, "year": academic_year, "link": SOURCE_LINK},
        as_dict=True,
    )


def count_school_students(school) -> int:
    return len({row.name for row in _student_rows(school.name, school.academic_year) if row.source == SOURCE_SCHOOL})


def derive_registration(school) -> dict:
    """Counts and fee basis derived from the school's own students.

    Each student is counted once per class and subject. The free subjects follow the per-student rule:
    a student with n subjects gets n // 4 of them free.
    """
    subjects_of = defaultdict(dict)
    class_of = {}
    for row in _student_rows(school.name, school.academic_year):
        if row.source != SOURCE_SCHOOL:
            continue
        class_of[row.name] = str(row["class"] or "")
        if row.subject:
            subjects_of[row.name][row.subject] = row.abbr

    counts = defaultdict(int)
    entries = free = 0
    for name, subjects in subjects_of.items():
        entries += len(subjects)
        free += len(subjects) // SUBJECTS_PER_FREE
        for subject in subjects:
            counts[(class_of[name], subject)] += 1

    return {
        "students": len(class_of),
        "entries": entries,
        "free": free,
        "paid": entries - free,
        "counts": dict(counts),
    }


def get_fee_basis(customer: str) -> dict:
    """Server-side fee basis for a customer, used by the payment code. has_list is False when the
    school has no list (older registrations), and the caller then keeps its previous behaviour."""
    year = resolve_academic_year(frappe.db.get_value("Customer", customer, "custom_current_academic_year"))
    derived = derive_registration(frappe._dict(name=customer, academic_year=year))
    return {
        "has_list": derived["students"] > 0,
        "students": derived["students"],
        "entries": derived["entries"],
        "free": derived["free"],
        "paid": derived["paid"],
    }


# --------------------------------------------------------------------------------------------------
# Exam Summary
# --------------------------------------------------------------------------------------------------

def _get_or_create_exams_summary(customer: str):
    name = frappe.db.get_value("Exams Summary", {"customer": customer, "is_little_champ": 0, "is_wof": 0})
    if name:
        return frappe.get_doc("Exams Summary", name)

    year = resolve_academic_year(frappe.db.get_value("Customer", customer, "custom_current_academic_year"))
    filters = {"docstatus": 1, "is_little_champ": 0}
    if year:
        filters["academic_year"] = year
    exam_detail = frappe.db.get_value("Yearly Exam Date", filters, "name", order_by="creation desc")
    if not exam_detail:
        frappe.throw(_("No submitted exam schedule is available for this school."))
    doc = frappe.new_doc("Exams Summary")
    doc.customer = customer
    doc.exam_detail = exam_detail
    doc.is_little_champ = 0
    doc.is_wof = 0
    return doc


def _resolve_subject(key, known: dict) -> str | None:
    """Match a subject key sent by the browser to a School Subject, without creating anything."""
    key = str(key or "").strip()
    if key in known.values():
        return key
    return known.get(key.lower())


def sync_exam_summary(customer: str, client_exams: dict | None = None) -> None:
    """Rebuild the ITO Exams Summary from the school's student list.

    Counts always come from the list. The browser may only supply teacher name, WhatsApp number and the
    exam date; those are matched to a class and subject that the list produced. A school without a
    list keeps its stored counts.
    """
    school = frappe._dict(
        name=customer,
        academic_year=resolve_academic_year(frappe.db.get_value("Customer", customer, "custom_current_academic_year")),
    )
    derived = derive_registration(school)
    doc = _get_or_create_exams_summary(customer)

    known = {}
    for row in frappe.get_all("School Subject", fields=["name", "abbr"]):
        known[row.name.lower()] = row.name
        if row.abbr:
            known.setdefault(row.abbr.lower(), row.name)

    # Teacher details and dates: what is already stored, then what the browser sent.
    details = {}
    slot_dates = {}
    for row in doc.exam_summary:
        details[(str(row.get("class") or ""), row.subject)] = {
            "teacher_name": row.teacher_name,
            "whatsapp_no": row.whatsapp_no,
            "slot_date": row.slot_date,
        }
        if row.slot_date and row.subject not in slot_dates:
            slot_dates[row.subject] = row.slot_date

    for key, rows in (client_exams or {}).items():
        subject = _resolve_subject(key, known)
        if not subject or not isinstance(rows, list):
            continue
        for row in rows:
            if not isinstance(row, dict):
                continue
            entry = details.setdefault((str(row.get("class") or ""), subject), {})
            entry["teacher_name"] = row.get("teacher_name") or ""
            entry["whatsapp_no"] = row.get("whatsapp") or row.get("whatsapp_no") or ""
            if row.get("slot_date"):
                entry["slot_date"] = row["slot_date"]
                slot_dates[subject] = row["slot_date"]

    if derived["students"]:
        doc.exam_summary = []
        for (class_name, subject), count in sorted(
            derived["counts"].items(), key=lambda item: (item[0][1], int(item[0][0] or 0))
        ):
            entry = details.get((class_name, subject), {})
            doc.append(
                "exam_summary",
                {
                    "subject": subject,
                    "class": class_name,
                    "teacher_name": entry.get("teacher_name"),
                    "whatsapp_no": entry.get("whatsapp_no"),
                    "slot_date": entry.get("slot_date") or slot_dates.get(subject) or None,
                    "no_of_students": count,
                },
            )
    else:
        # No list: never touch the counts, only the teacher fields of rows that already exist.
        for row in doc.exam_summary:
            entry = details.get((str(row.get("class") or ""), row.subject), {})
            row.teacher_name = entry.get("teacher_name")
            row.whatsapp_no = entry.get("whatsapp_no")
            row.slot_date = entry.get("slot_date") or row.slot_date

    if doc.is_new():
        doc.insert(ignore_permissions=True)
    else:
        doc.save(ignore_permissions=True)


# --------------------------------------------------------------------------------------------------
# Whitelisted API
# --------------------------------------------------------------------------------------------------

@frappe.whitelist()
def get_student_list_state():
    """What the wizard needs to decide whether the list is saved and what it implies."""
    school = get_session_school()
    derived = derive_registration(school)
    return {
        "has_list": derived["students"] > 0,
        "locked": is_locked(school),
        "students": derived["students"],
        "entries": derived["entries"],
        "free": derived["free"],
        "paid": derived["paid"],
        "academic_year": school.academic_year,
    }


@frappe.whitelist()
def download_school_student_template():
    """CSV template: Sr. No., Student Name, Class, Mobile No. and one column per subject."""
    school = get_session_school()
    subjects = get_subject_columns(school)
    if not subjects:
        frappe.throw(_("No subjects are configured for this academic year."))

    buffer = io.StringIO()
    writer = csv.writer(buffer)
    writer.writerow(["Sr. No.", "Student Name", "Class", "Mobile No."] + [s["abbr"] for s in subjects])
    writer.writerow(["1", "DEMO STUDENT", "5", "9999999999"] + ["Yes"] + ["No"] * (len(subjects) - 1))
    writer.writerow(["2", "DEMO STUDENT TWO", "8", ""] + ["Yes"] * min(2, len(subjects)) + ["No"] * max(len(subjects) - 2, 0))
    return {"filename": f"ITO_Student_List_Template_{frappe.utils.today()}.csv", "content": buffer.getvalue()}


@frappe.whitelist()
def preview_school_student_list(filedata=None, filename=None):
    """Validate an upload and return it row by row. Saves nothing."""
    school = get_session_school()
    parsed = parse_student_sheet(read_sheet(filedata, filename), get_subject_columns(school))
    return _summarise_preview(parsed, school)


@frappe.whitelist()
def save_school_student_list(filedata=None, filename=None, replace=0):
    """Validate the upload again on the server and save it as the school's student list.

    The file is re-read here rather than trusting a preview from the browser. An existing list is
    replaced only when the school confirms (replace=1).
    """
    school = get_session_school()
    if is_locked(school):
        frappe.throw(_("The application deadline has passed, so the student list can no longer be changed."))

    parsed = parse_student_sheet(read_sheet(filedata, filename), get_subject_columns(school))
    preview = _summarise_preview(parsed, school)
    if not preview["can_save"]:
        return {"success": False, "message": _("Fix the errors in the file before saving."), "preview": preview}

    existing = preview["existing_students"]
    if existing and not cint(replace):
        return {"success": False, "needs_confirmation": True, "existing_students": existing}

    if not school.custom_ito_school_code:
        frappe.throw(_("ITO School Code is required before registering students."))
    code = school.custom_ito_school_code.strip().upper()

    flags = frappe.flags.ignore_permissions
    frappe.flags.ignore_permissions = True
    try:
        _retire_school_students(school)
        for row in parsed["rows"]:
            student = frappe.new_doc("Registered Students")
            student.student_name = row["student_name"]
            student.school = school.name
            student.full_name = school.customer_name
            student.ito_school_code = code
            student.academic_year = school.academic_year
            student.set("class", row["class"])
            student.mobile_number = row["mobile"]
            student.serial_number = row["serial_no"]
            student.registration_source = SOURCE_SCHOOL
            for subject_name, abbr in zip(row["subject_names"], row["subjects"]):
                student.append("subjects_registered", {"subject": subject_name, "abbr": abbr})
            student.insert(ignore_permissions=True)

        sync_exam_summary(school.name)
        if school.custom_registration_date:
            # The fee follows the list, so a changed list reopens payment; the school submits again.
            frappe.db.set_value("Customer", school.name, "custom_registration_date", None)
    finally:
        frappe.flags.ignore_permissions = flags

    return {"success": True, "students": len(parsed["rows"]), "state": get_student_list_state()}


def _retire_school_students(school) -> None:
    """Remove the school's previous list. A student who already has a roll number is kept but marked
    not registered so hall tickets issued to that roll number stay traceable."""
    names = frappe.get_all(
        "Registered Students",
        filters={
            "school": school.name,
            "registration_source": SOURCE_SCHOOL,
            "academic_year": school.academic_year,
            "not_registered": 0,
        },
        fields=["name", "roll_no"],
    )
    for row in names:
        if row.roll_no:
            frappe.db.set_value("Registered Students", row.name, "not_registered", 1)
        else:
            frappe.delete_doc("Registered Students", row.name, force=True, ignore_permissions=True)


@frappe.whitelist()
def get_school_student_list(filters=None):
    """Read-only list of every student of the logged-in school, from both channels."""
    school = get_session_school()
    filters = frappe.parse_json(filters) if isinstance(filters, str) else (filters or {})
    class_filter = str(filters.get("class") or "").strip()
    subject_filter = str(filters.get("subject") or "").strip().upper()
    source_filter = str(filters.get("source") or "").strip()

    students = {}
    for row in _student_rows(school.name, school.academic_year):
        student = students.setdefault(
            row.name,
            {
                "name": row.name,
                "roll_no": row.roll_no or "",
                "student_name": row.student_name,
                "class": str(row["class"] or ""),
                "source": row.source,
                "source_label": SOURCE_LABELS.get(row.source, SOURCE_UNKNOWN_LABEL),
                "subjects": [],
            },
        )
        if row.subject:
            student["subjects"].append(row.abbr or row.subject)

    rows = list(students.values())
    # Totals are for the whole school so the filters narrow the table, not the headline numbers.
    totals = {
        "school": sum(1 for s in rows if s["source"] == SOURCE_SCHOOL),
        "link": sum(1 for s in rows if s["source"] == SOURCE_LINK),
        "not_recorded": sum(1 for s in rows if s["source"] not in SOURCE_LABELS),
        "total": len(rows),
    }
    classes = sorted({s["class"] for s in rows if s["class"]}, key=lambda value: (not value.isdigit(), int(value) if value.isdigit() else 0, value))
    subjects = sorted({abbr for s in rows for abbr in s["subjects"]})

    if class_filter:
        rows = [s for s in rows if s["class"] == class_filter]
    if subject_filter:
        rows = [s for s in rows if subject_filter in {abbr.upper() for abbr in s["subjects"]}]
    if source_filter:
        rows = [s for s in rows if (s["source"] if s["source"] in SOURCE_LABELS else SOURCE_UNKNOWN) == (source_filter if source_filter in SOURCE_LABELS else SOURCE_UNKNOWN)]

    rows.sort(key=lambda s: (int(s["class"]) if s["class"].isdigit() else 99, s["student_name"]))
    return {
        "students": rows,
        "totals": totals,
        "shown": len(rows),
        "filters": {"classes": classes, "subjects": subjects, "sources": list(SOURCE_LABELS)},
    }
