// Copyright (c) 2026, FinByz Tech Pvt Ltd and contributors

const ITO_IMPORT_COLORS = {
	Draft: "gray", Validated: "blue", Queued: "orange", "In Progress": "orange",
	Completed: "green", "Partially Completed": "yellow", Failed: "red",
	Reverted: "gray", "Partially Reverted": "yellow",
};
const ITO_ROW_COLORS = { Valid: "green", Error: "red", Imported: "blue", Failed: "red", Reverted: "gray" };
const ITO_IMPORT_API = "ito_customization.ito_customization.bulk_registration_import";

frappe.ui.form.on("ITO Bulk Import", {
	setup(frm) {
		frm.set_query("exam_detail", () => ({ filters: { docstatus: 1, is_little_champ: 0 } }));
		frm.set_query("customer", () => ({ filters: { custom_customer_category: "School Customer" } }));
		frm.ito_filter = "All";
	},

	refresh(frm) {
		frm.page.set_indicator(__(frm.doc.status || "Draft"), ITO_IMPORT_COLORS[frm.doc.status] || "gray");
		ito_import.add_buttons(frm);
		ito_import.render(frm);
		if (frm.doc.file_notes) {
			frm.dashboard.set_headline_alert(
				frappe.utils.escape_html(frm.doc.file_notes).replace(/\n/g, "<br>"), "yellow"
			);
		}
		if (["Queued", "In Progress"].includes(frm.doc.status)) {
			frm.dashboard.set_headline(__("Running. This page updates when it finishes."));
		}
	},
});

