frappe.listview_settings["Web Forms Registration"] = {
	refresh(listview) {
		listview.page.clear_inner_toolbar();
		listview.page.add_inner_button(__("Send Bulk Email"), () => {
			const names = listview.get_checked_items(true);
			if (!names.length) {
				frappe.msgprint(__("Please select at least one record."));
				return;
			}

			const d = new frappe.ui.Dialog({
				title: __("Send Bulk Email"),
				fields: [
					{
						fieldname: "email_template",
						label: __("Email Template"),
						fieldtype: "Link",
						options: "Email Template",
						reqd: 1,
					},
				],
				primary_action_label: __("Send"),
				primary_action({ email_template }) {
					frappe.call({
						method: "ito_customization.ito_customization.doctype.web_forms_registration.web_forms_registration.send_bulk_email",
						args: { names, email_template },
						freeze: true,
						freeze_message: __("Queuing emails..."),
						callback(r) {
							if (!r.exc) {
								const m = r.message || {};
								frappe.show_alert({
									message: __("{0} email(s) queued, {1} skipped (no email)", [
										m.queued || 0,
										m.skipped || 0,
									]),
									indicator: "green",
								});
								d.hide();
								listview.refresh();
							}
						},
					});
				},
			});
			d.show();
		});

		listview.page.add_inner_button(__("Resend E-Pass Email"), () => {
			const names = listview.get_checked_items(true);
			if (!names.length) {
				frappe.msgprint(__("Please select at least one record."));
				return;
			}
			frappe.confirm(__("Resend the E-Pass email for {0} selected record(s)?", [names.length]), () => {
				frappe.call({
					method: "ito_customization.ito_customization.doc_events.epass.bulk_resend_epass_email",
					args: { names },
					freeze: true,
					freeze_message: __("Queuing emails..."),
					callback(r) {
						if (r.exc) return;
						show_resend_result(r.message || {});
						listview.clear_checked_items();
						listview.refresh();
					},
				});
			});
		});
	},
};

// Short advice shown under each skip reason (keys match _resend_block_reason in epass.py).
const WFR_RESEND_HINTS = {
	"Registration is not approved": __("Set the status to Approve, then resend."),
	"No email address": __("Add an email address on the record, then resend."),
	"Not permitted": __("You need write access to these records."),
	"Not found": __("These records may have been deleted."),
};

// Styles live with the dialog, so a cached stylesheet can never leave it unstyled.
const WFR_RESEND_CSS = `
.wfr-rs { font-size: var(--text-md); color: var(--text-color); }
.wfr-rs-summary { display: grid; grid-template-columns: repeat(3, 1fr); border: 1px solid var(--border-color); border-radius: var(--border-radius-md); overflow: hidden; margin-bottom: 16px; }
.wfr-rs-cell { padding: 12px 16px; text-align: center; background: var(--card-bg); }
.wfr-rs-cell + .wfr-rs-cell { border-left: 1px solid var(--border-color); }
.wfr-rs-num { font-size: 24px; font-weight: 700; line-height: 1.2; }
.wfr-rs-lbl { margin-top: 2px; font-size: 11px; font-weight: 600; letter-spacing: .05em; text-transform: uppercase; color: var(--text-muted); }
.wfr-rs-cell.is-ok .wfr-rs-num { color: var(--green-600, #278f4f); }
.wfr-rs-cell.is-bad .wfr-rs-num { color: var(--red-600, #c53030); }
.wfr-rs-caption { font-weight: 600; margin-bottom: 8px; }
.wfr-rs-wrap { border: 1px solid var(--border-color); border-radius: var(--border-radius-md); max-height: 360px; overflow: auto; }
.wfr-rs-table { width: 100%; border-collapse: collapse; margin: 0; }
.wfr-rs-table th, .wfr-rs-table td { border: 1px solid var(--border-color); padding: 8px 10px; text-align: left; vertical-align: middle; }
.wfr-rs-table tr > :first-child { border-left: 0; }
.wfr-rs-table tr > :last-child { border-right: 0; }
.wfr-rs-table thead th { position: sticky; top: 0; z-index: 1; border-top: 0; background: var(--subtle-fg, var(--control-bg)); font-size: 11px; font-weight: 600; letter-spacing: .05em; text-transform: uppercase; color: var(--text-muted); }
.wfr-rs-table tbody tr:last-child td { border-bottom: 0; }
.wfr-rs-table td.wfr-rs-idx { width: 44px; text-align: center; color: var(--text-muted); }
.wfr-rs-table td.wfr-rs-id a { font-family: var(--font-stack-monospace, monospace); font-size: var(--text-sm); white-space: nowrap; }
.wfr-rs-table .wfr-rs-muted { color: var(--text-muted); font-style: italic; }
.wfr-rs-table tr.wfr-rs-group td { background: var(--control-bg); }
.wfr-rs-ghead { display: flex; align-items: center; gap: 8px; flex-wrap: wrap; }
.wfr-rs-ghead b { font-weight: 600; }
.wfr-rs-ghint { color: var(--text-muted); font-size: var(--text-sm); }
.wfr-rs-ghint::before { content: "\\2192  "; }
`;

