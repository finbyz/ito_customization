// Copyright (c) 2026, FinByz Tech Pvt Ltd and contributors
// For license information, please see license.txt

const WFR_API = "ito_customization.ito_customization.doctype.web_forms_registration.web_forms_registration";
const WFR_DOCTYPES = ["Customer", "Lead", "Contact", "Teacher"];

frappe.ui.form.on("Web Forms Registration", {
	refresh(frm) {
		if (frm.is_new()) return;

		add_create_button(frm);
		render_linked_records(frm);

		if (frm.doc.status !== "Approve") return;

		frm.add_custom_button(__("Resend Email"), () => {
			frappe.confirm(__("Resend the E-Pass email to {0}?", [frm.doc.email]), () => {
				frappe.call({
					method: "ito_customization.ito_customization.doc_events.epass.resend_epass_email",
					args: { docname: frm.doc.name },
					freeze: true,
					freeze_message: __("Queuing email..."),
					callback(r) {
						if (!r.exc) {
							frappe.show_alert({ message: __("Email queued"), indicator: "green" });
						}
					},
				});
			});
		});
	},
});

function add_create_button(frm) {
	// Label shows progress, e.g. "Create (2/4 done)"; click always re-reads the latest state.
	const btn = frm.add_custom_button(__("Create"), () => open_create_dialog(frm));
	frappe.call({
		method: `${WFR_API}.get_create_preview`,
		args: { name: frm.doc.name },
		callback(r) {
			const done = Object.values(r.message || {}).filter((p) => p.state === "linked").length;
			if (done) btn.text(__("Create ({0}/{1} done)", [done, WFR_DOCTYPES.length]));
		},
	});
}

function open_create_dialog(frm) {
	frappe.call({
		method: `${WFR_API}.get_create_preview`,
		args: { name: frm.doc.name },
		freeze: true,
		callback(r) {
			if (r.exc) return;
			show_create_dialog(frm, r.message);
		},
	});
}

function state_note(p) {
	if (p.state === "linked") return __("Done: {0}", [frappe.utils.escape_html(p.name)]);
	if (p.state === "exists") return __("Already exists ({0}), will be reused and linked", [frappe.utils.escape_html(p.name)]);
	return __("Will be created");
}

function show_create_dialog(frm, preview) {
	const all_done = WFR_DOCTYPES.every((dt) => preview[dt].state === "linked");
	const teacher_new = preview.Teacher.state === "new";

	const fields = [];
	WFR_DOCTYPES.forEach((dt) => {
		const p = preview[dt];
		fields.push({
			fieldname: dt.toLowerCase(),
			fieldtype: "Check",
			label: __(dt),
			default: 1, // new => ticked; exists/linked => ticked and locked
			read_only: p.state !== "new" ? 1 : 0,
			description: state_note(p),
		});
		if (dt === "Customer" && p.state === "new" && (p.similar || []).length) {
			fields.push({
				fieldname: "use_customer",
				fieldtype: "Select",
				label: __("Similar school found"),
				options: [`${__("Create a new Customer")}`].concat(p.similar.map((x) => `${x.name} | ${x.customer_name}`)).join("\n"),
				default: __("Create a new Customer"),
				depends_on: "eval:doc.customer",
				description: __("Pick an existing school to avoid creating a duplicate."),
			});
		}
		if (dt === "Teacher" && teacher_new) {
			fields.push(
				{
					fieldname: "subject",
					fieldtype: "Link",
					options: "School Subject",
					label: __("Teacher Subject"),
					depends_on: "eval:doc.teacher",
					description: __("Optional. Not collected at registration; the Teacher stays incomplete without it."),
				},
				{
					fieldname: "date_of_birth",
					fieldtype: "Date",
					label: __("Teacher Date of Birth"),
					depends_on: "eval:doc.teacher",
				}
			);
		}
	});

	const d = new frappe.ui.Dialog({
		title: __("Create Records"),
		fields,
		primary_action_label: __("Create"),
		primary_action(values) {
			// "linked" ones are already done; everything else that is ticked is sent
			const doctypes = WFR_DOCTYPES.filter(
				(dt) => values[dt.toLowerCase()] && preview[dt].state !== "linked"
			);
			if (!doctypes.length) {
				frappe.msgprint(__("Select at least one DocType."));
				return;
			}
			const extras = {};
			if (values.use_customer && values.use_customer.includes(" | ")) {
				extras.use_customer = values.use_customer.split(" | ")[0];
			}
			if (doctypes.includes("Teacher") && teacher_new) {
				if (values.subject) extras.subject = values.subject;
				if (values.date_of_birth) extras.date_of_birth = values.date_of_birth;
			}
			frappe.call({
				method: `${WFR_API}.create_linked_records`,
				args: { name: frm.doc.name, doctypes, extras },
				freeze: true,
				freeze_message: __("Creating records..."),
				callback(res) {
					if (res.exc) return;
					d.hide();
					show_result(frm, res.message, extras, doctypes);
				},
			});
		},
	});

	if (all_done) {
		d.get_primary_btn().prop("disabled", true);
		d.set_title(__("Everything is already created"));
	}
	d.show();
}

