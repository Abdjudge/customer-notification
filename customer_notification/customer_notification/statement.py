# Copyright (c) 2026, a and contributors
# For license information, please see license.txt
"""Per-customer Statement of Account / Account Reconciliation dispatch.

Driven by the "Customer Statement Dispatch" report action. For each selected
customer we build an in-memory Process Statement Of Accounts (PSOA) document,
render that customer's PDF via ERPNext's PSOA engine, render the chosen Email
Template, and email it (optionally with CC). Every send is recorded in the
Customer Notification Log.
"""

import json

import frappe
from frappe import _
from frappe.utils import now_datetime

from erpnext.accounts.doctype.process_statement_of_accounts.process_statement_of_accounts import (
	get_report_pdf,
)

from customer_notification.customer_notification.notification import dedupe_emails, emails_for_roles

# Dialog labels -> PSOA report type
REPORT_TYPE_MAP = {
	"Statement of Account": "General Ledger",
	"Customer Account Reconciliation": "Accounts Receivable",
}

LANGUAGE_MAP = {"English": "en", "Arabic": "ar"}


def _build_psoa(customer, customer_name, company, from_date, to_date, report, include_ageing):
	"""Create (unsaved) PSOA doc scoped to a single customer."""
	doc = frappe.new_doc("Process Statement Of Accounts")
	doc.company = company
	doc.from_date = from_date
	doc.to_date = to_date
	doc.posting_date = to_date
	doc.report = report
	doc.ageing_based_on = "Due Date"
	doc.include_ageing = 1 if include_ageing else 0
	doc.include_break = 0
	doc.orientation = "Landscape" if report == "Accounts Receivable" else "Portrait"
	doc.append("customers", {"customer": customer, "customer_name": customer_name})
	return doc


def _generate_pdf(customer, customer_name, company, from_date, to_date, report, include_ageing, lang):
	"""Return PDF bytes for one customer, rendered in `lang`, or None if no data."""
	doc = _build_psoa(customer, customer_name, company, from_date, to_date, report, include_ageing)
	prev_lang = frappe.local.lang
	try:
		frappe.local.lang = lang or "en"
		result = get_report_pdf(doc, consolidated=False)
	finally:
		frappe.local.lang = prev_lang
	if not result:
		return None
	return result.get(customer)


def _resolve_cc(cc_emails, cc_roles, recipient):
	cc = []
	if cc_emails:
		for part in cc_emails.replace("\n", ",").split(","):
			addr = part.strip()
			if addr:
				cc.append(addr)
	cc.extend(emails_for_roles(cc_roles))
	return dedupe_emails(cc, exclude=[recipient] if recipient else None)


def _log(customer, status, **kwargs):
	doc = frappe.new_doc("Customer Notification Log")
	doc.customer = customer
	doc.channel = "Email"
	doc.status = status
	doc.sent_on = now_datetime()
	for k, v in kwargs.items():
		doc.set(k, v)
	doc.flags.ignore_permissions = True
	doc.insert(ignore_permissions=True)


def send_statement_for_customer(
	customer,
	company,
	from_date,
	to_date,
	report,
	email_template,
	language="en",
	include_ageing=1,
	cc_emails=None,
	cc_roles=None,
	triggered_by="Statement",
):
	"""Generate and email a single customer's statement. Failures are logged, not raised."""
	customer_doc = frappe.get_doc("Customer", customer)
	doc_label = "Customer Account Reconciliation" if report == "Accounts Receivable" else "Statement of Account"

	recipient = customer_doc.email_id
	if not recipient:
		_log(customer, "Skipped", recipient=doc_label, error="No primary contact email on customer.",
			triggered_by=triggered_by)
		return {"customer": customer, "status": "Skipped", "reason": "no email"}

	try:
		pdf = _generate_pdf(
			customer, customer_doc.customer_name, company, from_date, to_date,
			report, include_ageing, language,
		)
	except Exception:
		err = frappe.get_traceback()
		frappe.log_error(title="Customer Statement: PDF failed", message=err)
		_log(customer, "Failed", recipient=recipient, error=err[:1000], triggered_by=triggered_by)
		return {"customer": customer, "status": "Failed", "stage": "pdf"}

	if not pdf:
		_log(customer, "Skipped", recipient=recipient,
			error=f"No {doc_label} data for the selected period.", triggered_by=triggered_by)
		return {"customer": customer, "status": "Skipped", "reason": "no data"}

	context = {
		"customer": customer_doc,
		"customer_name": customer_doc.customer_name or customer,
		"company": company,
		"from_date": from_date,
		"to_date": to_date,
		"document_type": doc_label,
	}
	try:
		et = frappe.get_doc("Email Template", email_template)
		subject = frappe.render_template(et.subject or doc_label, context)
		body_template = et.response_html if et.use_html else et.response
		message = frappe.render_template(body_template or "", context)

		cc = _resolve_cc(cc_emails, cc_roles, recipient)
		fname = f"{doc_label.replace(' ', '_')}_{customer}.pdf"
		frappe.sendmail(
			recipients=[recipient],
			cc=cc or None,
			subject=subject,
			message=message,
			attachments=[{"fname": fname, "fcontent": pdf}],
			reference_doctype="Customer",
			reference_name=customer,
		)
		recipient_display = recipient if not cc else "{} (cc: {})".format(recipient, ", ".join(cc))
		_log(customer, "Success", recipient=recipient_display, triggered_by=triggered_by)
		return {"customer": customer, "status": "Success"}
	except Exception:
		err = frappe.get_traceback()
		frappe.log_error(title="Customer Statement: email failed", message=err)
		_log(customer, "Failed", recipient=recipient, error=err[:1000], triggered_by=triggered_by)
		return {"customer": customer, "status": "Failed", "stage": "email"}


