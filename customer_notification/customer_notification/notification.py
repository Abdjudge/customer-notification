# Copyright (c) 2026, a and contributors
# For license information, please see license.txt
"""Core engine: build overdue-invoice notifications and dispatch them via Email / WhatsApp."""

import frappe
from frappe import _
from frappe.utils import (
	cint,
	date_diff,
	flt,
	fmt_money,
	format_date,
	get_url,
	now_datetime,
	today,
)

DEFAULT_TEMPLATE = """
<p>Dear {{ customer_name }},</p>
<p>Our records show the following overdue invoice(s) on your account. We kindly request settlement at your earliest convenience.</p>
{{ invoices_table }}
<p><b>Total Outstanding: {{ total_outstanding_formatted }}</b></p>
<p>Please find the invoice(s) attached. If you have already made the payment, kindly ignore this message.</p>
<p>Thank you,<br>{{ company }}</p>
"""


def get_overdue_invoices(customer):
	"""Return submitted Sales Invoices for `customer` that are past due and not fully paid."""
	rows = frappe.get_all(
		"Sales Invoice",
		filters={
			"customer": customer,
			"docstatus": 1,
			"outstanding_amount": (">", 0),
			"due_date": ("<", today()),
		},
		fields=[
			"name",
			"due_date",
			"grand_total",
			"outstanding_amount",
			"currency",
			"company",
		],
		order_by="due_date asc",
	)

	invoices = []
	for r in rows:
		invoices.append(
			{
				"invoice_no": r.name,
				"due_date": r.due_date,
				"due_date_formatted": format_date(r.due_date),
				"delay_days": date_diff(today(), r.due_date),
				"grand_total": flt(r.grand_total),
				"outstanding_amount": flt(r.outstanding_amount),
				"currency": r.currency,
				"company": r.company,
			}
		)
	return invoices


def build_invoices_table(invoices, currency=None):
	"""Render the overdue invoices as an HTML table for the email body."""
	head = (
		"<table border='1' cellpadding='6' cellspacing='0' "
		"style='border-collapse:collapse;width:100%;font-size:13px'>"
		"<thead><tr style='background:#f5f5f5'>"
		"<th>Invoice No</th><th>Due Date</th><th>Delay (Days)</th>"
		"<th>Invoice Total</th><th>Remaining Amount</th>"
		"</tr></thead><tbody>"
	)
	body = ""
	for inv in invoices:
		cur = inv.get("currency") or currency
		body += (
			"<tr>"
			f"<td>{frappe.utils.escape_html(inv['invoice_no'])}</td>"
			f"<td>{inv['due_date_formatted']}</td>"
			f"<td style='text-align:center'>{inv['delay_days']}</td>"
			f"<td style='text-align:right'>{fmt_money(inv['grand_total'], currency=cur)}</td>"
			f"<td style='text-align:right'>{fmt_money(inv['outstanding_amount'], currency=cur)}</td>"
			"</tr>"
		)
	return head + body + "</tbody></table>"


def build_plain_summary(invoices, currency=None):
	"""Plain-text version of the table for WhatsApp (no HTML rendering there)."""
	lines = ["Overdue Invoices:"]
	for inv in invoices:
		cur = inv.get("currency") or currency
		lines.append(
			f"- {inv['invoice_no']} | due {inv['due_date_formatted']} | "
			f"{inv['delay_days']} day(s) late | "
			f"remaining {fmt_money(inv['outstanding_amount'], currency=cur)}"
		)
	return "\n".join(lines)


def _render(template, context):
	return frappe.render_template(template or DEFAULT_TEMPLATE, context)


def emails_for_roles(roles):
	"""Return the email addresses of all enabled users holding any of `roles`."""
	if not roles:
		return []
	if isinstance(roles, str):
		roles = [roles]
	user_ids = frappe.get_all(
		"Has Role",
		filters={"role": ("in", roles), "parenttype": "User"},
		pluck="parent",
		distinct=True,
	)
	if not user_ids:
		return []
	rows = frappe.get_all(
		"User",
		filters={"name": ("in", user_ids), "enabled": 1},
		fields=["name", "email"],
	)
	emails = []
	for r in rows:
		if r.name in ("Guest", "Administrator"):
			continue
		addr = r.email or r.name
		if addr and "@" in addr:
			emails.append(addr)
	return emails


