# Copyright (c) 2026, a and contributors
# For license information, please see license.txt
"""Customer portal page: lists all Sales Invoices for the logged-in customer."""

import frappe
from frappe import _
from frappe.utils import flt, fmt_money, formatdate

no_cache = 1


def get_my_customers():
	"""Customers the logged-in portal user is linked to (via Customer's Portal Users)."""
	from erpnext.controllers.website_list_for_contact import get_parents_for_user

	return get_parents_for_user("Customer") or []


def get_context(context):
	if frappe.session.user == "Guest":
		frappe.local.flags.redirect_location = "/login?redirect-to=/my-invoices"
		raise frappe.Redirect

	customers = get_my_customers()
	invoices = []
	unpaid_count = 0

	if customers:
		invoices = frappe.get_all(
			"Sales Invoice",
			filters={"customer": ["in", customers], "docstatus": 1},
			fields=[
				"name", "posting_date", "due_date", "currency",
				"grand_total", "outstanding_amount", "status",
			],
			order_by="posting_date desc",
		)
		for inv in invoices:
			inv.posting_date_fmt = formatdate(inv.posting_date)
			inv.due_date_fmt = formatdate(inv.due_date) if inv.due_date else ""
			inv.grand_total_fmt = fmt_money(inv.grand_total, currency=inv.currency)
			inv.outstanding_fmt = fmt_money(inv.outstanding_amount, currency=inv.currency)
			inv.is_paid = flt(inv.outstanding_amount) <= 0
			if not inv.is_paid:
				unpaid_count += 1

	context.invoices = invoices
	context.unpaid_count = unpaid_count
	context.has_customer = bool(customers)
	context.title = _("My Invoices")
	context.show_sidebar = True
	return context
