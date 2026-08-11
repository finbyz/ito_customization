
import io
import base64

import barcode
from barcode.writer import ImageWriter
import frappe


@frappe.whitelist()
def get_barcode_base64(value: str) -> str:
	"""Return a base64-encoded PNG Code128 barcode for `value`.

	Returns an empty string if value is falsy, so the Jinja template
	can safely do: {% if box.tracking_no %}...{% endif %} around it,
	or just call it directly - an empty <img> src is harmless.
	"""
	if not value:
		return ""

	code128 = barcode.get("code128", str(value), writer=ImageWriter())
	buffer = io.BytesIO()
	# write_text: False removes barcode.py's own small text baked into
	# the image directly under the bars - the print format already
	# shows the tracking number as separate, cleanly-sized HTML text
	# below the image, so we don't want it twice.
	code128.write(
		buffer,
		options={
			"module_height": 8.0,
			"write_text": False,
			"quiet_zone": 0.0,
		},
	)
	return base64.b64encode(buffer.getvalue()).decode()