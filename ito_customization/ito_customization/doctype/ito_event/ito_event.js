// Copyright (c) 2026, FinByz Tech Pvt Ltd and contributors
// For license information, please see license.txt

const ITO_EVENT_API = "ito_customization.ito_customization.doc_events.event_outputs";

// Shown in "Template Placeholders". Keep in step with event_context() in event_outputs.py.
const ITO_EVENT_PLACEHOLDERS = [
	["Event", [
		["{{ event.event_name }}", "Event name"],
		["{{ event.organiser_name }}", "Organiser name"],
		["{{ event.date_long }}", "23 November 2026"],
		["{{ event.date_short }}", "23 Nov 2026"],
		["{{ event.weekday }}", "Monday"],
		["{{ event.time_display }}", "10:00 AM – 1:00 PM"],
		["{{ event.when_short }}", "23 Nov 2026, 10:00 AM – 1:00 PM"],
		["{{ event.venue_name }}", "Venue name"],
		["{{ event.venue_address }}", "Venue address"],
		["{{ event.venue_map_url }}", "Google Maps link"],
		["{{ event.logo_url }}", "Logo image link"],
		["{{ event.brand_colour }}", "Brand colour, e.g. #2f7f73"],
	]],
	["Registration", [
		["{{ doc.full_name }}", "Full name"],
		["{{ doc.designation }}", "Designation"],
		["{{ doc.school_name }}", "School name"],
		["{{ doc.school_code }}", "School code"],
		["{{ doc.school_city }}", "City"],
		["{{ doc.school_state }}", "State"],
		["{{ doc.email }}", "Email"],
		["{{ doc.mobile_number }}", "Mobile number"],
	]],
	["Email only", [
		["{{ page_url }}", "Link to the online E-Pass page"],
		["{{ qr_url }}", "QR image link, use in <img src=\"...\">"],
		["{{ output }}", "Name of the output being sent, e.g. E-Pass"],
	]],
	["Print Format only", [
		["{%- set event = event_context(doc) -%}", "Put this on the first line to use {{ event... }}"],
		["{{ qr_data_uri(doc.epass_token) }}", "QR image, use in <img src=\"...\">"],
		["{{ photo_data_uri(doc.photo, 500) }}", "Uploaded photo, use in <img src=\"...\">"],
	]],
];

frappe.ui.form.on("ITO Event", {
	refresh(frm) {
		render_placeholder_guide(frm);
		if (frm.is_new()) return;
		render_overview(frm);

		frm.add_custom_button(__("Preview Output"), () => open_preview(frm));
		frm.add_custom_button(__("Registrations"), () =>
			frappe.set_route("List", "Web Forms Registration", { event: frm.doc.name })
		);
	},
});

frappe.ui.form.on("ITO Event Detail", {
	label: render_placeholder_guide_for_row,
	key: render_placeholder_guide_for_row,
	details_remove: render_placeholder_guide_for_row,
});

function render_placeholder_guide_for_row(frm) {
	render_placeholder_guide(frm);
}

function render_placeholder_guide(frm) {
	const field = frm.fields_dict.placeholder_guide;
	if (!field) return;
	const esc = frappe.utils.escape_html;

	const groups = ITO_EVENT_PLACEHOLDERS.slice();
	const details = (frm.doc.details || [])
		.filter((d) => d.key || d.label)
		.map((d) => [`{{ event.details.${d.key || slug(d.label)} }}`, d.label || d.key]);
	groups.splice(1, 0, [
		__("Extra Details (from the table above)"),
		details.length ? details : [["", __("Add rows to Extra Details and Notes to see them here.")]],
	]);

	const tables = groups
		.map(([title, rows]) => `
			<div style="margin-bottom:14px">
				<div style="font-weight:600;margin-bottom:6px">${esc(__(title))}</div>
				<table class="table table-bordered table-sm" style="margin:0">
					${rows.map(([code, help]) => `<tr>
						<td style="width:45%">${code ? `<code class="ito-ph" role="button" title="${esc(__("Click to copy"))}" style="cursor:pointer">${esc(code)}</code>` : ""}</td>
						<td class="text-muted">${esc(help)}</td>
					</tr>`).join("")}
				</table>
			</div>`)
		.join("");

	field.$wrapper.html(`
		<div class="text-muted small" style="margin-bottom:12px">
			${__("Use these in an Email Template or Print Format. Click a placeholder to copy it. A placeholder that has no value prints nothing.")}
		</div>${tables}`);
	field.$wrapper.find(".ito-ph").on("click", (e) => frappe.utils.copy_to_clipboard($(e.currentTarget).text()));
}

