# Copyright (c) 2026, a and contributors
# For license information, please see license.txt
"""Outbound webhook engine for the Collection App integration.

Architecture
------------
Doc events (Customer, Sales Invoice, …) call `fire()`.
`fire()` builds the standard event envelope and enqueues `_deliver_and_log()`
as a background job so the user-facing save is never blocked.
The background job creates the ERP Webhook Log entry, POSTs to the Collection
App, and updates the log with the result.

Envelope format (matches the document spec):
  {
    "eventId":      "<uuid>",
    "eventType":    "customer.created",
    "occurredAt":   "2026-06-26T10:00:00Z",
    "sourceSystem": "ERPNext",
    "erpCompany":   "Global IT",
    "data":         { … },
    "metadata":     { "erpUser": "…" }
  }
"""

import uuid

import frappe
from frappe.utils import flt, get_url, now_datetime


# ---------------------------------------------------------------------------
# Settings helper
# ---------------------------------------------------------------------------

def _get_settings():
    try:
        return frappe.get_cached_doc("Collection App Settings")
    except Exception:
        return frappe._dict({"enabled": 0})


# ---------------------------------------------------------------------------
# Core: envelope + fire
# ---------------------------------------------------------------------------

def _build_envelope(event_type, data, metadata=None):
    s = _get_settings()
    return {
        "eventId": str(uuid.uuid4()),
        "eventType": event_type,
        "occurredAt": now_datetime().isoformat() + "Z",
        "sourceSystem": s.source_system or "ERPNext",
        "erpCompany": s.erp_company or None,
        "data": data,
        "metadata": metadata or {"erpUser": frappe.session.user},
    }


def fire(event_type, data, ref_doctype=None, ref_name=None, metadata=None):
    """Build the envelope and enqueue delivery. Safe to call inside doc events."""
    s = _get_settings()
    if not s.enabled or not s.get("collection_app_webhook_url"):
        return

    payload = _build_envelope(event_type, data, metadata)

    frappe.enqueue(
        "customer_notification.customer_notification.webhook._deliver_and_log",
        queue="default",
        payload=payload,
        ref_doctype=ref_doctype or "",
        ref_name=ref_name or "",
        timeout=90,
        now=frappe.flags.in_test,
        enqueue_after_commit=True,
    )


# ---------------------------------------------------------------------------
# Background delivery
# ---------------------------------------------------------------------------

def _deliver_and_log(payload, ref_doctype="", ref_name=""):
    """Background: create log entry, POST to Collection App, update log."""
    import requests

    settings = frappe.get_doc("Collection App Settings")
    url = settings.collection_app_webhook_url
    secret = settings.get_password("webhook_secret") if settings.webhook_secret else None

    headers = {"Content-Type": "application/json"}
    if secret:
        headers["Authorization"] = f"Bearer {secret}"

    # Create log
    log = frappe.new_doc("ERP Webhook Log")
    log.event_id = payload["eventId"]
    log.event_type = payload["eventType"]
    log.status = "Pending"
    log.reference_doctype = ref_doctype
    log.reference_name = ref_name
    log.payload = frappe.as_json(payload)
    log.sent_on = now_datetime()
    log.attempts = 1
    log.flags.ignore_permissions = True
    log.insert(ignore_permissions=True)
    frappe.db.commit()

    try:
        resp = requests.post(url, json=payload, headers=headers, timeout=30)
        log.response_code = resp.status_code
        log.response_body = (resp.text or "")[:500]
        if resp.status_code in (200, 201, 202):
            log.status = "Delivered"
            log.delivered_on = now_datetime()
        else:
            log.status = "Failed"
            log.error = f"HTTP {resp.status_code}: {(resp.text or '')[:200]}"
    except Exception:
        log.status = "Failed"
        log.error = frappe.get_traceback()[:500]
        frappe.log_error(
            title="ERP Webhook: delivery failed",
            message=f"Event: {payload.get('eventType')}\nLog: {log.name}\n\n{frappe.get_traceback()}",
        )

    log.flags.ignore_permissions = True
    log.save(ignore_permissions=True)
    frappe.db.commit()


def _retry_delivery(log_name):
    """Retry a previously failed webhook log entry."""
    import requests

    log = frappe.get_doc("ERP Webhook Log", log_name)
    payload = frappe.parse_json(log.payload)

    settings = frappe.get_doc("Collection App Settings")
    url = settings.collection_app_webhook_url
    secret = settings.get_password("webhook_secret") if settings.webhook_secret else None

    headers = {"Content-Type": "application/json"}
    if secret:
        headers["Authorization"] = f"Bearer {secret}"

    log.attempts = (log.attempts or 0) + 1

    try:
        resp = requests.post(url, json=payload, headers=headers, timeout=30)
        log.response_code = resp.status_code
        log.response_body = (resp.text or "")[:500]
        if resp.status_code in (200, 201, 202):
            log.status = "Delivered"
            log.delivered_on = now_datetime()
        else:
            log.status = "Failed"
            log.error = f"HTTP {resp.status_code}: {(resp.text or '')[:200]}"
    except Exception:
        log.status = "Failed"
        log.error = frappe.get_traceback()[:500]
        frappe.log_error(
            title="ERP Webhook: retry failed",
            message=f"Log: {log_name}\n\n{frappe.get_traceback()}",
        )

    log.flags.ignore_permissions = True
    log.save(ignore_permissions=True)
    frappe.db.commit()


