from unittest.mock import MagicMock, patch

from frappe.tests import UnitTestCase
from ito_customization.ito_customization.doctype.registered_students import student_import


class UnitTestStudentImport(UnitTestCase):
	def test_extract_students_enqueues_user_scoped_background_job(self):
		cache = MagicMock()
		cache.get_value.return_value = None

		with (
			patch.object(student_import, "check_import_access"),
			patch.object(student_import, "get_private_file"),
			patch.object(student_import.frappe, "generate_hash", return_value="a" * 32),
			patch.object(student_import.frappe, "cache", return_value=cache),
			patch.object(student_import.frappe, "enqueue") as enqueue,
			patch.object(student_import.frappe, "session", student_import.frappe._dict(user="teacher@example.com")),
		):
			result = student_import.extract_students(
				"private-file", "Test School", "2026-2027"
			)

		self.assertEqual(result, {"job_id": "a" * 32})
		cache.set_value.assert_called_once_with(
			"registered_students_extraction::teacher@example.com::" + "a" * 32,
			{
				"status": "queued",
				"current_page": 0,
				"queue_job_id": "registered-student-extraction::" + "a" * 32,
			},
			expires_in_sec=student_import.EXTRACTION_RESULT_TTL,
		)
		enqueue.assert_called_once()
		self.assertEqual(enqueue.call_args.kwargs["queue"], "long")
		self.assertEqual(enqueue.call_args.kwargs["timeout"], 1800)

	def test_extraction_status_is_scoped_to_current_user(self):
		cache = MagicMock()
		cache.get_value.return_value = {"status": "processing"}

		with (
			patch.object(student_import.frappe, "cache", return_value=cache),
			patch.object(student_import.frappe, "session", student_import.frappe._dict(user="teacher@example.com")),
		):
			result = student_import.get_student_extraction_status("b" * 32)

		self.assertEqual(result, {"status": "processing"})
		cache.get_value.assert_called_once_with(
			"registered_students_extraction::teacher@example.com::" + "b" * 32
		)


SUBJECTS = [
	{"name": "International Science Olympiad (ISO)", "abbr": "ISO"},
	{"name": "International Maths Olympiad (IMO)", "abbr": "IMO"},
]