function slug(text) {
	return (text || "").trim().toLowerCase().replace(/[^a-z0-9]+/g, "_").replace(/^_+|_+$/g, "");
}

function open_preview(frm) {
	const outputs = (frm.doc.outputs || []).filter((o) => o.enabled).map((o) => o.output_name);
	if (!outputs.length) {
		frappe.msgprint(__("Add an enabled row to Outputs first."));
		return;
	}
	if (frm.is_dirty()) {
		frappe.msgprint(__("Save the event first, so the preview shows your latest changes."));
		return;
	}

	const d = new frappe.ui.Dialog({
		title: __("Preview Output"),
		size: "extra-large",
		fields: [
			{ fieldname: "output", fieldtype: "Select", label: __("Output"), options: outputs, default: outputs[0], reqd: 1 },
			{ fieldname: "col", fieldtype: "Column Break" },
			{
				fieldname: "registration",
				fieldtype: "Link",
				label: __("Registration"),
				options: "Web Forms Registration",
				description: __("Optional. Leave blank to use any approved registration of this event."),
				get_query: () => ({ filters: { event: frm.doc.name } }),
			},
			{ fieldname: "sec", fieldtype: "Section Break" },
			{ fieldname: "result", fieldtype: "HTML" },
		],
		primary_action_label: __("Show Preview"),
		primary_action(values) {
			frappe.call({
				method: `${ITO_EVENT_API}.preview`,
				args: { event: frm.doc.name, output: values.output, docname: values.registration || null },
				freeze: true,
				callback(r) {
					if (r.exc || !r.message) return;
					show_preview(d, r.message);
				},
			});
		},
	});
	d.show();
}

function show_preview(d, p) {
	const esc = frappe.utils.escape_html;
	const pdf_url = p.print_format
		? `/printview?doctype=${encodeURIComponent("Web Forms Registration")}&name=${encodeURIComponent(p.docname)}&format=${encodeURIComponent(p.print_format)}&trigger_print=0`
		: "";
	const $r = d.fields_dict.result.$wrapper;
	$r.html(`
		<div class="text-muted small" style="margin-bottom:8px">
			${__("Preview for {0}. Nothing is sent.", [`<a href="/app/web-forms-registration/${encodeURIComponent(p.docname)}" target="_blank">${esc(p.docname)}</a>`])}
			${pdf_url ? ` &middot; <a href="${pdf_url}" target="_blank" rel="noopener">${__("Open PDF / Print Preview")}</a>` : ""}
		</div>
		<div style="margin-bottom:8px"><b>${__("Subject")}:</b> ${esc(p.subject)}</div>
		<iframe class="ito-preview-frame" style="width:100%;height:520px;border:1px solid var(--border-color);border-radius:var(--border-radius-md);background:#fff"></iframe>
	`);
	// sandboxed: the email body is shown, its scripts (if any) never run
	const frame = $r.find(".ito-preview-frame")[0];
	frame.setAttribute("sandbox", "");
	frame.srcdoc = p.message;
}


// ---------------------------------------------------------------------------
// Overview: live numbers for this event. Each number opens the matching records.
// ---------------------------------------------------------------------------

const ITO_OVERVIEW_CSS = `
.ito-ov { font-size: var(--text-md); }
.ito-ov-top { display: flex; justify-content: space-between; align-items: center; margin-bottom: 10px; color: var(--text-muted); font-size: var(--text-sm); }
.ito-ov-groups { display: grid; grid-template-columns: repeat(auto-fit, minmax(230px, 1fr)); gap: 12px; }
.ito-ov-group { border: 1px solid var(--border-color); border-radius: var(--border-radius-md); padding: 10px 12px; background: var(--card-bg); }
.ito-ov-title { font-size: 11px; font-weight: 600; letter-spacing: .05em; text-transform: uppercase; color: var(--text-muted); margin-bottom: 8px; }
.ito-ov-tiles { display: grid; grid-template-columns: repeat(auto-fit, minmax(64px, 1fr)); gap: 6px; }
.ito-ov-tile { border-radius: var(--border-radius); padding: 6px 4px; text-align: center; }
.ito-ov-tile.is-link { cursor: pointer; }
.ito-ov-tile.is-link:hover { background: var(--control-bg); }
.ito-ov-num { font-size: 22px; font-weight: 700; line-height: 1.2; }
.ito-ov-lbl { font-size: var(--text-xs); color: var(--text-muted); }
.ito-ov-tile.green .ito-ov-num { color: var(--green-600, #278f4f); }
.ito-ov-tile.orange .ito-ov-num { color: var(--orange-600, #c05621); }
.ito-ov-tile.red .ito-ov-num { color: var(--red-600, #c53030); }
.ito-ov-tile.muted .ito-ov-num { color: var(--text-muted); }
`;

