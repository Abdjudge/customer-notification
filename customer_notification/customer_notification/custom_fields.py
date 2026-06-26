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
		# --- Collection App fields ---
		{
			"fieldname": "cn_collection_section",
			"label": "Collection App",
			"fieldtype": "Section Break",
			"insert_after": "cc_users",
			"collapsible": 1,
		},
		{
			"fieldname": "account_type",
			"label": "Account Type",
			"fieldtype": "Select",
			"options": "\nGlobal\nAfaqy\nOther",
			"insert_after": "cn_collection_section",
			"description": "Customer classification used by the Collection App (Global, Afaqy, Other).",
		},
		{
			"fieldname": "kam_code",
			"label": "KAM Code",
			"fieldtype": "Data",
			"insert_after": "account_type",
			"description": "Key Account Manager employee code.",
		},
		{
			"fieldname": "kam_name",
			"label": "KAM Name",
			"fieldtype": "Data",
			"insert_after": "kam_code",
			"description": "Key Account Manager display name.",
		},
		{
			"fieldname": "service_activation_date",
			"label": "Service Activation Date",
			"fieldtype": "Date",
			"insert_after": "kam_name",
			"description": "Date the customer's service went live — used to trigger the Welcome Call.",
		},
	],
	"Sales Order": [
		{
			"fieldname": "is_addon",
			"label": "Is Add-On",
			"fieldtype": "Check",
			"insert_after": "order_type",
			"description": (
				"Tick if this Sales Order represents an add-on service for an existing customer. "
				"The Collection App uses this to trigger the Existing Customer Add-on Confirmation workflow."
			),
		},
	],
}


def create():
	"""Create / update the app's custom fields. Safe to run repeatedly."""
	create_custom_fields(CUSTOM_FIELDS, ignore_validate=True)


def after_migrate():
	create()
