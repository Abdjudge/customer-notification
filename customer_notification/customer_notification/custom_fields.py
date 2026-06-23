# Copyright (c) 2026, a and contributors
# For license information, please see license.txt
"""Custom fields this app adds to standard (ERPNext) doctypes.

Created idempotently on install and on every `bench migrate` via the
`after_migrate` hook, so the fields survive across deployments.
"""

import frappe
from frappe.custom.doctype.custom_field.custom_field import create_custom_fields

CUSTOM_FIELDS = {
	"Customer": [
		{
			"fieldname": "cn_cc_section",
			"label": "Notification CC Recipients",
			"fieldtype": "Section Break",
			"insert_after": "represents_company",
			"collapsible": 1,
		},
		{
			"fieldname": "cc_users",
			"label": "CC Users",
			"fieldtype": "Table MultiSelect",
			"options": "Notification Type CC User",
			"insert_after": "cn_cc_section",
			"description": (
				"These users are CC'd whenever this customer is emailed an invoice "
				"from the Send to Customer dialog."
			),
		},
	],
}


def create():
	"""Create / update the app's custom fields. Safe to run repeatedly."""
	create_custom_fields(CUSTOM_FIELDS, ignore_validate=True)


def after_migrate():
	create()
