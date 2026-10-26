frappe.ready(function () {
  // Turn Select fields into Google-Forms style radio buttons
  const RADIO_FIELDS = ["designation", "attendance_confirmation"];

  frappe.web_form.after_load = function () {
    RADIO_FIELDS.forEach(function (fn) {
      const f = frappe.web_form.fields_dict[fn];
      if (!f) return;
      const $w = f.$wrapper;
      $w.find("select").hide();
      const opts = (f.df.options || "").split("\n").filter(Boolean);
      const $g = $('<div class="ito-radio-group"></div>');
      opts.forEach(function (o) {
        $g.append(
          '<label><input type="radio" name="' + fn + '" value="' +
          frappe.utils.escape_html(o) + '"><span>' + frappe.utils.escape_html(o) + "</span></label>"
        );
      });
      $w.find(".control-input").append($g);
      $g.on("change", "input", function () {
        frappe.web_form.set_value(fn, this.value);
      });
    });

    // Footer: copy note, submit row (progress + clear form), password note
    if (!$(".ito-copy-note").length) {
      const $foot = $(".web-form-footer");
      $foot.before('<p class="ito-copy-note">A copy of your responses will be emailed to your email address.</p>');
      const $row = $('<div class="ito-footer-row"></div>');
      $row.append('<div class="ito-progress"><div class="bar"></div><span>Page 1 of 1</span></div>');
      $row.append('<a class="ito-clear">Clear form</a>');
      $foot.append($row);
      $foot.append('<p class="ito-pw">Never submit passwords through this form.</p>');
      $(".ito-clear").on("click", function () {
        frappe.confirm(__("Clear all answers in this form?"), function () { window.location.reload(); });
      });
    }
  };

  // Client-side validation (email consent + declaration checkboxes are not stored in the doctype)
  frappe.web_form.validate = function () {
    if (!$("#ito-email-consent").is(":checked")) {
      frappe.msgprint(__("Please tick the Email consent checkbox."));
      return false;
    }
    if (!$("#ito-declaration").is(":checked")) {
      frappe.msgprint(__("Please accept the Event Guidelines and Declaration to continue."));
      return false;
    }
    return true;
  };
});