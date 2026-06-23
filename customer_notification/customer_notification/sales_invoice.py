# Copyright (c) 2026, a and contributors
# For license information, please see license.txt
"""Send a Sales Invoice PDF to its customer using a configured Email Template.

Used by the Sales Invoice form button and the list-view bulk action.
"""

import json

import frappe
from frappe import _
from frappe.utils import now_datetime

LANGUAGE_MAP = {"English": "en", "Arabic": "ar"}


@frappe.whitelist()
def get_invoice_email_defaults(customer=None, invoice=None, is_return=None):
	"""Return the configured defaults for the send-invoice dialog.

	When a ``customer`` is given, the customer's configured CC users (the
	``cc_users`` table on Customer) are resolved to email addresses and
	returned so the dialog can pre-fill the CC field.

	For return invoices (Credit Notes, ``is_return = 1``) the return-specific
	Email Template / Print Format from settings are used, falling back to the
	regular Sales Invoice ones when they are left blank.
	"""
	s = frappe.get_cached_doc("Customer Notification Settings")

	if is_return is None and invoice:
		is_return = frappe.db.get_value("Sales Invoice", invoice, "is_return")
	is_return = int(is_return or 0)

	if is_return:
		email_template = s.return_invoice_email_template or s.sales_invoice_email_template
		print_format = s.return_invoice_print_format or s.sales_invoice_print_format
	else:
		email_template = s.sales_invoice_email_template
		print_format = s.sales_invoice_print_format

	return {
		"email_template": email_template,
		"print_format": print_format,
		"language": s.default_language or "English",
		"cc_users": get_customer_cc_users(customer) if customer else [],
	}


def get_customer_cc_users(customer):
	"""Return the Customer's configured `cc_users` as a list of User ids."""
	if not customer:
		return []
	return frappe.get_all(
		"Notification Type CC User",
		filters={"parent": customer, "parenttype": "Customer", "parentfield": "cc_users"},
		pluck="user",
	)


def get_customer_cc_emails(customer):
	"""Resolve the Customer's `cc_users` table to a de-duplicated list of emails."""
	return _resolve_cc_to_emails(get_customer_cc_users(customer))


def _resolve_cc_to_emails(values):
	"""Map CC entries (User ids and/or raw email addresses) to a clean email list.

	Each value is looked up as a User: if found, its email is used; otherwise the
	value is treated as a raw email address. Duplicates are dropped.
	"""
	emails = []
	for value in values or []:
		value = (value or "").strip()
		if not value:
			continue
		email = frappe.db.get_value("User", value, "email") or value
		if email and email not in emails:
			emails.append(email)
	return emails


def _log(customer, invoice, status, **kwargs):
	doc = frappe.new_doc("Customer Notification Log")
	doc.customer = customer
	doc.channel = "Email"
	doc.status = status
	doc.sent_on = now_datetime()
	for k, v in kwargs.items():
		doc.set(k, v)
	doc.flags.ignore_permissions = True
	doc.insert(ignore_permissions=True)


def _recipient(si):
	"""Resolve the customer email for an invoice."""
	return si.get("contact_email") or frappe.db.get_value("Customer", si.customer, "email_id")


def _normalize_cc(cc):
	"""Accept a JSON string, comma/newline string, or list and return a clean email list."""
	if not cc:
		return []
	if isinstance(cc, str):
		try:
			parsed = json.loads(cc)
			cc = parsed if isinstance(parsed, list) else cc
		except (ValueError, TypeError):
			pass
	if isinstance(cc, str):
		cc = cc.replace("\n", ",").split(",")
	out = []
	for item in cc:
		email = (item or "").strip()
		if email and email not in out:
			out.append(email)
	return out