def dedupe_emails(addresses, exclude=None):
	"""Case-insensitive de-duplication preserving order; drops `exclude` addresses."""
	seen = {e.lower() for e in (exclude or [])}
	result = []
	for addr in addresses:
		key = addr.lower()
		if key not in seen:
			seen.add(key)
			result.append(addr)
	return result


def resolve_cc(nt, customer_doc=None):
	"""Build the list of CC email addresses for a notification type.

	Combines static addresses, linked Users' emails, and the emails of every
	enabled user holding any of the selected CC Roles. De-duplicated.
	"""
	cc = []

	# 1. Static addresses (comma / newline separated)
	if nt.cc_emails:
		for part in nt.cc_emails.replace("\n", ",").split(","):
			addr = part.strip()
			if addr:
				cc.append(addr)

	# 2. Linked Users -> their email
	for row in nt.get("cc_users") or []:
		if not row.user:
			continue
		email = frappe.db.get_value("User", row.user, "email") or row.user
		if email:
			cc.append(email)

	# 3. Users holding the selected roles
	roles = [r.role for r in (nt.get("cc_roles") or []) if r.role]
	cc.extend(emails_for_roles(roles))

	return dedupe_emails(cc)


def _build_context(notification_type, customer_doc, invoices):
	currency = invoices[0]["currency"] if invoices else None
	total_outstanding = sum(i["outstanding_amount"] for i in invoices)
	return {
		"customer": customer_doc,
		"customer_name": customer_doc.customer_name or customer_doc.name,
		"invoices": invoices,
		"invoices_table": build_invoices_table(invoices, currency),
		"plain_summary": build_plain_summary(invoices, currency),
		"total_outstanding": total_outstanding,
		"total_outstanding_formatted": fmt_money(total_outstanding, currency=currency),
		"currency": currency,
		"company": invoices[0]["company"] if invoices else frappe.defaults.get_user_default("Company"),
	}


def _log(notification_type, customer, channel, status, **kwargs):
	doc = frappe.new_doc("Customer Notification Log")
	doc.notification_type = notification_type
	doc.customer = customer
	doc.channel = channel
	doc.status = status
	doc.sent_on = now_datetime()
	for k, v in kwargs.items():
		doc.set(k, v)
	doc.flags.ignore_permissions = True
	doc.insert(ignore_permissions=True)
	return doc


def _generate_pdfs(invoices, print_format=None):
	"""Return [(filename, pdf_bytes), ...] for each invoice."""
	attachments = []
	for inv in invoices:
		try:
			pdf = frappe.get_print(
				"Sales Invoice",
				inv["invoice_no"],
				print_format=print_format or None,
				as_pdf=True,
			)
			attachments.append((f"{inv['invoice_no']}.pdf", pdf))
		except Exception:
			frappe.log_error(
				title="Customer Notification: PDF generation failed",
				message=f"Invoice {inv['invoice_no']}\n{frappe.get_traceback()}",
			)
	return attachments