function show_resend_result({ queued = [], skipped = [] }) {
	if (!skipped.length) {
		frappe.show_alert({ message: __("{0} E-Pass email(s) queued", [queued.length]), indicator: "green" });
		return;
	}
	const esc = frappe.utils.escape_html;
	const blank = (v, label) => (v ? esc(v) : `<span class="wfr-rs-muted">${label}</span>`);

	// One table, rows grouped under a header row per reason.
	const groups = {};
	skipped.forEach((s) => (groups[s.reason] = groups[s.reason] || []).push(s));

	let n = 0;
	const body = Object.entries(groups)
		.map(([reason, rows]) => {
			const hint = WFR_RESEND_HINTS[reason];
			const head = `<tr class="wfr-rs-group"><td colspan="5">
				<div class="wfr-rs-ghead">
					<span class="indicator-pill red">${rows.length}</span>
					<b>${esc(__(reason))}</b>
					${hint ? `<span class="wfr-rs-ghint">${esc(hint)}</span>` : ""}
				</div>
			</td></tr>`;
			const lines = rows
				.map(
					(s) => `<tr>
						<td class="wfr-rs-idx">${++n}</td>
						<td class="wfr-rs-id"><a href="/app/web-forms-registration/${encodeURIComponent(s.name)}" target="_blank" rel="noopener">${esc(s.name)}</a></td>
						<td>${blank(s.full_name, __("No name"))}</td>
						<td>${blank(s.email, __("No email"))}</td>
						<td>${blank(s.status, __("Not set"))}</td>
					</tr>`
				)
				.join("");
			return head + lines;
		})
		.join("");

	const none_sent = !queued.length;
	const d = new frappe.ui.Dialog({
		title: none_sent ? __("Mail not sent") : __("Some emails were not sent"),
		indicator: none_sent ? "red" : "orange",
		size: "large",
		fields: [{ fieldname: "body", fieldtype: "HTML" }],
		primary_action_label: __("Close"),
		primary_action: () => d.hide(),
	});
	d.fields_dict.body.$wrapper.html(`
		<style>${WFR_RESEND_CSS}</style>
		<div class="wfr-rs">
			<div class="wfr-rs-summary">
				<div class="wfr-rs-cell">
					<div class="wfr-rs-num">${queued.length + skipped.length}</div>
					<div class="wfr-rs-lbl">${__("Selected")}</div>
				</div>
				<div class="wfr-rs-cell is-ok">
					<div class="wfr-rs-num">${queued.length}</div>
					<div class="wfr-rs-lbl">${__("Queued")}</div>
				</div>
				<div class="wfr-rs-cell is-bad">
					<div class="wfr-rs-num">${skipped.length}</div>
					<div class="wfr-rs-lbl">${__("Not sent")}</div>
				</div>
			</div>
			<div class="wfr-rs-caption">${__("Records not sent")}</div>
			<div class="wfr-rs-wrap">
				<table class="wfr-rs-table">
					<thead><tr>
						<th>#</th><th>${__("Record")}</th><th>${__("Name")}</th><th>${__("Email")}</th><th>${__("Status")}</th>
					</tr></thead>
					<tbody>${body}</tbody>
				</table>
			</div>
		</div>
	`);
	d.show();
}
