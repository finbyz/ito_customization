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
    """Create or Update Contact for Sales Partner"""
    
    # First, check if there's already a contact linked to this Sales Partner
    existing_link = frappe.db.get_value(
        "Dynamic Link",
        {
            "link_doctype": "Sales Partner",
            "link_name": doc.name,
            "parenttype": "Contact"
        },
        "parent"
    )
    
    if existing_link:
        # Contact already linked to this Sales Partner - Update it
        contact = frappe.get_doc("Contact", existing_link)
        
        # Track if any changes were made
        updated = False
        
        # Update Contact Name if changed
        if contact.first_name != doc.partner_name:
            contact.first_name = doc.partner_name
            updated = True
        
        # Update Designation
        if contact.designation != "Sales Person":
            contact.designation = "Sales Person"
            updated = True
        
        # Update Email
        if doc.custom_email_id:
            # Check if email exists in email_ids
            email_exists = False
            for email in contact.email_ids:
                if email.email_id == doc.custom_email_id:
                    email_exists = True
                    if not email.is_primary:
                        email.is_primary = 1
                        updated = True
                    break
            
            if not email_exists:
                # Add new email
                contact.append("email_ids", {
                    "email_id": doc.custom_email_id,
                    "is_primary": 1
                })
                updated = True
            
            if contact.email_id != doc.custom_email_id:
                contact.email_id = doc.custom_email_id
                updated = True
        
        # Update Phone
        if doc.custom_mobile_number:
            # Check if phone exists in phone_nos
            phone_exists = False
            for phone in contact.phone_nos:
                if phone.phone == doc.custom_mobile_number:
                    phone_exists = True
                    if not phone.is_primary_mobile_no:
                        phone.is_primary_mobile_no = 1
                        updated = True
                    break
            
            if not phone_exists:
                # Add new phone
                contact.append("phone_nos", {
                    "phone": doc.custom_mobile_number,
                    "is_primary_mobile_no": 1
                })
                updated = True
            
            if contact.mobile_no != doc.custom_mobile_number:
                contact.mobile_no = doc.custom_mobile_number
                updated = True
        
        # Save if updated
        if updated:
            contact.save(ignore_permissions=True)
            frappe.msgprint(
                _("Contact updated successfully for {0}").format(doc.partner_name),
                alert=True,
                indicator="blue"
            )
        
        return
    
    # No existing contact linked to this Sales Partner
    # Check if contact exists with same name and mobile (but not linked)
    existing = frappe.db.exists(
        "Contact",
        {
            "first_name": doc.partner_name,
            "mobile_no": doc.custom_mobile_number
        }
    )
    
    if existing:
        # Contact exists but not linked - Link it to this Sales Partner
        contact = frappe.get_doc("Contact", existing)
        
        # Check if already linked
        already_linked = False
        for link in contact.links:
            if link.link_doctype == "Sales Partner" and link.link_name == doc.name:
                already_linked = True
                break
        
        if not already_linked:
            contact.append("links", {
                "link_doctype": "Sales Partner",
                "link_name": doc.name,
                "link_title": doc.partner_name
            })
            contact.save(ignore_permissions=True)
            
            frappe.msgprint(
                _("Existing contact linked to {0}").format(doc.partner_name),
                alert=True,
                indicator="blue"
            )
        
        return
    
    # Create new contact only if none exists
    contact = frappe.new_doc("Contact")
    
    # Contact Name
    contact.first_name = doc.partner_name
    
    # Designation
    contact.designation = "Sales Person"
    
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


def create_address(doc):
    """Create or Update Address for Sales Partner if address fields are filled"""
    
    # Check if address fields are available
    if not (doc.custom_city or doc.custom_stateprovince or doc.custom_country or doc.custom_pincode):
        return
    
    # First, check if there's already an address linked to this Sales Partner
    existing_link = frappe.db.get_value(
        "Dynamic Link",
        {
            "link_doctype": "Sales Partner",
            "link_name": doc.name,
            "parenttype": "Address"
        },
        "parent"
    )
    
    if existing_link:
        # Address already linked to this Sales Partner - Update it
        address = frappe.get_doc("Address", existing_link)
        
        # Track if any changes were made
        updated = False
        
        # Update Address Title
        if address.address_title != doc.partner_name:
            address.address_title = doc.partner_name
            updated = True
        
        # Update Address Line 1
        if doc.description:
            import re
            clean_desc = re.sub('<[^<]+?>', '', doc.description)
            new_address_line = clean_desc[:140] if clean_desc else doc.partner_name
        else:
            new_address_line = doc.partner_name
        
        if address.address_line1 != new_address_line:
            address.address_line1 = new_address_line
            updated = True
        
        # Update City
        if doc.custom_city and address.city != doc.custom_city:
            address.city = doc.custom_city
            updated = True
        
        # Update State
        if doc.custom_stateprovince and address.state != doc.custom_stateprovince:
            address.state = doc.custom_stateprovince
            updated = True
        
        # Update Country
        if doc.custom_country and address.country != doc.custom_country:
            address.country = doc.custom_country
            updated = True
        
        # Update Pincode
        if doc.custom_pincode and address.pincode != doc.custom_pincode:
            address.pincode = doc.custom_pincode
            updated = True
        
        # Update District
        if doc.custom_district and hasattr(address, 'custom_district'):
            if address.custom_district != doc.custom_district:
                address.custom_district = doc.custom_district
                updated = True
        
        # Update Email
        if doc.custom_email_id and address.email_id != doc.custom_email_id:
            address.email_id = doc.custom_email_id
            updated = True
        
        # Update Phone
        if doc.custom_mobile_number and address.phone != doc.custom_mobile_number:
            address.phone = doc.custom_mobile_number
            updated = True
        
        # Save if updated
        if updated:
            address.save(ignore_permissions=True)
            frappe.msgprint(
                _("Address updated successfully for {0}").format(doc.partner_name),
                alert=True,
                indicator="blue"
            )
        
        return
    
    # No existing address linked to this Sales Partner
    # Check if address already exists with same details (but not linked)
    existing = frappe.db.exists(
        "Address",
        {
            "address_title": doc.partner_name,
            "city": doc.custom_city,
            "pincode": doc.custom_pincode
        }
    )
    
    if existing:
        # Address exists but not linked - Link it to this Sales Partner
        address = frappe.get_doc("Address", existing)
        
        # Check if already linked
        already_linked = False
        for link in address.links:
            if link.link_doctype == "Sales Partner" and link.link_name == doc.name:
                already_linked = True
                break
        
        if not already_linked:
            address.append("links", {
                "link_doctype": "Sales Partner",
                "link_name": doc.name,
                "link_title": doc.partner_name
            })
            address.save(ignore_permissions=True)
            
            frappe.msgprint(
                _("Existing address linked to {0}").format(doc.partner_name),
                alert=True,
                indicator="blue"
            )
        
        return
    
    # Create new address only if none exists
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
    if doc.custom_stateprovince:
        address.state = doc.custom_stateprovince
    
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