def send_for_customer(notification_type, customer, triggered_by="Manual"):
	"""Build and dispatch the overdue-invoice notification for a single customer.

	Returns a dict describing what happened. Failures are logged, not raised.
	"""
	nt = frappe.get_doc("Notification Type", notification_type)
	customer_doc = frappe.get_doc("Customer", customer)
	invoices = get_overdue_invoices(customer)

	if not invoices:
		_log(
			notification_type,
			customer,
			"Email" if nt.send_email else "WhatsApp",
			"Skipped",
			invoice_count=0,
			error="No overdue invoices.",
			triggered_by=triggered_by,
		)
		return {"customer": customer, "status": "Skipped", "reason": "no overdue invoices"}

	context = _build_context(nt, customer_doc, invoices)
	total_outstanding = context["total_outstanding"]
	results = []

	# ---- Email ----
	if nt.send_email:
		email = customer_doc.email_id
		if not email:
			_log(
				notification_type, customer, "Email", "Skipped",
				invoice_count=len(invoices), total_outstanding=total_outstanding,
				error="No primary contact email on customer.", triggered_by=triggered_by,
			)
			results.append({"channel": "Email", "status": "Skipped", "reason": "no email"})
		else:
			try:
				body = _render(nt.message_template, context)
				subject = _render(nt.subject or "Overdue Invoices", context)
				attachments = None
				if nt.attach_invoice_pdf:
					attachments = [
						{"fname": fname, "fcontent": fcontent}
						for fname, fcontent in _generate_pdfs(invoices, nt.print_format)
					]
				cc = resolve_cc(nt, customer_doc)
				# don't CC the same address we're sending To
				cc = [c for c in cc if c.lower() != email.lower()]
				frappe.sendmail(
					recipients=[email],
					cc=cc or None,
					subject=subject,
					message=body,
					attachments=attachments,
					reference_doctype="Notification Type",
					reference_name=notification_type,
				)
				recipient_display = email if not cc else "{} (cc: {})".format(email, ", ".join(cc))
				_log(
					notification_type, customer, "Email", "Success",
					recipient=recipient_display, invoice_count=len(invoices),
					total_outstanding=total_outstanding, triggered_by=triggered_by,
				)
				results.append({"channel": "Email", "status": "Success", "recipient": email, "cc": cc})
			except Exception:
				err = frappe.get_traceback()
				frappe.log_error(title="Customer Notification: email failed", message=err)
				_log(
					notification_type, customer, "Email", "Failed",
					recipient=email, invoice_count=len(invoices),
					total_outstanding=total_outstanding, error=err[:1000],
					triggered_by=triggered_by,
				)
				results.append({"channel": "Email", "status": "Failed"})

	# ---- WhatsApp ----
	if nt.send_whatsapp:
		results.append(_send_whatsapp(nt, customer_doc, invoices, context, triggered_by))

	return {"customer": customer, "status": "Sent", "results": results}


def _send_whatsapp(nt, customer_doc, invoices, context, triggered_by):
	notification_type = nt.name
	customer = customer_doc.name
	total_outstanding = context["total_outstanding"]

	if not frappe.db.exists("DocType", "WhatsApp Message"):
		_log(
			notification_type, customer, "WhatsApp", "Skipped",
			invoice_count=len(invoices), total_outstanding=total_outstanding,
			error="frappe_whatsapp app / WhatsApp Message doctype not available.",
			triggered_by=triggered_by,
		)
		return {"channel": "WhatsApp", "status": "Skipped", "reason": "whatsapp not installed"}

	mobile = customer_doc.mobile_no
	if not mobile:
		_log(
			notification_type, customer, "WhatsApp", "Skipped",
			invoice_count=len(invoices), total_outstanding=total_outstanding,
			error="No mobile number on customer primary contact.", triggered_by=triggered_by,
		)
		return {"channel": "WhatsApp", "status": "Skipped", "reason": "no mobile"}

	try:
		# Text summary
		text = (
			f"Dear {context['customer_name']},\n\n"
			f"{context['plain_summary']}\n\n"
			f"Total Outstanding: {context['total_outstanding_formatted']}\n\n"
			f"Kindly arrange settlement. Thank you."
		)
		msg = frappe.new_doc("WhatsApp Message")
		msg.type = "Outgoing"
		msg.to = mobile
		msg.message_type = "Manual"
		msg.content_type = "text"
		msg.message = text
		msg.flags.ignore_permissions = True
		msg.insert(ignore_permissions=True)

		# One document message per invoice PDF
		if nt.attach_invoice_pdf:
			for fname, fcontent in _generate_pdfs(invoices, nt.print_format):
				file_doc = frappe.get_doc(
					{
						"doctype": "File",
						"file_name": fname,
						"is_private": 0,
						"content": fcontent,
					}
				).insert(ignore_permissions=True)
				docmsg = frappe.new_doc("WhatsApp Message")
				docmsg.type = "Outgoing"
				docmsg.to = mobile
				docmsg.message_type = "Manual"
				docmsg.content_type = "document"
				docmsg.attach = file_doc.file_url
				docmsg.message = fname
				docmsg.flags.ignore_permissions = True
				docmsg.insert(ignore_permissions=True)

		_log(
			notification_type, customer, "WhatsApp", "Success",
			recipient=mobile, invoice_count=len(invoices),
			total_outstanding=total_outstanding, triggered_by=triggered_by,
		)
		return {"channel": "WhatsApp", "status": "Success", "recipient": mobile}
	except Exception:
		err = frappe.get_traceback()
		frappe.log_error(title="Customer Notification: whatsapp failed", message=err)
		_log(
			notification_type, customer, "WhatsApp", "Failed",
			recipient=mobile, invoice_count=len(invoices),
			total_outstanding=total_outstanding, error=err[:1000],
			triggered_by=triggered_by,
		)
		return {"channel": "WhatsApp", "status": "Failed"}