def run_statements(customers, **kwargs):
	"""Background worker: dispatch statements to a list of customers."""
	for customer in customers:
		try:
			send_statement_for_customer(customer, **kwargs)
		except Exception:
			frappe.log_error(
				title="Customer Statement: run_statements failed",
				message=f"{customer}\n{frappe.get_traceback()}",
			)
	frappe.db.commit()
	return {"customers": len(customers)}


@frappe.whitelist()
def send_statements(
	customers,
	company,
	from_date,
	to_date,
	report_type,
	email_template,
	language="English",
	include_ageing=1,
	cc_emails=None,
	cc_roles=None,
):
	"""Whitelisted entrypoint for the report's 'Send Statement of Account' action."""
	if isinstance(customers, str):
		customers = json.loads(customers)
	customers = [c for c in customers if c]
	if not customers:
		frappe.throw(_("Select at least one customer row."))

	if isinstance(cc_roles, str):
		cc_roles = json.loads(cc_roles) if cc_roles.strip().startswith("[") else (
			[cc_roles] if cc_roles.strip() else []
		)

	report = REPORT_TYPE_MAP.get(report_type, report_type)
	if report not in ("General Ledger", "Accounts Receivable"):
		frappe.throw(_("Invalid statement type: {0}").format(report_type))

	if not email_template:
		frappe.throw(_("Please choose an Email Template."))

	lang = LANGUAGE_MAP.get(language, language or "en")

	frappe.enqueue(
		"customer_notification.customer_notification.statement.run_statements",
		queue="long",
		timeout=3600,
		customers=customers,
		company=company,
		from_date=from_date,
		to_date=to_date,
		report=report,
		email_template=email_template,
		language=lang,
		include_ageing=frappe.utils.cint(include_ageing),
		cc_emails=cc_emails,
		cc_roles=cc_roles,
		triggered_by=f"Statement: {frappe.session.user}",
	)
	return {"message": _("Queued {0} statement(s) for sending.").format(len(customers))}


# ------------------------------------------------------------------
# Default Email Templates (English + Arabic). Idempotent.
# ------------------------------------------------------------------
DEFAULT_TEMPLATES = [
	{
		"name": "Customer Statement (English)",
		"subject": "Account Statement - {{ customer_name }}",
		"response": """<p>Dear {{ customer_name }},</p>
<p>Please find attached your <b>{{ document_type }}</b> from {{ company }} covering the period
<b>{{ from_date }}</b> to <b>{{ to_date }}</b>.</p>
<p>We kindly ask you to review the attached statement and reach out to us should you have any
questions or notice any discrepancies.</p>
<p>Thank you for your continued business.</p>
<p>Best regards,<br>{{ company }}</p>""",
	},
	{
		"name": "Customer Statement (Arabic)",
		"subject": "كشف حساب - {{ customer_name }}",
		"response": """<div dir="rtl" style="text-align:right">
<p>عميلنا العزيز {{ customer_name }}،</p>
<p>نرفق لكم <b>{{ document_type }}</b> الخاص بكم من {{ company }} عن الفترة من
<b>{{ from_date }}</b> إلى <b>{{ to_date }}</b>.</p>
<p>نرجو التكرم بمراجعة الكشف المرفق والتواصل معنا في حال وجود أي استفسار أو ملاحظات.</p>
<p>شاكرين لكم حسن تعاملكم معنا.</p>
<p>مع خالص التحية،<br>{{ company }}</p>
</div>""",
	},
	{
		"name": "Sales Invoice (English)",
		"subject": "Invoice {{ invoice }} from {{ company }}",
		"response": """<p>Dear {{ customer_name }},</p>
<p>Please find attached invoice <b>{{ invoice }}</b> from {{ company }}.</p>
<p>Total: <b>{{ frappe.utils.fmt_money(doc.grand_total, currency=doc.currency) }}</b>
&nbsp;|&nbsp; Outstanding: <b>{{ frappe.utils.fmt_money(doc.outstanding_amount, currency=doc.currency) }}</b>
&nbsp;|&nbsp; Due: <b>{{ due_date }}</b></p>
<p>Thank you for your business.</p>
<p>Best regards,<br>{{ company }}</p>""",
	},
	{
		"name": "Sales Invoice (Arabic)",
		"subject": "فاتورة {{ invoice }} من {{ company }}",
		"response": """<div dir="rtl" style="text-align:right">
<p>عميلنا العزيز {{ customer_name }}،</p>
<p>نرفق لكم الفاتورة رقم <b>{{ invoice }}</b> الصادرة من {{ company }}.</p>
<p>الإجمالي: <b>{{ frappe.utils.fmt_money(doc.grand_total, currency=doc.currency) }}</b>
&nbsp;|&nbsp; المتبقي: <b>{{ frappe.utils.fmt_money(doc.outstanding_amount, currency=doc.currency) }}</b>
&nbsp;|&nbsp; تاريخ الاستحقاق: <b>{{ due_date }}</b></p>
<p>شاكرين لكم حسن تعاملكم معنا.</p>
<p>مع خالص التحية،<br>{{ company }}</p>
</div>""",
	},
]


def create_default_email_templates():
	"""Create the default English/Arabic statement Email Templates if missing."""
	created = []
	for t in DEFAULT_TEMPLATES:
		if frappe.db.exists("Email Template", t["name"]):
			continue
		doc = frappe.new_doc("Email Template")
		doc.name = t["name"]
		doc.subject = t["subject"]
		doc.use_html = 1
		doc.response_html = t["response"]
		doc.response = t["response"]
		doc.insert(ignore_permissions=True)
		created.append(t["name"])
	frappe.db.commit()
	return created