const ito_import = {
	rows(frm) {
		return (frm.doc.rows || []).map((r) => {
			let details = {};
			try { details = JSON.parse(r.details || "{}"); } catch (e) { details = {}; }
			return Object.assign({}, r, { d: details });
		});
	},

	call(frm, method, args, message) {
		return frm.call({ doc: frm.doc, method, args, freeze: true, freeze_message: message || __("Checking the file...") })
			.then(() => frm.reload_doc());
	},

	download(method, args) {
		window.open(`/api/method/${ITO_IMPORT_API}.${method}?` + new URLSearchParams(args));
	},

	add_buttons(frm) {
		const running = ["Queued", "In Progress"].includes(frm.doc.status);
		const rows = ito_import.rows(frm);

		if (frm.doc.exam_detail) {
			const dl = __("Download");
			frm.add_custom_button(__("Empty Template"), () =>
				ito_import.download("download_template", { exam_detail: frm.doc.exam_detail }), dl);
			frm.add_custom_button(__("Schools of this Schedule (to update)"), () =>
				ito_import.download("download_prefilled", Object.assign(
					{ exam_detail: frm.doc.exam_detail }, frm.doc.customer ? { customer: frm.doc.customer } : {}
				)), dl);
			if (!frm.is_new() && rows.some((r) => ["Error", "Failed"].includes(r.status))) {
				frm.add_custom_button(__("Rows to Fix (Excel)"), () =>
					ito_import.download("download_errors", { import_name: frm.doc.name }), dl);
			}
		}

		if (frm.is_new() || frm.is_dirty() || !frm.doc.import_file || running) return;

		frm.add_custom_button(frm.doc.status === "Draft" ? __("Validate") : __("Check Again"), () =>
			ito_import.call(frm, "validate_file"));

		const fixes = rows.filter((r) => r.status !== "Imported")
			.reduce((n, r) => n + (r.d.issues || []).filter((i) => i.fix).length, 0);
		if (fixes) {
			frm.add_custom_button(__("Apply {0} Suggested Fixes", [fixes]), () =>
				frappe.confirm(__("Apply all {0} suggested fixes and check again?", [fixes]), () =>
					ito_import.call(frm, "apply_all_fixes")));
		}

		const valid = rows.filter((r) => r.status === "Valid").length;
		if (frm.doc.status !== "Draft" && valid) {
			frm.add_custom_button(__("Start Import"), () => {
				let msg = __("Import {0} schools? Rows with errors are skipped.", [valid]);
				if ((frm.doc.file_notes || "").includes("already imported")) {
					msg = `<b>${__("This file was already imported before.")}</b><br>${msg}`;
				}
				frappe.confirm(msg, () => ito_import.call(frm, "start_import", {}, __("Starting...")));
			}).addClass("btn-primary");
		}

		const imported = rows.filter((r) => r.status === "Imported").length;
		if (imported) {
			frm.add_custom_button(__("Revert Import"), () =>
				frappe.confirm(
					__("Undo what this import did for {0} schools? New records are deleted and updated ones get their old values back. Records changed since the import are left as they are.", [imported]),
					() => ito_import.call(frm, "start_revert", {}, __("Starting..."))
				));
		}
	},

	// ------------------------------------------------------------------ school cards

	render(frm) {
		const wrapper = frm.get_field("rows_html").$wrapper;
		const rows = ito_import.rows(frm);
		if (!rows.length) {
			wrapper.html(`<div class="text-muted">${__("Attach the sheet and click Validate to see the schools here.")}</div>`);
			return;
		}
		const filters = {
			All: () => true,
			Ready: (r) => r.status === "Valid",
			Warnings: (r) => (r.d.issues || []).some((i) => i.level === "warning"),
			Errors: (r) => ["Error", "Failed"].includes(r.status),
			New: (r) => r.d.preview && r.d.preview.is_new,
			Updates: (r) => r.d.preview && !r.d.preview.is_new,
			Imported: (r) => r.status === "Imported",
			Reverted: (r) => r.status === "Reverted",
		};
		const bar = Object.keys(filters).map((name) => {
			const n = rows.filter(filters[name]).length;
			if (!n && !["All", "Ready", "Errors"].includes(name)) return "";
			const active = frm.ito_filter === name ? "btn-primary" : "btn-default";
			return `<button class="btn btn-xs ${active} ito-filter" data-filter="${name}" style="margin:0 4px 4px 0">${__(name)} (${n})</button>`;
		}).join("");

		const shown = rows.filter(filters[frm.ito_filter] || filters.All);
		wrapper.html(`
			<div class="ito-import">
				<div style="margin-bottom:8px">${bar}</div>
				${shown.map((r) => ito_import.card(frm, r)).join("") || `<div class="text-muted">${__("No schools in this filter.")}</div>`}
			</div>`);

		wrapper.find(".ito-filter").on("click", function () {
			frm.ito_filter = $(this).data("filter");
			ito_import.render(frm);
		});
		wrapper.find(".ito-toggle").on("click", function () {
			$(this).closest(".ito-card").find(".ito-body").toggle();
		});
		wrapper.find(".ito-fix").on("click", function () {
			const row = rows.find((r) => r.row_no == $(this).data("row"));
			const issue = row.d.issues[$(this).data("issue")];
			ito_import.call(frm, "save_corrections", { row_no: row.row_no, corrections: { [issue.path]: issue.fix } });
		});
		wrapper.find(".ito-action").on("click", function () {
			const row = rows.find((r) => r.row_no == $(this).data("row"));
			ito_import.action(frm, row.d.issues[$(this).data("issue")].action);
		});
		wrapper.find(".ito-edit").on("click", function () {
			ito_import.edit(frm, rows.find((r) => r.row_no == $(this).data("row")));
		});
	},

	card(frm, r) {
		const esc = frappe.utils.escape_html;
		const p = r.d.preview || {};
		const issues = r.d.issues || [];
		const editable = !["Imported"].includes(r.status) && !["Queued", "In Progress"].includes(frm.doc.status);
		const link = (dt, name) => name
			? `<a href="/app/${frappe.router.slug(dt)}/${encodeURIComponent(name)}" target="_blank">${esc(dt)}: ${esc(name)}</a>`
			: "";
		const links = [link("Customer", r.customer), link("Exams Summary", r.exams_summary), link("Books Selection", r.books_selection)]
			.filter(Boolean).join(" &middot; ");
		const kind = p.is_new === undefined ? "" : `<span class="badge" style="margin-left:6px">${p.is_new ? __("New school") : __("Update")}</span>`;
		const corrected = r.corrections ? `<span class="badge" style="margin-left:6px">${__("Corrected on form")}</span>` : "";

		const issue_html = issues.map((i, idx) => {
			const icon = i.level === "error" ? "&#10006;" : "&#9888;";
			const color = i.level === "error" ? "var(--red-600)" : "var(--yellow-700, #a16207)";
			const where = [i.cell, i.label].filter(Boolean).map(esc).join(" ");
			let buttons = "";
			if (editable && i.fix) {
				buttons += ` <button class="btn btn-xs btn-default ito-fix" data-row="${r.row_no}" data-issue="${idx}">${__("Use")} ${esc(i.fix)}</button>`;
			}
			if (editable && i.action) {
				const label = i.action.type === "create_subject" ? __("Create Subject") : __("Add to Schedule");
				buttons += ` <button class="btn btn-xs btn-default ito-action" data-row="${r.row_no}" data-issue="${idx}">${label}</button>`;
			}
			return `<div style="color:${color};margin:2px 0">${icon} ${where ? `<b>${where}</b>: ` : ""}${esc(i.text)}${buttons}</div>`;
		}).join("");

		const changes = (p.changes || []).length ? `
			<table class="table table-bordered table-sm" style="margin:6px 0;font-size:12px">
				<tr><th>${__("Field")}</th><th>${__("Now")}</th><th>${__("After import")}</th></tr>
				${p.changes.map((c) => `<tr><td>${esc(c.label)}</td><td class="text-muted">${esc(c.old || "-")}</td><td>${esc(c.new)}</td></tr>`).join("")}
			</table>` : `<div class="text-muted">${__("No school details change.")}</div>`;

		const by_subject = {};
		(p.exams || []).forEach((e) => {
			(by_subject[e.subject] = by_subject[e.subject] || []).push(`${__("Class")} ${e.class}: ${e.no_of_students}`);
		});
		const exams = Object.keys(by_subject).length
			? Object.entries(by_subject).map(([s, list]) => `<div><b>${esc(s)}</b>: ${esc(list.join(", "))}</div>`).join("")
			: `<div class="text-muted">${__("No exam rows.")}</div>`;
		const books = (p.books || []).length
			? p.books.map((b) => `<div>${esc(b.subject)}, ${__("class")} ${esc(b.class)}: ${esc(Object.entries(b.books).map(([k, v]) => `${k} ${v}`).join(", "))}</div>`).join("")
			: `<div class="text-muted">${__("No books.")}</div>`;
		const staff = (p.staff || []).length
			? p.staff.map((s) => `<div>${esc(s.role)}: ${esc([s.name, s.mobile, s.email].filter(Boolean).join(", "))}</div>`).join("")
			: `<div class="text-muted">${__("No staff.")}</div>`;
		const result_lines = ["Imported", "Failed", "Reverted"].includes(r.status)
			? `<div class="text-muted" style="white-space:pre-wrap;margin-bottom:6px">${esc((r.message || "").split("\n").filter((l) => !l.startsWith("Warning:")).slice(0, 6).join("\n"))}</div>` : "";

		return `
			<div class="ito-card" style="border:1px solid var(--border-color);border-radius:8px;padding:10px 12px;margin-bottom:8px">
				<div style="display:flex;justify-content:space-between;align-items:center;gap:8px;flex-wrap:wrap">
					<div>
						<span class="indicator-pill ${ITO_ROW_COLORS[r.status] || "gray"}">${__(r.status)}</span>
						<b style="margin-left:6px">${__("Row")} ${r.row_no} &middot; ${esc(r.school_code || "")}</b>
						<span style="margin-left:6px">${esc(r.school_name || "")}</span>${kind}${corrected}
					</div>
					<div>
						${editable && (r.d.fields || []).length ? `<button class="btn btn-xs btn-default ito-edit" data-row="${r.row_no}">${__("Edit Values")}</button>` : ""}
						<button class="btn btn-xs btn-default ito-toggle">${__("Details")}</button>
					</div>
				</div>
				${links ? `<div style="margin-top:4px;font-size:12px">${links}</div>` : ""}
				${issue_html ? `<div style="margin-top:6px;font-size:12px">${issue_html}</div>` : ""}
				<div class="ito-body" style="display:none;margin-top:8px;font-size:12px">
					${result_lines}
					<div style="font-weight:600">${__("School details")}</div>${changes}
					<div style="font-weight:600;margin-top:6px">${__("Staff")}</div>${staff}
					<div style="font-weight:600;margin-top:6px">${__("Exam enrollment")}</div>${exams}
					<div style="font-weight:600;margin-top:6px">${__("Books")}</div>${books}
				</div>
			</div>`;
	},

	edit(frm, row) {
		const fields = row.d.fields || [];
		const key = (i) => `f_${i}`;
		const d = new frappe.ui.Dialog({
			title: __("Row {0}: {1}", [row.row_no, row.school_name || row.school_code || ""]),
			fields: [{ fieldtype: "HTML", options: `<div class="text-muted small">${__("Corrected values are used instead of the sheet. Clear a field to ignore the sheet value.")}</div>` }]
				.concat(fields.map((f, i) => ({ fieldtype: "Data", fieldname: key(i), label: f.label, default: f.value }))),
			primary_action_label: __("Save and Check"),
			primary_action(values) {
				const corrections = {};
				fields.forEach((f, i) => {
					const v = (values[key(i)] || "").trim();
					if (v !== (f.value || "")) corrections[f.path] = v;
				});
				d.hide();
				if (!Object.keys(corrections).length) return;
				ito_import.call(frm, "save_corrections", { row_no: row.row_no, corrections });
			},
		});
		d.show();
	},

	action(frm, action) {
		if (action.type === "add_to_schedule") {
			frappe.confirm(__("Add {0} to exam schedule {1}? Fill its slot dates there afterwards.", [action.subject, frm.doc.exam_detail]),
				() => ito_import.call(frm, "add_subject_to_schedule", { subject: action.subject }));
			return;
		}
		const words = (action.header || "").replace(/\b(EXAM|TEACHER|WB)\b/g, "").trim();
		const title = words ? frappe.utils.to_title_case(words.toLowerCase()) : "";
		const d = new frappe.ui.Dialog({
			title: __("Create School Subject"),
			fields: [
				{ fieldtype: "Data", fieldname: "subject_name", label: __("Subject Name"), reqd: 1,
					default: title && !/olympiad/i.test(title) ? `${title} Olympiad` : title,
					description: __("Other subjects use the form 'Maths Olympiad (IMO)'.") },
				{ fieldtype: "Data", fieldname: "abbr", label: __("Abbreviation"), reqd: 1, default: action.key || "",
					description: __("The sheet columns are matched by this, for example ABACUS.") },
				{ fieldtype: "Check", fieldname: "add_to_schedule", label: __("Add to exam schedule {0}", [frm.doc.exam_detail]), default: 1 },
			],
			primary_action_label: __("Create and Check"),
			primary_action(values) {
				d.hide();
				ito_import.call(frm, "create_subject", values);
			},
		});
		d.show();
	},
};