def send_invoice(invoice, email_template=None, print_format=None, language="en", cc=None,
		triggered_by="Sales Invoice"):
	"""Email a single Sales Invoice PDF to its customer. Failures are logged, not raised."""
	si = frappe.get_doc("Sales Invoice", invoice)

	if si.docstatus != 1:
		_log(si.customer, invoice, "Skipped", recipient=invoice, error="Invoice is not submitted.",
			triggered_by=triggered_by)
		return {"invoice": invoice, "status": "Skipped", "reason": "not submitted"}

	recipient = _recipient(si)
	if not recipient:
		_log(si.customer, invoice, "Skipped", recipient=invoice,
			error="No email on invoice contact or customer.", triggered_by=triggered_by)
		return {"invoice": invoice, "status": "Skipped", "reason": "no email"}

	if not email_template:
		defaults = get_invoice_email_defaults(is_return=si.is_return)
		email_template = defaults["email_template"]
		print_format = print_format or defaults["print_format"]
		language = language or defaults["language"]
	if not email_template:
		frappe.throw(_("No Sales Invoice Email Template configured in Customer Notification Settings."))

	if cc is None:
		# Background / bulk sends pass no cc: fall back to the customer's CC users.
		cc_emails = get_customer_cc_emails(si.customer)
	else:
		# Dialog sends a list of User ids (and/or raw emails) — resolve to emails.
		cc_emails = _resolve_cc_to_emails(_normalize_cc(cc))
	cc_emails = [e for e in cc_emails if e != recipient]

	lang = LANGUAGE_MAP.get(language, language or "en")
	context = {
		"doc": si,
		"customer_name": si.customer_name or si.customer,
		"company": si.company,
		"invoice": si.name,
		"grand_total": si.grand_total,
		"outstanding_amount": si.outstanding_amount,
		"due_date": si.due_date,
	}

	prev_lang = frappe.local.lang
	try:
		frappe.local.lang = lang
		pdf = frappe.get_print("Sales Invoice", invoice, print_format=print_format or None, as_pdf=True)
		et = frappe.get_doc("Email Template", email_template)
		subject = frappe.render_template(et.subject or "Invoice {0}".format(invoice), context)
		body_template = et.response_html if et.use_html else et.response
		message = frappe.render_template(body_template or "", context)
	finally:
		frappe.local.lang = prev_lang

	try:
		frappe.sendmail(
			recipients=[recipient],
			cc=cc_emails or None,
			subject=subject,
			message=message,
			attachments=[{"fname": f"{invoice}.pdf", "fcontent": pdf}],
			reference_doctype="Sales Invoice",
			reference_name=invoice,
		)
		_log(si.customer, invoice, "Success", recipient=recipient, triggered_by=triggered_by)
		return {"invoice": invoice, "status": "Success", "recipient": recipient}
	except Exception:
		err = frappe.get_traceback()
		frappe.log_error(title="Send Sales Invoice: email failed", message=err)
		_log(si.customer, invoice, "Failed", recipient=recipient, error=err[:1000], triggered_by=triggered_by)
		return {"invoice": invoice, "status": "Failed"}


def run_send_invoices(invoices, **kwargs):
	for invoice in invoices:
		try:
			send_invoice(invoice, **kwargs)
		except Exception:
			frappe.log_error(
				title="Send Sales Invoice: run failed",
				message=f"{invoice}\n{frappe.get_traceback()}",
			)
	frappe.db.commit()
	return {"invoices": len(invoices)}


@frappe.whitelist()
def send_invoice_now(invoice, email_template=None, print_format=None, language=None, cc=None):
	"""Form button: send a single invoice synchronously and report the result."""
	result = send_invoice(
		invoice,
		email_template=email_template,
		print_format=print_format,
		language=language,
		cc=cc,
		triggered_by=f"Sales Invoice: {frappe.session.user}",
	)
	if result["status"] == "Success":
		frappe.msgprint(_("Invoice {0} sent to {1}.").format(invoice, result["recipient"]),
			indicator="green", alert=True)
	elif result["status"] == "Skipped":
		frappe.msgprint(_("Skipped: {0}").format(result.get("reason")), indicator="orange")
	else:
		frappe.msgprint(_("Sending failed. Check the Error Log."), indicator="red")
	return result


@frappe.whitelist()
def send_invoices(invoices, email_template=None, print_format=None, language=None):
	"""List bulk action: queue sending for the selected invoices."""
	if isinstance(invoices, str):
		invoices = json.loads(invoices)
	invoices = [i for i in invoices if i]
	if not invoices:
		frappe.throw(_("Select at least one invoice."))

	frappe.enqueue(
		"customer_notification.customer_notification.sales_invoice.run_send_invoices",
		queue="long",
		timeout=3600,
		invoices=invoices,
		email_template=email_template,
		print_format=print_format,
		language=language,
		triggered_by=f"Sales Invoice Bulk: {frappe.session.user}",
	)
	return {"message": _("Queued {0} invoice(s) for sending.").format(len(invoices))}


@frappe.whitelist()
def download_my_invoice(invoice, print_format=None):
	"""Portal download: stream an invoice PDF, but only if it belongs to the
	logged-in customer (used by the My Invoices page)."""
	from erpnext.controllers.website_list_for_contact import get_parents_for_user

	customer = frappe.db.get_value("Sales Invoice", invoice, "customer")
	if not customer or customer not in (get_parents_for_user("Customer") or []):
		raise frappe.PermissionError(_("You are not allowed to access this invoice."))

	pf = print_format or frappe.db.get_single_value(
		"Customer Notification Settings", "sales_invoice_print_format"
	)
	pdf = frappe.get_print("Sales Invoice", invoice, print_format=pf or None, as_pdf=True)
	frappe.local.response.filename = f"{invoice}.pdf"
	frappe.local.response.filecontent = pdf
	frappe.local.response.type = "pdf"
