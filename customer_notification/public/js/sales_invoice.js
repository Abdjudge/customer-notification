// Copyright (c) 2026, a and contributors
// Adds a "Send to Customer" button to the Sales Invoice form.

frappe.ui.form.on("Sales Invoice", {
	refresh(frm) {
		if (frm.is_new() || frm.doc.docstatus !== 1) return;

		frm.add_custom_button(
			__("Send to Customer"),
			() => {
				frappe.call({
					method: "customer_notification.customer_notification.sales_invoice.get_invoice_email_defaults",
					args: { customer: frm.doc.customer, invoice: frm.doc.name },
					callback(r) {
						open_send_invoice_dialog(frm, r.message || {});
					},
				});
			},
			__("Notify")
		);
	},
});

function open_send_invoice_dialog(frm, defaults) {
	const cc = (defaults.cc_users || []).slice();
	const d = new frappe.ui.Dialog({
		title: __("Send Invoice {0}", [frm.doc.name]),
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
			{
				fieldname: "cc",
				label: __("CC Users"),
				fieldtype: "MultiSelectPills",
				default: cc,
				description: __("Pre-filled from the customer's CC Users. Search to add any other user."),
				get_data(txt) {
					// Link-style search against the User doctype so any user can be added.
					return frappe.db.get_link_options("User", txt);
				},
			},
		],
		primary_action_label: __("Send"),
		primary_action(values) {
			frappe.call({
				method: "customer_notification.customer_notification.sales_invoice.send_invoice_now",
				args: {
					invoice: frm.doc.name,
					email_template: values.email_template,
					print_format: values.print_format,
					language: values.language,
					cc: JSON.stringify(values.cc || []),
				},
				freeze: true,
				freeze_message: __("Sending invoice..."),
				callback() {
					d.hide();
				},
			});
		},
	});
	d.show();
}
