// Copyright (c) 2026, a and contributors
// For license information, please see license.txt
/* eslint-disable */

frappe.query_reports["Customer Statement Dispatch"] = {
	filters: [
		{
			fieldname: "company",
			label: __("Company"),
			fieldtype: "Link",
			options: "Company",
			default: frappe.defaults.get_user_default("Company"),
			reqd: 1,
		},
		{
			fieldname: "report_date",
			label: __("As On Date"),
			fieldtype: "Date",
			default: frappe.datetime.get_today(),
			reqd: 1,
		},
		{
			fieldname: "from_date",
			label: __("Statement From Date"),
			fieldtype: "Date",
			default: frappe.datetime.year_start(),
		},
		{
			fieldname: "to_date",
			label: __("Statement To Date"),
			fieldtype: "Date",
			default: frappe.datetime.get_today(),
		},
		{
			fieldname: "ageing_based_on",
			label: __("Ageing Based On"),
			fieldtype: "Select",
			options: "Due Date\nPosting Date",
			default: "Due Date",
		},
		{
			fieldname: "party_type",
			label: __("Party Type"),
			fieldtype: "Link",
			options: "Party Type",
			default: "Customer",
			hidden: 1,
		},
		{
			fieldname: "party",
			label: __("Customer"),
			fieldtype: "MultiSelectList",
			get_data: function (txt) {
				return frappe.db.get_link_options("Customer", txt);
			},
		},
		{
			fieldname: "customer_group",
			label: __("Customer Group"),
			fieldtype: "Link",
			options: "Customer Group",
		},
	],

	// Enable the row-selection checkbox column.
	get_datatable_options(options) {
		return Object.assign(options, { checkboxColumn: true });
	},

	onload(report) {
		report.page.add_inner_button(__("Send Statement of Account"), () => {
			open_send_dialog(report);
		});
	},
};

function open_send_dialog(report) {
	const checked = frappe.query_report.get_checked_items() || [];
	const customers = [...new Set(checked.map((r) => r.party).filter(Boolean))];

	if (!customers.length) {
		frappe.msgprint({
			title: __("No rows selected"),
			indicator: "orange",
			message: __("Tick the checkbox on one or more customer rows first."),
		});
		return;
	}

	const f = report.get_values();

	const d = new frappe.ui.Dialog({
		title: __("Send Statement to {0} Customer(s)", [customers.length]),
		fields: [
			{
				fieldname: "report_type",
				label: __("Statement Type"),
				fieldtype: "Select",
				options: ["Statement of Account", "Customer Account Reconciliation"].join("\n"),
				default: "Statement of Account",
				reqd: 1,
			},
			{
				fieldname: "language",
				label: __("Language"),
				fieldtype: "Select",
				options: ["English", "Arabic"].join("\n"),
				default: "English",
				reqd: 1,
			},
			{ fieldtype: "Column Break" },
			{
				fieldname: "email_template",
				label: __("Email Template"),
				fieldtype: "Link",
				options: "Email Template",
				reqd: 1,
			},
			{
				fieldname: "include_ageing",
				label: __("Include Ageing Summary"),
				fieldtype: "Check",
				default: 1,
			},
			{ fieldtype: "Section Break", label: __("Statement Period") },
			{
				fieldname: "company",
				label: __("Company"),
				fieldtype: "Link",
				options: "Company",
				default: f.company,
				read_only: 1,
			},
			{
				fieldname: "from_date",
				label: __("From Date"),
				fieldtype: "Date",
				default: f.from_date || frappe.datetime.year_start(),
				reqd: 1,
			},
			{
				fieldname: "to_date",
				label: __("To Date"),
				fieldtype: "Date",
				default: f.to_date || f.report_date || frappe.datetime.get_today(),
				reqd: 1,
			},
			{ fieldtype: "Section Break", label: __("CC (optional)") },
			{
				fieldname: "cc_emails",
				label: __("CC Email Addresses"),
				fieldtype: "Small Text",
				description: __("Comma or newline separated."),
			},
			{
				fieldname: "cc_roles",
				label: __("CC Roles"),
				fieldtype: "MultiSelectList",
				description: __("CC every enabled user holding any selected role."),
				get_data: function (txt) {
					return frappe.db.get_link_options("Role", txt);
				},
			},
		],
		primary_action_label: __("Send"),
		primary_action(values) {
			frappe.call({
				method: "customer_notification.customer_notification.statement.send_statements",
				args: {
					customers: JSON.stringify(customers),
					company: values.company,
					from_date: values.from_date,
					to_date: values.to_date,
					report_type: values.report_type,
					email_template: values.email_template,
					language: values.language,
					include_ageing: values.include_ageing,
					cc_emails: values.cc_emails,
					cc_roles: JSON.stringify(values.cc_roles || []),
				},
				freeze: true,
				freeze_message: __("Queuing statements..."),
				callback(r) {
					if (!r.exc) {
						d.hide();
						frappe.msgprint({
							title: __("Queued"),
							indicator: "green",
							message: (r.message && r.message.message) || __("Statements queued."),
						});
					}
				},
			});
		},
	});
	d.show();
}