# ---------------------------------------------------------------------------
# Data builders — map ERPNext doc fields to the Collection App schema
# ---------------------------------------------------------------------------

def _customer_data(doc):
    return {
        "customerCode": doc.name,
        "customerName": doc.customer_name,
        "status": "Inactive" if doc.disabled else "Active",
        "accountType": doc.get("account_type") or None,
        "kamCode": doc.get("kam_code") or None,
        "kamName": doc.get("kam_name") or None,
        "salesLead": doc.get("sales_lead") or None,
        "billingEmail": doc.email_id or None,
        "phone": doc.mobile_no or doc.phone or None,
        "customerGroup": doc.customer_group or None,
        "territory": doc.territory or None,
        "createdAt": str(doc.creation) if doc.creation else None,
        "serviceActivationDate": str(doc.get("service_activation_date"))
        if doc.get("service_activation_date")
        else None,
    }


def _contact_data(doc):
    customer_code = None
    for link in doc.links or []:
        if link.link_doctype == "Customer":
            customer_code = link.link_name
            break

    email = doc.email_id
    if not email and getattr(doc, "email_ids", None):
        email = doc.email_ids[0].email_id if doc.email_ids else None

    phone = doc.phone
    if not phone and getattr(doc, "phone_nos", None):
        phone = doc.phone_nos[0].phone if doc.phone_nos else None

    return {
        "contactId": doc.name,
        "firstName": doc.first_name or None,
        "lastName": doc.last_name or None,
        "fullName": doc.full_name or None,
        "email": email or None,
        "phone": phone or None,
        "mobile": doc.mobile_no or None,
        "customerCode": customer_code,
    }


def _sales_order_data(doc):
    return {
        "salesOrderNo": doc.name,
        "customerCode": doc.customer,
        "status": doc.status,
        "isAddon": bool(doc.get("is_addon")),
        "orderDate": str(doc.transaction_date) if doc.transaction_date else None,
        "totalAmount": flt(doc.grand_total),
        "currency": doc.currency,
        "company": doc.company,
        "createdBy": doc.owner,
        "items": [
            {
                "itemCode": item.item_code,
                "itemName": item.item_name,
                "qty": flt(item.qty),
                "rate": flt(item.rate),
                "amount": flt(item.amount),
                "uom": item.uom or None,
            }
            for item in (doc.items or [])
        ],
    }


def _invoice_data(doc):
    so_nos = list({
        item.get("sales_order")
        for item in (doc.items or [])
        if item.get("sales_order")
    })
    return {
        "invoiceNo": doc.name,
        "customerCode": doc.customer,
        "invoiceDate": str(doc.posting_date) if doc.posting_date else None,
        "dueDate": str(doc.due_date) if doc.due_date else None,
        "status": doc.status,
        "totalAmount": flt(doc.grand_total),
        "paidAmount": flt(doc.grand_total) - flt(doc.outstanding_amount),
        "outstandingAmount": flt(doc.outstanding_amount),
        "currency": doc.currency,
        "company": doc.company,
        "salesOrderNo": so_nos[0] if so_nos else None,
        "isCreditNote": bool(doc.is_return),
        "returnAgainstInvoice": doc.return_against if doc.is_return else None,
        "lineItems": [
            {
                "itemCode": item.item_code,
                "itemName": item.item_name,
                "itemGroup": item.item_group or None,
                "qty": flt(item.qty),
                "rate": flt(item.rate),
                "amount": flt(item.amount),
                "uom": item.uom or None,
            }
            for item in (doc.items or [])
        ],
    }


def _pdf_data(doc):
    s = _get_settings()
    pf = s.get("invoice_print_format") or ""
    pf_param = f"&format={pf}" if pf else ""
    pdf_url = (
        f"{get_url()}/api/method/frappe.utils.pdf.get_pdf"
        f"?doctype=Sales+Invoice&name={doc.name}{pf_param}"
    )
    return {
        "invoiceNo": doc.name,
        "pdfUrl": pdf_url,
        "pdfGeneratedAt": now_datetime().isoformat() + "Z",
        "version": 1,
    }


