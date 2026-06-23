// Copyright (c) 2026, a and contributors
// Adds a "Send to Customer" bulk action to the Sales Invoice list view,
// extending (not replacing) ERPNext's existing list settings.

frappe.listview_settings["Sales Invoice"] = frappe.listview_settings["Sales Invoice"] || {};

(function () {
	const settings = frappe.listview_settings["Sales Invoice"];
	const original_onload = settings.onload;

	settings.onload = function (listview) {
		if (original_onload) {
			original_onload(listview);
		}

		listview.page.add_actions_menu_item(
			__("Send to Customer (Email)"),
			() => {
				const items = listview.get_checked_items(true); // names only
				if (!items.length) {
					frappe.msgprint(__("Select one or more invoices first."));
					return;
				}
				frappe.call({
					method: "customer_notification.customer_notification.sales_invoice.get_invoice_email_defaults",
					callback(r) {
						open_bulk_dialog(items, r.message || {});
					},
				});
			},
			false
		);
	};

	function open_bulk_dialog(invoices, defaults) {
		const d = new frappe.ui.Dialog({
			title: __("Send {0} Invoice(s) to Customers", [invoices.length]),
			fields: [
				{
					fieldname: "email_template",
					label: __("Email Template"),
					fieldtype: "Link",
					options: "Email Template",
					default: defaults.email_template,
					reqd: 1,
				},
				{
					fieldname: "print_format",
					label: __("Print Format"),
					fieldtype: "Link",
					options: "Print Format",
					default: defaults.print_format,
					get_query() {
						return { filters: { doc_type: "Sales Invoice" } };
					},
				},
				{
					fieldname: "language",
					label: __("Language"),
					fieldtype: "Select",
					options: ["English", "Arabic"].join("\n"),
					default: defaults.language || "English",
				},
			],
			primary_action_label: __("Send"),
			primary_action(values) {
				frappe.call({
					method: "customer_notification.customer_notification.sales_invoice.send_invoices",
					args: {
						invoices: JSON.stringify(invoices),
						email_template: values.email_template,
						print_format: values.print_format,
						language: values.language,
					},
					freeze: true,
					freeze_message: __("Queuing invoices..."),
					callback(r) {
						if (!r.exc) {
							d.hide();
							frappe.msgprint({
								title: __("Queued"),
								indicator: "green",
								message: (r.message && r.message.message) || __("Invoices queued."),
							});
						}
					},
				});
			},
		});
		d.show();
	}
})();