function render_overview(frm) {
	const field = frm.fields_dict.overview_html;
	if (!field) return;
	field.$wrapper.html(`<div class="text-muted small">${__("Loading overview...")}</div>`);
	frappe.call({
		method: "ito_customization.ito_customization.doc_events.event_dashboard.get_event_stats",
		args: { event: frm.doc.name },
		error() {
			field.$wrapper.html(`<div class="text-muted small">${__("Could not load the overview.")}</div>`);
		},
		callback(r) {
			if (r.message) draw_overview(frm, field.$wrapper, r.message);
		},
	});
}

function draw_overview(frm, $wrap, s) {
	const ev = frm.doc.name;
	const reg = (filters) => ({ doctype: "Web Forms Registration", filters: Object.assign({ event: ev }, filters) });
	const names = (list) => (list.length ? { doctype: "Web Forms Registration", filters: { name: ["in", list] } } : null);
	const approved = { status: "Approve" };

	const groups = [
		[__("Registrations"), [
			[s.registrations.total, __("Total"), "", reg({})],
			[s.registrations.approved, __("Approved"), "green", reg(approved)],
			[s.registrations.pending, __("Pending"), "orange", reg({ status: ["is", "not set"] })],
			[s.registrations.rejected, __("Rejected"), "muted", reg({ status: "Reject" })],
		]],
	];
	(s.outputs || []).forEach((o) => {
		groups.push([o.output, [
			[o.sent, __("Sent"), "green", null],
			[o.not_sent.length, __("Not sent yet"), o.not_sent.length ? "orange" : "muted", names(o.not_sent)],
		]]);
	});
	groups.push(
		[__("Attendance (approved)"), [
			[s.attendance.present, __("Present"), "green", reg(Object.assign({ event_attendance: "Present" }, approved))],
			[s.attendance.absent, __("Absent"), "red", reg(Object.assign({ event_attendance: "Absent" }, approved))],
			[s.attendance.not_marked, __("Not marked"), "muted", reg(Object.assign({ event_attendance: ["not in", ["Present", "Absent"]] }, approved))],
		]]
	);

	const esc = frappe.utils.escape_html;
	const targets = [];
	const html = groups
		.map(([title, tiles]) => `
			<div class="ito-ov-group">
				<div class="ito-ov-title">${esc(title)}</div>
				<div class="ito-ov-tiles">${tiles
					.map(([num, label, tone, target]) => {
						const i = target ? targets.push(target) - 1 : -1;
						return `<div class="ito-ov-tile ${tone} ${i >= 0 ? "is-link" : ""}" ${i >= 0 ? `data-t="${i}" role="button" title="${esc(__("Open these records"))}"` : ""}>
							<div class="ito-ov-num">${num}</div><div class="ito-ov-lbl">${esc(label)}</div>
						</div>`;
					})
					.join("")}</div>
			</div>`)
		.join("");

	$wrap.html(`<style>${ITO_OVERVIEW_CSS}</style>
		<div class="ito-ov">
			<div class="ito-ov-top">
				<span>${__("Click a number to open those records.")}</span>
				<a href="#" class="ito-ov-refresh">${__("Refresh")}</a>
			</div>
			<div class="ito-ov-groups">${html}</div>
		</div>`);
	$wrap.find(".ito-ov-refresh").on("click", (e) => {
		e.preventDefault();
		render_overview(frm);
	});
	$wrap.find(".ito-ov-tile.is-link").on("click", (e) => {
		const t = targets[$(e.currentTarget).data("t")];
		frappe.route_options = t.filters;
		frappe.set_route("List", t.doctype);
	});
}