def _payment_data(doc):
    allocations = [
        {
            "invoiceNo": ref.reference_name,
            "allocatedAmount": flt(ref.allocated_amount),
            "outstandingAmount": flt(ref.outstanding_amount),
        }
        for ref in (doc.references or [])
        if ref.reference_doctype == "Sales Invoice"
    ]
    return {
        "paymentId": doc.name,
        "paymentDate": str(doc.posting_date) if doc.posting_date else None,
        "customerCode": doc.party if doc.party_type == "Customer" else None,
        "amount": flt(doc.paid_amount),
        "currency": (
            doc.get("paid_from_account_currency")
            or doc.get("paid_to_account_currency")
            or doc.get("currency")
            or "SAR"
        ),
        "status": doc.status,
        "referenceNo": doc.reference_no or None,
        "company": doc.company,
        "allocatedInvoices": allocations,
    }


def _item_data(doc):
    return {
        "itemCode": doc.name,
        "itemName": doc.item_name,
        "itemGroup": doc.item_group or None,
        "description": (doc.description or "")[:200] or None,
        "disabled": bool(doc.disabled),
        "uom": doc.stock_uom or None,
    }


# ---------------------------------------------------------------------------
# Doc event handlers
# ---------------------------------------------------------------------------

def on_customer_insert(doc, method=None):
    s = _get_settings()
    if not s.enabled or not s.get("send_customer_events"):
        return
    fire("customer.created", _customer_data(doc), "Customer", doc.name)


def on_customer_update(doc, method=None):
    s = _get_settings()
    if not s.enabled or not s.get("send_customer_events"):
        return
    fire("customer.updated", _customer_data(doc), "Customer", doc.name)


def on_contact_change(doc, method=None):
    s = _get_settings()
    if not s.enabled or not s.get("send_customer_events"):
        return
    has_customer = any(
        lnk.link_doctype == "Customer" for lnk in (doc.links or [])
    )
    if not has_customer:
        return
    event_type = "contact.created" if method == "after_insert" else "contact.updated"
    fire(event_type, _contact_data(doc), "Contact", doc.name)


def on_sales_order_insert(doc, method=None):
    s = _get_settings()
    if not s.enabled or not s.get("send_sales_order_events"):
        return
    fire("sales_order.created_draft", _sales_order_data(doc), "Sales Order", doc.name)


def on_sales_order_submit(doc, method=None):
    s = _get_settings()
    if not s.enabled or not s.get("send_sales_order_events"):
        return
    fire("sales_order.submitted", _sales_order_data(doc), "Sales Order", doc.name)


def on_sales_order_cancel(doc, method=None):
    s = _get_settings()
    if not s.enabled or not s.get("send_sales_order_events"):
        return
    fire("sales_order.cancelled", _sales_order_data(doc), "Sales Order", doc.name)


def on_invoice_insert(doc, method=None):
    s = _get_settings()
    if not s.enabled or not s.get("send_invoice_events"):
        return
    event_type = "credit_note.created" if doc.is_return else "invoice.created"
    fire(event_type, _invoice_data(doc), "Sales Invoice", doc.name)


def on_invoice_submit(doc, method=None):
    s = _get_settings()
    if not s.enabled or not s.get("send_invoice_events"):
        return
    event_type = "credit_note.submitted" if doc.is_return else "invoice.submitted"
    fire(event_type, _invoice_data(doc), "Sales Invoice", doc.name)

    if s.get("send_pdf_webhook") and not doc.is_return:
        fire("invoice.pdf_ready", _pdf_data(doc), "Sales Invoice", doc.name)


def on_invoice_update(doc, method=None):
    s = _get_settings()
    if not s.enabled or not s.get("send_invoice_events"):
        return
    if doc.docstatus != 1:
        return

    # Only fire if financially relevant fields changed to reduce noise
    before = doc.get_doc_before_save()
    if before:
        watched = ("outstanding_amount", "due_date", "status", "grand_total")
        if not any(
            str(getattr(doc, f, "")) != str(getattr(before, f, ""))
            for f in watched
        ):
            return

    event_type = "credit_note.updated" if doc.is_return else "invoice.updated"
    fire(event_type, _invoice_data(doc), "Sales Invoice", doc.name)


def on_invoice_cancel(doc, method=None):
    s = _get_settings()
    if not s.enabled or not s.get("send_invoice_events"):
        return
    event_type = "credit_note.cancelled" if doc.is_return else "invoice.cancelled"
    fire(event_type, _invoice_data(doc), "Sales Invoice", doc.name)


def on_payment_submit(doc, method=None):
    s = _get_settings()
    if not s.enabled or not s.get("send_payment_events"):
        return
    if doc.party_type != "Customer":
        return
    fire("payment.submitted", _payment_data(doc), "Payment Entry", doc.name)


def on_payment_cancel(doc, method=None):
    s = _get_settings()
    if not s.enabled or not s.get("send_payment_events"):
        return
    if doc.party_type != "Customer":
        return
    fire("payment.cancelled", _payment_data(doc), "Payment Entry", doc.name)


def on_item_change(doc, method=None):
    s = _get_settings()
    if not s.enabled or not s.get("send_item_events"):
        return
    event_type = "item.created" if method == "after_insert" else "item.updated"
    fire(event_type, _item_data(doc), "Item", doc.name)
