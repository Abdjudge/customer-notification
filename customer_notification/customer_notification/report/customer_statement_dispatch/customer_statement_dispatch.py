# Copyright (c) 2026, a and contributors
# For license information, please see license.txt
"""Customer Statement Dispatch.

Reuses ERPNext's Accounts Receivable Summary logic so the columns, outstanding
balances and ageing match that report exactly. Rows are selectable; the
"Send Statement of Account" action (see the .js) dispatches a per-customer
Statement of Account / Customer Account Reconciliation PDF by email.
"""

import frappe
from erpnext.accounts.report.accounts_receivable_summary.accounts_receivable_summary import (
	execute as ar_summary_execute,
)


def execute(filters=None):
	filters = frappe._dict(filters or {})
	# Accounts Receivable Summary needs these; supply sensible defaults.
	filters.setdefault("party_type", "Customer")
	if not filters.get("range"):
		filters["range"] = "30, 60, 90, 120"
	if not filters.get("ageing_based_on"):
		filters["ageing_based_on"] = "Due Date"
	if not filters.get("report_date"):
		filters["report_date"] = filters.get("to_date") or frappe.utils.today()

	return ar_summary_execute(filters)
