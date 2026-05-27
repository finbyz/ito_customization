# -*- coding: utf-8 -*-
# Copyright (c) 2026, FinByz Tech Pvt Ltd and contributors
# For license information, please see license.txt

import frappe
from frappe import _

def after_save(doc, method=None):
    """Create Address and Contact for Sales Partner"""
    
    # Create Contact for Sales Partner
    create_contact(doc)
    
    # Create Address for Sales Partner
    create_address(doc)


def create_contact(doc):
    """Create Contact for Sales Partner if it doesn't exist"""
    
    # Check if contact already exists for this Sales Partner
    existing = frappe.db.exists(
        "Contact",
        {
            "first_name": doc.partner_name,
            "mobile_no": doc.custom_mobile_number
        }
    )
    
    if not existing:
        
        contact = frappe.new_doc("Contact")
        
        # Contact Name
        contact.first_name = doc.partner_name
        
        # Designation
        contact.designation = "External Cordinator"
        
        # Email
        if doc.custom_email_id:
            contact.append("email_ids", {
                "email_id": doc.custom_email_id,
                "is_primary": 1
            })
            contact.email_id = doc.custom_email_id
        
        # Phone
        if doc.custom_mobile_number:
            contact.append("phone_nos", {
                "phone": doc.custom_mobile_number,
                "is_primary_mobile_no": 1
            })
            contact.mobile_no = doc.custom_mobile_number
        
        # Link with Sales Partner
        contact.append("links", {
            "link_doctype": "Sales Partner",
            "link_name": doc.name,
            "link_title": doc.partner_name
        })
        
        contact.insert(ignore_permissions=True)
        
        frappe.msgprint(
            _("Contact created successfully for {0}").format(doc.partner_name),
            alert=True,
            indicator="green"
        )
    else:
        frappe.msgprint(
            _("Contact already exists for {0}").format(doc.partner_name),
            alert=True,
            indicator="blue"
        )


def create_address(doc):
    """Create Address for Sales Partner if address fields are filled"""
    
    # Check if address fields are available
    if (
        doc.custom_city or 
        doc.custom_state or 
        doc.custom_country or 
        doc.custom_pincode
    ):
        
        # Check if address already exists for this Sales Partner
        existing = frappe.db.exists(
            "Address",
            {
                "address_title": doc.partner_name,
                "city": doc.custom_city,
                "pincode": doc.custom_pincode
            }
        )
        
        if not existing:
            
            address = frappe.new_doc("Address")
            
            # Address Title
            address.address_title = doc.partner_name
            
            # Address Type
            address.address_type = "Office"
            
            # Address Line 1 - Use description or partner name
            if doc.description:
                # Remove HTML tags from description
                import re
                clean_desc = re.sub('<[^<]+?>', '', doc.description)
                address.address_line1 = clean_desc[:140] if clean_desc else doc.partner_name
            else:
                address.address_line1 = doc.partner_name
            
            # City
            if doc.custom_city:
                address.city = doc.custom_city
            
            # State
            if doc.custom_state:
                address.state = doc.custom_state
            
            # Country
            if doc.custom_country:
                address.country = doc.custom_country
            
            # Pincode
            if doc.custom_pincode:
                address.pincode = doc.custom_pincode
            
            # District (if available as custom field)
            if doc.custom_district:
                address.custom_district = doc.custom_district
            
            # Email
            if doc.custom_email_id:
                address.email_id = doc.custom_email_id
            
            # Phone
            if doc.custom_mobile_number:
                address.phone = doc.custom_mobile_number
            
            # Link with Sales Partner
            address.append("links", {
                "link_doctype": "Sales Partner",
                "link_name": doc.name,
                "link_title": doc.partner_name
            })
            
            # Set as primary address
            address.is_primary_address = 1
            
            address.insert(ignore_permissions=True)
            
            frappe.msgprint(
                _("Address created successfully for {0}").format(doc.partner_name),
                alert=True,
                indicator="green"
            )
        else:
            frappe.msgprint(
                _("Address already exists for {0}").format(doc.partner_name),
                alert=True,
                indicator="blue"
            )
