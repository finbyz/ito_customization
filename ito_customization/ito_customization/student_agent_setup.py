"""Install the Student List agent and upgrade its older extraction prompt."""

import json
from pathlib import Path

import frappe


def ensure_student_list_agent() -> None:
	if not frappe.db.exists("DocType", "AI Agent") or not frappe.db.exists("DocType", "ITO AI Settings"):
		return
	seed_path = Path(__file__).resolve().parents[1] / "fixtures" / "ai_agent.json"
	seed = json.loads(seed_path.read_text())[0]
	seed["output_schema"] = json.dumps(seed["output_schema"])
	name = seed["name"]
	if frappe.db.exists("AI Agent", name):
		agent = frappe.get_doc("AI Agent", name)
		old_schema = (
			'"class"' not in (agent.output_schema or "")
			or '"subject_columns"' not in (agent.output_schema or "")
			or '"subject_cells"' not in (agent.output_schema or "")
			or ('"roll_no"' in (agent.output_schema or "") and '"serial_no"' not in (agent.output_schema or ""))
		)
		old_subject_prompt = any(
			"Student subject grid v4" in (row.content or "")
			or "Student subject grid v3" in (row.content or "")
			or "Student subject grid v2" in (row.content or "")
			or "Subject columns use abbreviations such as ISO" in (row.content or "")
			for row in agent.messages
		)
		if old_schema or old_subject_prompt or not agent.output_schema or not any(row.content for row in agent.messages):
			agent.agent_type = seed["agent_type"]
			agent.temperature = seed["temperature"]
			agent.output_schema = seed["output_schema"]
			agent.set("messages", seed["messages"])
			agent.save(ignore_permissions=True)
	elif frappe.db.exists("LLM", seed["llm"]):
		agent = frappe.get_doc(seed)
		agent.insert(ignore_permissions=True)
	else:
		return

	if not frappe.db.get_single_value("ITO AI Settings", "student_list_agent"):
		frappe.db.set_single_value("ITO AI Settings", "student_list_agent", name)
