import frappe

def update_website_context(context):
    restricted_pages = [
        "/ito_registration",
        "/little_champ_registration",
    ]
    
    if frappe.session.user == "Guest" and frappe.request.path in restricted_pages:
        frappe.local.flags.redirect_location = "/login"
        raise frappe.Redirect
