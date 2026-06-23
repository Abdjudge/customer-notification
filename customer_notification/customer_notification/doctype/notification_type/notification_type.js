// Copyright (c) 2026, a and contributors
// For license information, please see license.txt

frappe.ui.form.on("Notification Type", {
	refresh(frm) {
		frm.add_custom_button(__("Bulk Add Customers"), () => {
			open_bulk_add_dialog(frm);
		});

		if (frm.is_new()) return;

		frm.add_custom_button(__("Send to All Customers"), () => {
			frappe.confirm(
				__("Send overdue-invoice notifications to all {0} customer(s) of this type?", [
					(frm.doc.customers || []).length,
				]),
				() => {
					frm.call("send_to_all_customers").then((r) => {
						if (!r.exc) {
							frappe.msgprint({
								title: __("Queued"),
								indicator: "green",
								message: r.message && r.message.message
									? r.message.message
									: __("Notifications have been queued for sending."),
							});
						}
					});
				}
			);
		}).addClass("btn-primary");
	},
});

function open_bulk_add_dialog(frm) {
	const dialog = new frappe.ui.Dialog({
		title: __("Bulk Add Customers"),
		fields: [
			{
				fieldtype: "Link",
				fieldname: "customer_group",
				label: __("Customer Group"),
				options: "Customer Group",
				description: __("Pick a group to add all of its customers."),
			},
			{
				fieldtype: "Check",
				fieldname: "include_child_groups",
				label: __("Include Child Groups"),
				default: 1,
				depends_on: "customer_group",
			},
			{
				fieldtype: "Column Break",
			},
			{
				fieldtype: "Link",
				fieldname: "territory",
				label: __("Territory"),
				options: "Territory",
			},
			{
				fieldtype: "Check",
				fieldname: "include_disabled",
				label: __("Include Disabled Customers"),
				default: 0,
			},
			{
				fieldtype: "Section Break",
			},
			{
				fieldtype: "HTML",
				fieldname: "preview",
			},
		],
		primary_action_label: __("Add Customers"),
		primary_action(values) {
			fetch_customers(values).then((customers) => {
				const added = add_customers_to_table(frm, customers);
				dialog.hide();
				frappe.show_alert({
					message: __("{0} customer(s) added, {1} already present.", [
						added,
						customers.length - added,
					]),
					indicator: added ? "green" : "orange",
				});
			});
		},
	});

	// "Preview" secondary action: show how many would match before adding.
	dialog.set_secondary_action_label(__("Preview Count"));
	dialog.set_secondary_action(() => {
		fetch_customers(dialog.get_values()).then((customers) => {
			dialog.fields_dict.preview.$wrapper.html(
				`<div class="text-muted">${__("{0} customer(s) match these filters.", [
					customers.length,
				])}</div>`
			);
		});
	});

	dialog.show();
}

function fetch_customers(values) {
	if (!values.customer_group && !values.territory) {
		frappe.throw(__("Select a Customer Group or Territory first."));
	}
	return frappe
		.call({
			method: "customer_notification.customer_notification.doctype.notification_type.notification_type.get_customers_by_filters",
			args: {
				customer_group: values.customer_group,
				territory: values.territory,
				include_child_groups: values.include_child_groups ? 1 : 0,
				disabled: values.include_disabled ? 1 : 0,
			},
		})
		.then((r) => r.message || []);
}

function add_customers_to_table(frm, customers) {
	const existing = new Set((frm.doc.customers || []).map((row) => row.customer));
	let added = 0;
	customers.forEach((c) => {
		if (existing.has(c.customer)) return;
		const row = frm.add_child("customers", {
			customer: c.customer,
			customer_name: c.customer_name,
		});
		existing.add(c.customer);
		added += 1;
	});
	if (added) {
		frm.refresh_field("customers");
		frm.dirty();
	}
	return added;
}