class UnitTestStudentExtractionLogic(UnitTestCase):
	def test_marked_subjects_accepts_current_and_legacy_agent_schema(self):
		lookup = student_import.subject_index(SUBJECTS)
		current = {"marked_subjects": ["ISO"]}
		legacy = {"subject_cells": [
			{"abbr": "IMO", "status": "marked"},
			{"abbr": "ISO", "status": "blank"},
		]}
		self.assertEqual(student_import.provisional_subjects(current, lookup), ["ISO"])
		self.assertEqual(student_import.provisional_subjects(legacy, lookup), ["IMO"])

	def test_review_rows_flags_numeric_names_duplicates_and_reused_serials(self):
		rows = [
			{"student_name": "ANIKA\nCHAVHAN", "class": "1", "serial_no": "1", "subjects": ["ISO"]},
			{"student_name": "anika chavhan", "class": "1", "serial_no": "2", "subjects": ["ISO"]},
			{"student_name": "9558979262", "class": "1", "serial_no": "3", "subjects": ["ISO"]},
			{"student_name": "RAVI", "class": "1", "serial_no": "1", "subjects": ["ISO"]},
		]
		with patch.object(student_import.frappe, "db") as db:
			db.exists.return_value = False
			reviewed = student_import.review_rows(rows, "School", "AY", SUBJECTS, ["1"])
		self.assertEqual(reviewed[0]["student_name"], "ANIKA CHAVHAN")  # wrapped name is one record
		self.assertEqual(len(reviewed), 4)  # nothing is silently dropped
		self.assertIn("Possible duplicate of row 1", reviewed[1]["issues"])
		self.assertIn("looks like a number", reviewed[2]["issues"])
		self.assertIn("Serial 1 is used by another student", reviewed[3]["issues"])

	def test_invoke_page_retries_a_response_cut_off_before_all_rows(self):
		service = MagicMock()
		service.invoke.side_effect = [
			{"rows_counted": 3, "students": [{"student_name": "A"}]},
			{"rows_counted": 3, "students": [{"student_name": "A"}, {"student_name": "B"}, {"student_name": "C"}]},
		]
		warnings = []
		data = student_import.invoke_page(service, MagicMock(), 1, warnings)
		self.assertEqual(len(data["students"]), 3)
		self.assertEqual(warnings, [])

	def test_invoke_page_fails_loudly_instead_of_returning_an_empty_page(self):
		service = MagicMock()
		service.invoke.return_value = None
		with self.assertRaises(student_import.frappe.ValidationError):
			student_import.invoke_page(service, MagicMock(), 2, [])
		self.assertEqual(service.invoke.call_count, student_import.AGENT_ATTEMPTS)

	def test_invoke_page_warns_when_the_ai_count_still_disagrees(self):
		service = MagicMock()
		service.invoke.return_value = {"rows_counted": 5, "students": [{"student_name": "A"}]}
		warnings = []
		student_import.invoke_page(service, MagicMock(), 1, warnings)
		self.assertEqual(len(warnings), 1)
		self.assertIn("counted 5", warnings[0])

	def test_rotate_image_turns_a_sideways_page_upright(self):
		import io

		from PIL import Image

		buffer = io.BytesIO()
		Image.new("RGB", (40, 10), "white").save(buffer, format="PNG")
		rotated = student_import.rotate_image(buffer.getvalue(), 90, "PNG")
		with Image.open(io.BytesIO(rotated)) as image:
			self.assertEqual(image.size, (10, 40))

	def test_extraction_job_stores_result_for_the_uploaded_file(self):
		cache, stored = MagicMock(), {}
		cache.get_value.return_value = {"status": "queued"}
		cache.set_value.side_effect = lambda key, value, **kwargs: stored.update(value)
		file_doc = MagicMock(name="File", file_name="list.pdf", file_url="/private/files/list.pdf")
		file_doc.name = "abc123"
		file_doc.get_full_path.return_value = "/tmp/list.pdf"
		outcome = {"students": [{"student_name": "A", "class": "1", "serial_no": "1", "subjects": ["ISO"]}],
			"warnings": ["note"], "pages": [{"page": 1, "students": 1, "printed_totals": []}], "usage": {}}
		with (
			patch.object(student_import.frappe, "cache", return_value=cache),
			patch.object(student_import.frappe, "session", student_import.frappe._dict(user="teacher@example.com")),
			patch.object(student_import, "_", side_effect=lambda text: text),  # translation also uses the mocked cache
			patch.object(student_import, "ensure_db_connection"),
			patch.object(student_import, "check_import_access"),
			patch.object(student_import, "get_private_file", return_value=file_doc),
			patch.object(student_import, "run_extraction", return_value=outcome),
			patch.object(student_import, "get_subject_master", return_value=SUBJECTS),
			patch.object(student_import, "get_class_master", return_value=["1"]),
			patch.object(student_import.frappe, "db") as db,
		):
			db.exists.return_value = False
			student_import.run_extraction_job("c" * 32, "abc123", "School", "AY")
		self.assertEqual(stored["status"], "completed")
		self.assertEqual(stored["result"]["file_name"], "abc123")
		self.assertEqual(len(stored["result"]["rows"]), 1)
		self.assertEqual(stored["result"]["warnings"], ["note"])

	def test_detect_rotation_picks_the_tile_the_model_names(self):
		import io

		from PIL import Image

		buffer = io.BytesIO()
		Image.new("RGB", (300, 200), "white").save(buffer, format="JPEG")
		service = MagicMock()
		service.get_llm.return_value.invoke.return_value.content = "3"
		self.assertEqual(student_import.detect_rotation(service, MagicMock(), buffer.getvalue()), 180)
		service.get_llm.return_value.invoke.return_value.content = [{"type": "text", "text": "Tile 2"}]
		self.assertEqual(student_import.detect_rotation(service, MagicMock(), buffer.getvalue()), 90)

	def test_detect_rotation_never_blocks_extraction(self):
		service = MagicMock()
		service.get_llm.side_effect = RuntimeError("provider down")
		with patch.object(student_import.frappe, "log_error"):
			self.assertEqual(student_import.detect_rotation(service, MagicMock(), b"not an image"), 0)

	def test_invoke_page_stops_at_once_on_a_rejected_model_and_explains_why(self):
		class BadRequestError(Exception):
			pass

		service = MagicMock()
		service.invoke.side_effect = BadRequestError("LLM Provider NOT provided. You passed model=Qwen3-VL-Plus\nmore")
		with patch.object(student_import.frappe, "log_error"), patch.object(student_import.time, "sleep") as sleep:
			with self.assertRaises(student_import.frappe.ValidationError) as caught:
				student_import.invoke_page(service, MagicMock(), 1, [])
		self.assertEqual(service.invoke.call_count, 1)
		sleep.assert_not_called()
		self.assertIn("LLM Provider NOT provided", str(caught.exception))
		self.assertIn("LLM Provider", str(caught.exception))

	def test_provider_message_hides_keys(self):
		text = student_import.provider_message(Exception("Incorrect API key provided: sk-abc123def456ghi789"))
		self.assertNotIn("abc123", text)
