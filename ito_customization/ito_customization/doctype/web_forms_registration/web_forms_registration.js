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


const WFR_ICONS = { Customer: "🏫", Lead: "🎯", Contact: "👤", Teacher: "🎓" };

function render_linked_records(frm) {
	const $wrap = frm.fields_dict.linked_records_html && frm.fields_dict.linked_records_html.$wrapper;
	if (!$wrap) return;
	$wrap.html(`<div class="text-muted small">${__("Loading...")}</div>`);

	frappe.call({
		method: `${WFR_API}.get_linked_records`,
		args: { name: frm.doc.name },
		callback(r) {
			const esc = frappe.utils.escape_html;
			const cards = (r.message || []).map((x) => {
				const icon = WFR_ICONS[x.doctype] || "";
				const head = `<div class="wfr-card-top"><span class="wfr-icon">${icon}</span><span class="wfr-dt">${__(x.doctype)}</span>`;
				if (x.state === "linked") {
					return `<div class="wfr-card">
						${head}<span class="indicator-pill green">${__("Linked")}</span></div>
						<a class="wfr-title" href="/app/${frappe.router.slug(x.doctype)}/${encodeURIComponent(x.name)}">${esc(x.title || x.name)}</a>
						<div class="wfr-sub">${esc(x.subtitle || x.name)}</div>
						<div class="wfr-meta">${__("Created {0} by {1}", [esc(x.created_on), esc(x.created_by)])}</div>
					</div>`;
				}
				const label = x.state === "restricted" ? __("No access") : __("Not created");
				const tone = x.state === "restricted" ? "gray" : "orange";
				return `<div class="wfr-card wfr-card-empty">
					${head}<span class="indicator-pill ${tone}">${label}</span></div>
					<div class="wfr-sub">${x.state === "restricted" ? "" : __("Use the Create button above")}</div>
				</div>`;
			});
			$wrap.html(`
				<style>
					.wfr-grid{display:grid;grid-template-columns:repeat(auto-fit,minmax(220px,1fr));gap:12px}
					.wfr-card{border:1px solid var(--border-color);border-radius:var(--border-radius-md);background:var(--card-bg);padding:14px 16px;box-shadow:var(--shadow-xs)}
					.wfr-card-empty{background:var(--control-bg);border-style:dashed;box-shadow:none}
					.wfr-card-top{display:flex;align-items:center;gap:8px;margin-bottom:8px}
					.wfr-icon{font-size:18px}
					.wfr-dt{font-weight:600;color:var(--text-muted);text-transform:uppercase;font-size:11px;letter-spacing:.04em;flex:1}
					.wfr-title{display:block;font-size:var(--text-lg);font-weight:600;margin-bottom:2px;word-break:break-word}
					.wfr-sub{color:var(--text-color);font-size:var(--text-md);margin-bottom:6px;word-break:break-word}
					.wfr-meta{color:var(--text-muted);font-size:var(--text-sm)}
				</style>
				<div class="wfr-grid">${cards.join("")}</div>`);
		},
	});
}
