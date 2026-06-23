// Copyright (c) 2026, a and contributors
// For license information, please see license.txt

frappe.ui.form.on("Customer Notification Settings", {
	refresh(frm) {
		// Restrict both Print Format pickers to Sales Invoice print formats only.
		const sales_invoice_only = () => ({ filters: { doc_type: "Sales Invoice" } });
		frm.set_query("sales_invoice_print_format", sales_invoice_only);
		frm.set_query("return_invoice_print_format", sales_invoice_only);
	},
});
