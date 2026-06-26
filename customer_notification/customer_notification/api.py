# Copyright (c) 2026, a and contributors
# For license information, please see license.txt
"""Whitelisted REST pull-endpoints for the Collection App full-sync, aging, and reconciliation.

All endpoints require an authenticated ERPNext session or API key — the same
credentials used for any other ERPNext REST call.

Endpoint URLs (Frappe whitelist convention):
  GET /api/method/customer_notification.customer_notification.api.get_customers
  GET /api/method/customer_notification.customer_notification.api.get_contacts
  GET /api/method/customer_notification.customer_notification.api.get_invoices
  GET /api/method/customer_notification.customer_notification.api.get_payments
  GET /api/method/customer_notification.customer_notification.api.get_sales_orders
  GET /api/method/customer_notification.customer_notification.api.get_items
  GET /api/method/customer_notification.customer_notification.api.get_aging_snapshot
  GET /api/method/customer_notification.customer_notification.api.get_reconciliation_summary
  GET /api/method/customer_notification.customer_notification.api.get_invoice_pdf_url

All list endpoints support:
  page         (int, default 1)
  page_size    (int, default 200, max 500)
  modified_after  (datetime string, optional — for incremental sync)
"""

import frappe
from frappe.utils import date_diff, flt, get_url, now_datetime, today


def _guard():
    if frappe.session.user == "Guest":
        frappe.throw("Authentication required.", frappe.PermissionError)


def _page_args(page, page_size):
    p = max(1, int(page or 1))
    ps = min(500, max(1, int(page_size or 200)))
    return p, ps, (p - 1) * ps