# ------------------------------------------------------------------
# Background dispatchers
# ------------------------------------------------------------------
def _customers_of_type(notification_type):
	rows = frappe.get_all(
		"Notification Type Customer",
		filters={"parent": notification_type, "parenttype": "Notification Type"},
		pluck="customer",
	)
	# de-duplicate, keep order
	seen = set()
	return [c for c in rows if not (c in seen or seen.add(c))]


def run_for_type(notification_type, triggered_by="Manual"):
	"""Send the notification to every customer of the given type. Meant to run in a job."""
	customers = _customers_of_type(notification_type)
	for customer in customers:
		try:
			send_for_customer(notification_type, customer, triggered_by=triggered_by)
		except Exception:
			frappe.log_error(
				title="Customer Notification: run_for_type failed",
				message=f"{notification_type} / {customer}\n{frappe.get_traceback()}",
			)
	frappe.db.commit()
	return {"customers": len(customers)}


def enqueue_send_for_type(notification_type, triggered_by="Manual"):
	customers = _customers_of_type(notification_type)
	if not customers:
		frappe.msgprint(_("This notification type has no customers."))
		return {"message": _("No customers linked to this type.")}
	frappe.enqueue(
		"customer_notification.customer_notification.notification.run_for_type",
		queue="long",
		notification_type=notification_type,
		triggered_by=triggered_by,
		timeout=3600,
	)
	return {"message": _("Queued notifications for {0} customer(s).").format(len(customers))}


def enqueue_send_for_customer(notification_type, customer, triggered_by="Manual"):
	frappe.enqueue(
		"customer_notification.customer_notification.notification.send_for_customer",
		queue="default",
		notification_type=notification_type,
		customer=customer,
		triggered_by=triggered_by,
		timeout=1200,
	)
	return {"message": _("Queued notification for {0}.").format(customer)}


# ------------------------------------------------------------------
# Whitelisted entrypoints (used by client buttons)
# ------------------------------------------------------------------
@frappe.whitelist()
def get_customer_notification_types(customer):
	"""Return the Notification Types a customer belongs to (enabled only)."""
	parents = frappe.get_all(
		"Notification Type Customer",
		filters={"customer": customer, "parenttype": "Notification Type"},
		pluck="parent",
	)
	if not parents:
		return []
	return frappe.get_all(
		"Notification Type",
		filters={"name": ("in", parents), "enabled": 1},
		fields=["name", "title", "send_email", "send_whatsapp"],
	)


@frappe.whitelist()
def push_for_customer(customer, notification_type=None):
	"""Custom-button entrypoint on Customer. Sends for one or all of the customer's types."""
	if notification_type:
		types = [notification_type]
	else:
		types = [t["name"] for t in get_customer_notification_types(customer)]

	if not types:
		frappe.throw(_("This customer is not linked to any enabled Notification Type."))

	for nt in types:
		enqueue_send_for_customer(nt, customer, triggered_by=f"Customer: {frappe.session.user}")

	return {"message": _("Queued {0} notification(s) for {1}.").format(len(types), customer)}
