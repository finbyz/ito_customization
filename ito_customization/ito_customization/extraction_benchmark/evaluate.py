"""Offline regression benchmark for the student-list AI extraction.

Runs the production extraction (`run_extraction`) on every document in cases.json, scores it against
the hand-transcribed ground truth, and reports accuracy, token use and cost per document.

    bench --site <site> execute ito_customization.ito_customization.extraction_benchmark.evaluate.run
    bench --site <site> execute ...evaluate.run --kwargs '{"llm": "openrouter/qwen/qwen3.8-flash"}'
    bench --site <site> execute ...evaluate.compare --kwargs '{"llms": ["openai/gpt-5.6-terra", "openrouter/qwen/qwen3.8-flash"]}'

Add documents by adding a case to cases.json and the file to files/. Nothing here writes students.
"""

import json
import time
from difflib import SequenceMatcher
from pathlib import Path

import frappe

from ito_customization.ito_customization.doctype.registered_students import student_import

HERE = Path(__file__).parent
EXACT = 1.0
NAME_MATCH = 0.88
APPROX_MATCH = 0.7


def load_cases(only=None) -> list[dict]:
	cases = json.loads((HERE / "cases.json").read_text())["cases"]
	return [case for case in cases if not only or case["id"] in only]


def run(llm=None, only=None, save=True) -> list[dict]:
	"""Evaluate every case (or those listed in `only`) and print a summary table."""
	frappe.set_user("Administrator")
	subjects = student_import.get_subject_master()
	classes = student_import.get_class_master()
	results = []
	for case in load_cases(only):
		path = HERE / "files" / case["file"]
		started = time.time()
		student_import.ensure_db_connection()
		try:
			# review_rows needs a school and year only for its duplicate lookup, which is irrelevant here.
			outcome = student_import.run_extraction(str(path), path.suffix.lower(), "", "", llm=llm)
			student_import.ensure_db_connection()
			rows = student_import.review_rows(outcome["students"], "", "", subjects, classes)
			result = score(case, outcome, rows, classes)
		except Exception as exc:
			result = {"id": case["id"], "error": f"{type(exc).__name__}: {exc}"}
		result["seconds"] = round(time.time() - started, 1)
		result["llm"] = llm or "(agent default)"
		result["cost_usd"] = estimate_cost(llm, (result.get("usage") or {}))
		results.append(result)
	print_summary(results)
	if save:
		out = HERE / "results" / f"{(llm or 'default').replace('/', '_')}.json"
		out.parent.mkdir(exist_ok=True)
		out.write_text(json.dumps(results, indent=1, default=str))
	return results


def compare(llms, only=None) -> dict:
	return {llm: run(llm=llm, only=only) for llm in llms}


def score(case, outcome, rows, classes) -> dict:
	expected = case.get("students", [])
	pool = list(range(len(rows)))
	name_hits = subject_hits = class_hits = subject_checked = class_checked = exact_names = 0
	missing, wrong_subjects = [], []
	for item in expected:
		best, best_ratio = None, 0.0
		for index in pool:
			ratio = SequenceMatcher(None, item["name"].casefold(), rows[index]["student_name"].casefold()).ratio()
			if ratio > best_ratio:
				best, best_ratio = index, ratio
		threshold = APPROX_MATCH if item.get("approx") else NAME_MATCH
		if best is None or best_ratio < threshold:
			missing.append(item["name"])
			continue
		pool.remove(best)
		name_hits += 1
		exact_names += best_ratio >= EXACT
		row = rows[best]
		if "subjects" in item:
			subject_checked += 1
			got = {part.strip() for part in row["subjects"].split(",") if part.strip()}
			if got == set(item["subjects"]):
				subject_hits += 1
			else:
				wrong_subjects.append(f'{item["name"]}: expected {sorted(item["subjects"])} got {sorted(got)}')
		if "class" in item:
			class_checked += 1
			class_hits += student_import.resolve_class(row["class"], classes) == student_import.resolve_class(item["class"], classes)

	rule = case.get("rule_every_student_subjects")
	rule_hits = None
	if rule:
		rule_hits = sum({p.strip() for p in row["subjects"].split(",") if p.strip()} == set(rule) for row in rows)
	by_class = {}
	for row in rows:
		by_class[row["class"]] = by_class.get(row["class"], 0) + 1
	expected_by_class = case.get("expected_counts_by_class")
	flagged_names = [row["student_name"] for row in rows if "student name" in row["issues"].casefold() or "looks like" in row["issues"]]
	return {
		"id": case["id"],
		"students_returned": len(rows),
		"students_expected": case.get("expected_total"),
		"count_ok": len(rows) == case.get("expected_total"),
		"names_matched": f"{name_hits}/{len(expected)}",
		"names_exact": f"{exact_names}/{len(expected)}",
		"subjects_ok": f"{subject_hits}/{subject_checked}" if subject_checked else None,
		"classes_ok": f"{class_hits}/{class_checked}" if class_checked else None,
		"rule_subjects_ok": f"{rule_hits}/{len(rows)}" if rule else None,
		"counts_by_class": by_class,
		"counts_by_class_ok": (by_class == expected_by_class) if expected_by_class else None,
		"missing_names": missing,
		"wrong_subjects": wrong_subjects,
		"flagged_name_rows": flagged_names,
		"warnings": outcome["warnings"] + student_import.page_total_warnings(outcome),
		"extra_rows": [rows[i]["student_name"] for i in pool][:20] if not expected_by_class else [],
		"usage": outcome["usage"],
		"returned": [[r["class"], r["serial_no"], r["student_name"], r["subjects"]] for r in rows],
	}


def estimate_cost(llm, usage) -> float | None:
	"""Cost from litellm's bundled price list; None when the model is not priced there."""
	try:
		import litellm

		model = llm or frappe.db.get_value("AI Agent", frappe.db.get_single_value("ITO AI Settings", "student_list_agent"), "llm")
		prices = litellm.model_cost.get(model) or litellm.model_cost.get(model.split("/", 1)[-1])
		if not prices or not usage:
			return None
		return round(
			usage.get("input_tokens", 0) * prices.get("input_cost_per_token", 0)
			+ usage.get("output_tokens", 0) * prices.get("output_cost_per_token", 0), 4)
	except Exception:
		return None


def print_summary(results):
	for item in results:
		if "error" in item:
			print(f'{item["id"]}: ERROR {item["error"]}')
			continue
		print(
			f'{item["id"]}: rows {item["students_returned"]}/{item["students_expected"]} '
			f'names {item["names_matched"]} (exact {item["names_exact"]}) subjects {item["subjects_ok"]} '
			f'class {item["classes_ok"]} rule {item["rule_subjects_ok"]} '
			f'tokens {item["usage"]} cost ${item["cost_usd"]} {item["seconds"]}s'
		)
		for key in ("missing_names", "wrong_subjects", "warnings"):
			if item[key]:
				print(f"   {key}: {item[key][:8]}")