def _wrap(data, total, page, page_size):
    ps = min(500, max(1, int(page_size)))
    return {
        "page": int(page),
        "page_size": ps,
        "total": total,
        "total_pages": max(1, (total + ps - 1) // ps),
        "data": data,
    }


# ---------------------------------------------------------------------------
# Customers
# ---------------------------------------------------------------------------

@frappe.whitelist()
def get_customers(page=1, page_size=200, modified_after=None):
    """Paginated customer list with all Collection App required fields."""
    _guard()
    page, page_size, offset = _page_args(page, page_size)

    filters = {}
    if modified_after:
        filters["modified"] = (">=", modified_after)

    rows = frappe.get_all(
        "Customer",
        filters=filters,
        fields=[
            "name as customerCode",
            "customer_name as customerName",
            "disabled",
            "account_type as accountType",
            "kam_code as kamCode",
            "kam_name as kamName",
            "email_id as billingEmail",
            "mobile_no as phone",
            "customer_group as customerGroup",
            "territory",
            "creation as createdAt",
            "service_activation_date as serviceActivationDate",
            "modified",
        ],
        limit=page_size,
        limit_start=offset,
        order_by="creation asc",
    )
    for r in rows:
        r["status"] = "Inactive" if r.pop("disabled", False) else "Active"

    return _wrap(rows, frappe.db.count("Customer", filters), page, page_size)


# ---------------------------------------------------------------------------
# Contacts
# ---------------------------------------------------------------------------

@frappe.whitelist()
def get_contacts(page=1, page_size=200, modified_after=None):
    """Paginated contacts linked to any Customer."""
    _guard()
    page, page_size, offset = _page_args(page, page_size)

    linked = frappe.get_all(
        "Dynamic Link",
        filters={"link_doctype": "Customer", "parenttype": "Contact"},
        fields=["parent as contact_name", "link_name as customerCode"],
    )
    if not linked:
        return _wrap([], 0, page, page_size)

    contact_map = {r.contact_name: r.customerCode for r in linked}
    filters = {"name": ("in", list(contact_map))}
    if modified_after:
        filters["modified"] = (">=", modified_after)

    rows = frappe.get_all(
        "Contact",
        filters=filters,
        fields=["name", "first_name", "last_name", "full_name",
                "email_id", "mobile_no", "phone", "modified"],
        limit=page_size,
        limit_start=offset,
        order_by="creation asc",
    )
    for r in rows:
        r["customerCode"] = contact_map.get(r["name"])

    return _wrap(rows, frappe.db.count("Contact", filters), page, page_size)


# ---------------------------------------------------------------------------
# Sales Invoices
# ---------------------------------------------------------------------------

@frappe.whitelist()
def get_invoices(page=1, page_size=200, from_date=None, to_date=None,
                 status=None, modified_after=None):
    """Paginated submitted Sales Invoices (includes line items)."""
    _guard()
    page, page_size, offset = _page_args(page, page_size)

    filters = {"docstatus": 1}
    if from_date and to_date:
        filters["posting_date"] = ("between", [from_date, to_date])
    elif from_date:
        filters["posting_date"] = (">=", from_date)
    elif to_date:
        filters["posting_date"] = ("<=", to_date)
    if status:
        filters["status"] = status
    if modified_after:
        filters["modified"] = (">=", modified_after)

    rows = frappe.get_all(
        "Sales Invoice",
        filters=filters,
        fields=[
            "name as invoiceNo", "customer as customerCode",
            "posting_date as invoiceDate", "due_date as dueDate",
            "status", "grand_total as totalAmount",
            "outstanding_amount as outstandingAmount",
            "currency", "company", "is_return as isCreditNote",
            "return_against as returnAgainstInvoice",
        ],
        limit=page_size,
        limit_start=offset,
        order_by="posting_date asc, name asc",
    )

    for r in rows:
        r["paidAmount"] = flt(r["totalAmount"]) - flt(r["outstandingAmount"])
        r["isCreditNote"] = bool(r.get("isCreditNote"))
        if not r["isCreditNote"]:
            r["returnAgainstInvoice"] = None

        so_rows = frappe.get_all(
            "Sales Invoice Item",
            filters={"parent": r["invoiceNo"], "sales_order": ("!=", "")},
            pluck="sales_order",
            distinct=True,
            limit=1,
        )
        r["salesOrderNo"] = so_rows[0] if so_rows else None

        r["lineItems"] = frappe.get_all(
            "Sales Invoice Item",
            filters={"parent": r["invoiceNo"]},
            fields=["item_code as itemCode", "item_name as itemName",
                    "item_group as itemGroup", "qty", "rate", "amount", "uom"],
        )

    return _wrap(rows, frappe.db.count("Sales Invoice", filters), page, page_size)


# ---------------------------------------------------------------------------
# Payments
# ---------------------------------------------------------------------------

@frappe.whitelist()
def get_payments(page=1, page_size=200, from_date=None, to_date=None,
                 modified_after=None):
    """Paginated submitted Payment Entries for Customer party type."""
    _guard()
    page, page_size, offset = _page_args(page, page_size)

    filters = {"docstatus": 1, "party_type": "Customer"}
    if from_date and to_date:
        filters["posting_date"] = ("between", [from_date, to_date])
    elif from_date:
        filters["posting_date"] = (">=", from_date)
    elif to_date:
        filters["posting_date"] = ("<=", to_date)
    if modified_after:
        filters["modified"] = (">=", modified_after)

    rows = frappe.get_all(
        "Payment Entry",
        filters=filters,
        fields=[
            "name as paymentId", "posting_date as paymentDate",
            "party as customerCode", "paid_amount as amount",
            "paid_from_account_currency as currency",
            "status", "reference_no as referenceNo", "company",
        ],
        limit=page_size,
        limit_start=offset,
        order_by="posting_date asc, name asc",
    )

    for r in rows:
        r["currency"] = r.get("currency") or "SAR"
        r["allocatedInvoices"] = frappe.get_all(
            "Payment Entry Reference",
            filters={"parent": r["paymentId"], "reference_doctype": "Sales Invoice"},
            fields=["reference_name as invoiceNo",
                    "allocated_amount as allocatedAmount",
                    "outstanding_amount as outstandingAmount"],
        )

    return _wrap(rows, frappe.db.count("Payment Entry", filters), page, page_size)


# ---------------------------------------------------------------------------
# Sales Orders
# ---------------------------------------------------------------------------

@frappe.whitelist()
def get_sales_orders(page=1, page_size=200, from_date=None, to_date=None,
                     status=None, modified_after=None):
    """Paginated Sales Orders (draft + submitted)."""
    _guard()
    page, page_size, offset = _page_args(page, page_size)

    filters = {"docstatus": ("in", [0, 1])}
    if from_date and to_date:
        filters["transaction_date"] = ("between", [from_date, to_date])
    elif from_date:
        filters["transaction_date"] = (">=", from_date)
    elif to_date:
        filters["transaction_date"] = ("<=", to_date)
    if status:
        filters["status"] = status
    if modified_after:
        filters["modified"] = (">=", modified_after)

    rows = frappe.get_all(
        "Sales Order",
        filters=filters,
        fields=[
            "name as salesOrderNo", "customer as customerCode",
            "status", "is_addon as isAddon",
            "transaction_date as orderDate", "grand_total as totalAmount",
            "currency", "company", "owner as createdBy",
        ],
        limit=page_size,
        limit_start=offset,
        order_by="transaction_date asc, name asc",
    )

    for r in rows:
        r["isAddon"] = bool(r.get("isAddon"))
        r["items"] = frappe.get_all(
            "Sales Order Item",
            filters={"parent": r["salesOrderNo"]},
            fields=["item_code as itemCode", "item_name as itemName",
                    "qty", "rate", "amount", "uom"],
        )

    return _wrap(rows, frappe.db.count("Sales Order", filters), page, page_size)


# ---------------------------------------------------------------------------
# Items / Products
# ---------------------------------------------------------------------------

@frappe.whitelist()
def get_items(page=1, page_size=200, modified_after=None):
    """Paginated Item list for product segmentation."""
    _guard()
    page, page_size, offset = _page_args(page, page_size)

    filters = {}
    if modified_after:
        filters["modified"] = (">=", modified_after)

    rows = frappe.get_all(
        "Item",
        filters=filters,
        fields=["name as itemCode", "item_name as itemName",
                "item_group as itemGroup", "stock_uom as uom", "disabled"],
        limit=page_size,
        limit_start=offset,
        order_by="creation asc",
    )
    for r in rows:
        r["disabled"] = bool(r.get("disabled"))

    return _wrap(rows, frappe.db.count("Item", filters), page, page_size)


# ---------------------------------------------------------------------------
# Aging Snapshot  (also used by the daily push task)
# ---------------------------------------------------------------------------

def _compute_aging(as_of_date=None, company=None, customer=None):
    """Compute aging buckets from outstanding submitted Sales Invoices.

    Returns a list of per-customer dicts matching the document spec buckets:
    0-30, 31-60, 61-90, 91-120, 121-200, 201-300, 301-400, 401-500, 501+.
    """
    as_of = as_of_date or today()

    filters = {"docstatus": 1, "outstanding_amount": (">", 0)}
    if company:
        filters["company"] = company
    if customer:
        filters["customer"] = customer

    invoices = frappe.get_all(
        "Sales Invoice",
        filters=filters,
        fields=["name", "customer", "due_date", "outstanding_amount"],
    )

    if not invoices:
        return []

    # Fetch account_type per customer in bulk
    cust_codes = list({i.customer for i in invoices})
    cust_meta = {}
    for i in range(0, len(cust_codes), 200):
        chunk = cust_codes[i:i + 200]
        for row in frappe.get_all(
            "Customer",
            filters={"name": ("in", chunk)},
            fields=["name", "account_type"],
        ):
            cust_meta[row.name] = row.account_type or None

    buckets = {}
    for inv in invoices:
        cust = inv.customer
        if cust not in buckets:
            buckets[cust] = {
                "customerCode": cust,
                "accountType": cust_meta.get(cust),
                "snapshotDate": as_of,
                "totalOutstanding": 0.0,
                "aging0_30": 0.0,
                "aging31_60": 0.0,
                "aging61_90": 0.0,
                "aging91_120": 0.0,
                "aging121_200": 0.0,
                "aging201_300": 0.0,
                "aging301_400": 0.0,
                "aging401_500": 0.0,
                "aging501Above": 0.0,
            }

        due = inv.due_date or as_of
        days = max(0, date_diff(as_of, due))
        amt = flt(inv.outstanding_amount)
        b = buckets[cust]
        b["totalOutstanding"] += amt

        if days <= 30:
            b["aging0_30"] += amt
        elif days <= 60:
            b["aging31_60"] += amt
        elif days <= 90:
            b["aging61_90"] += amt
        elif days <= 120:
            b["aging91_120"] += amt
        elif days <= 200:
            b["aging121_200"] += amt
        elif days <= 300:
            b["aging201_300"] += amt
        elif days <= 400:
            b["aging301_400"] += amt
        elif days <= 500:
            b["aging401_500"] += amt
        else:
            b["aging501Above"] += amt

    return list(buckets.values())


@frappe.whitelist()
def get_aging_snapshot(as_of_date=None, company=None, customer=None):
    """Return full aging snapshot for all customers (or one customer)."""
    _guard()
    data = _compute_aging(as_of_date=as_of_date, company=company, customer=customer)
    return {
        "snapshotDate": as_of_date or today(),
        "company": company,
        "totalCustomers": len(data),
        "data": data,
    }


# ---------------------------------------------------------------------------
# Reconciliation
# ---------------------------------------------------------------------------

@frappe.whitelist()
def get_reconciliation_summary(from_date, to_date, company=None):
    """Invoice and payment totals for a date range — used for reconciliation."""
    _guard()

    company_sql = "AND company = %(company)s" if company else ""
    params = {"fd": from_date, "td": to_date, "company": company or ""}

    inv = frappe.db.sql(
        f"""
        SELECT COUNT(*) AS cnt,
               COALESCE(SUM(grand_total), 0)        AS total,
               COALESCE(SUM(outstanding_amount), 0) AS outstanding
        FROM `tabSales Invoice`
        WHERE docstatus = 1
          AND posting_date BETWEEN %(fd)s AND %(td)s
          {company_sql}
        """,
        params,
        as_dict=True,
    )[0]

    pay = frappe.db.sql(
        f"""
        SELECT COUNT(*) AS cnt,
               COALESCE(SUM(paid_amount), 0) AS total
        FROM `tabPayment Entry`
        WHERE docstatus = 1
          AND party_type = 'Customer'
          AND posting_date BETWEEN %(fd)s AND %(td)s
          {company_sql}
        """,
        params,
        as_dict=True,
    )[0]

    return {
        "fromDate": from_date,
        "toDate": to_date,
        "company": company,
        "invoices": {
            "count": int(inv.cnt or 0),
            "total": flt(inv.total),
            "outstanding": flt(inv.outstanding),
        },
        "payments": {
            "count": int(pay.cnt or 0),
            "total": flt(pay.total),
        },
    }


# ---------------------------------------------------------------------------
# Invoice PDF URL
# ---------------------------------------------------------------------------

@frappe.whitelist()
def get_invoice_pdf_url(invoice, print_format=None):
    """Return a signed URL to download the PDF for a given Sales Invoice."""
    _guard()

    if not frappe.db.exists("Sales Invoice", invoice):
        frappe.throw(f"Invoice '{invoice}' not found.", frappe.DoesNotExistError)

    pf = print_format or frappe.db.get_single_value(
        "Collection App Settings", "invoice_print_format"
    ) or ""
    pf_param = f"&format={pf}" if pf else ""
    pdf_url = (
        f"{get_url()}/api/method/frappe.utils.pdf.get_pdf"
        f"?doctype=Sales+Invoice&name={invoice}{pf_param}"
    )

    return {
        "invoiceNo": invoice,
        "pdfUrl": pdf_url,
        "generatedAt": now_datetime().isoformat() + "Z",
    }