function show_result(frm, result, extras, doctypes) {
	const link = (dt, n) =>
		`<li>${__(dt)}: <a href="/app/${frappe.router.slug(dt)}/${encodeURIComponent(n)}">${frappe.utils.escape_html(n)}</a></li>`;
	const list = (o) => Object.entries(o || {}).map(([dt, n]) => link(dt, n)).join("");
	const c = list(result.created);
	const e = list(result.existing);

	const missing = [];
	if (result.created.Teacher && !extras.subject) missing.push(__("Teacher has no Subject yet"));
	if (result.created.Teacher && !extras.date_of_birth) missing.push(__("Teacher has no Date of Birth yet"));

	const customer = (result.created.Customer || result.existing.Customer);
	const msg =
		(c ? `<b>${__("Created")}</b><ul>${c}</ul>` : "") +
		(e ? `<b>${__("Already existed (reused and linked)")}</b><ul>${e}</ul>` : "") +
		(missing.length ? `<div class="text-warning"><b>${__("Still to complete")}</b><ul>${missing.map((m) => `<li>${m}</li>`).join("")}</ul></div>` : "");

	frappe.msgprint({
		title: __("Done"),
		indicator: "green",
		message: msg,
		primary_action: customer
			? { label: __("Open Customer"), action: () => frappe.set_route("Form", "Customer", customer) }
			: undefined,
	});
	frm.reload_doc();
}


// Inline stroke icons: follow the theme (currentColor) and look the same on every OS.
const WFR_ICONS = {
	Customer: '<path d="M3 21h18M5 21V8l7-5 7 5v13M9 21v-6h6v6"/>',
	Lead: '<circle cx="12" cy="12" r="9"/><circle cx="12" cy="12" r="5"/><circle cx="12" cy="12" r="1"/>',
	Contact: '<circle cx="12" cy="8" r="4"/><path d="M4 21c0-4 3.5-7 8-7s8 3 8 7"/>',
	Teacher: '<path d="M2 9l10-5 10 5-10 5z"/><path d="M6 11.5V16c0 1.5 3 3 6 3s6-1.5 6-3v-4.5"/>',
};
const WFR_STATE_CLASS = { linked: "is-linked", reused: "is-reused", missing: "is-missing", restricted: "is-restricted" };

function wfr_icon(dt) {
	return `<svg class="wfr-svg" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="1.8" stroke-linecap="round" stroke-linejoin="round" aria-hidden="true">${WFR_ICONS[dt] || ""}</svg>`;
}

function wfr_skeleton() {
	const one = `<div class="wfr-card wfr-skeleton"><div class="wfr-sk-line w40"></div><div class="wfr-sk-line w80"></div><div class="wfr-sk-line w60"></div></div>`;
	return `<div class="wfr-grid">${one.repeat(WFR_DOCTYPES.length)}</div>`;
}

function render_linked_records(frm) {
	const $wrap = frm.fields_dict.linked_records_html && frm.fields_dict.linked_records_html.$wrapper;
	if (!$wrap) return;
	$wrap.html(wfr_skeleton());

	frappe.call({
		method: `${WFR_API}.get_linked_records`,
		args: { name: frm.doc.name },
		error() {
			$wrap.html(`<div class="wfr-error">${__("Could not load linked records.")}
				<a href="#" class="wfr-retry">${__("Retry")}</a></div>`);
			$wrap.find(".wfr-retry").on("click", (e) => {
				e.preventDefault();
				render_linked_records(frm);
			});
		},
		callback(r) {
			const esc = frappe.utils.escape_html;
			const rows = r.message || [];
			const done = rows.filter((x) => x.state === "linked").length;
			const total = rows.length;
			const pct = total ? Math.round((done / total) * 100) : 0;

			const summary = `<div class="wfr-summary">
				<div class="wfr-summary-text">
					${done === total && total ? `<span class="indicator-pill green">${__("All linked")}</span>` : ""}
					<span>${__("{0} of {1} records created", [done, total])}</span>
				</div>
				<div class="wfr-bar" role="progressbar" aria-valuemin="0" aria-valuemax="100" aria-valuenow="${pct}">
					<div class="wfr-bar-fill${done === total && total ? " is-done" : ""}" style="width:${pct}%"></div>
				</div>
			</div>`;

			const cards = rows.map((x) => {
				const dt = __(x.doctype);
				const head = (pill, tone) => `<div class="wfr-card-top">
					<span class="wfr-icon">${wfr_icon(x.doctype)}</span>
					<span class="wfr-dt">${esc(dt)}</span>
					<span class="indicator-pill ${tone}" aria-label="${esc(pill)}">${esc(pill)}</span>
				</div>`;

				if (x.state === "linked") {
					const href = `/app/${frappe.router.slug(x.doctype)}/${encodeURIComponent(x.name)}`;
					const when = x.created_iso ? frappe.datetime.prettyDate(x.created_iso) : esc(x.created_on);
					const avatar = x.owner ? frappe.avatar(x.owner, "avatar-xs") : "";
					return `<a class="wfr-card ${WFR_STATE_CLASS.linked}" href="${href}" aria-label="${esc(__("Open {0}", [dt]))}">
						${head(__("Linked"), "green")}
						<div class="wfr-title">${esc(x.title || x.name)}</div>
						<div class="wfr-sub">${esc(x.subtitle || x.name)}</div>
						<div class="wfr-meta" title="${esc(x.created_on)} — ${esc(x.created_by)}">
							${avatar}<span>${esc(when)} · ${esc(x.created_by)}</span>
						</div>
					</a>`;
				}
				const restricted = x.state === "restricted";
				return `<div class="wfr-card ${restricted ? WFR_STATE_CLASS.restricted : WFR_STATE_CLASS.missing}">
					${head(restricted ? __("No access") : __("Not created"), restricted ? "gray" : "orange")}
					<div class="wfr-sub">${restricted ? "" : __("Use the Create button above")}</div>
				</div>`;
			});

			$wrap.html(`${summary}<div class="wfr-grid">${cards.join("")}</div>`);
		},
	});
}
