// Copyright (c) 2026, a and contributors
// Adds a "Send Overdue Invoice Notification" button to the Customer form.

frappe.ui.form.on("Customer", {
	refresh(frm) {
		if (frm.is_new()) return;

		frm.add_custom_button(
			__("Send Overdue Invoice Notification"),
			() => {
				frappe.call({
					method: "customer_notification.customer_notification.notification.get_customer_notification_types",
					args: { customer: frm.doc.name },
					callback(r) {
						const types = r.message || [];
						if (!types.length) {
							frappe.msgprint({
								title: __("No Notification Type"),
								indicator: "orange",
								message: __(
									"This customer is not linked to any enabled Notification Type."
								),
							});
							return;
						}
						_prompt_and_send(frm, types);
					},
				});
			},
			__("Notify")
		);
	},
});

function _prompt_and_send(frm, types) {
	const send = (notification_type) => {
		frappe.call({
			method: "customer_notification.customer_notification.notification.push_for_customer",
			args: { customer: frm.doc.name, notification_type: notification_type || null },
			freeze: true,
			freeze_message: __("Queuing notification..."),
			callback(r) {
				if (!r.exc) {
					frappe.show_alert({
						message: (r.message && r.message.message) || __("Queued."),
						indicator: "green",
					});
				}
			},
		});
	};

	if (types.length === 1) {
		send(types[0].name);
		return;
	}

	// Multiple types: let the user pick one or send to all.
	const d = new frappe.ui.Dialog({
		title: __("Choose Notification Type"),
		fields: [
			{
				fieldname: "notification_type",
				fieldtype: "Select",
				label: __("Notification Type"),
				options: [__("All Types")].concat(types.map((t) => t.title || t.name)),
				default: __("All Types"),
			},
		],
		primary_action_label: __("Send"),
		primary_action(values) {
			d.hide();
			if (values.notification_type === __("All Types")) {
				send(null);
			} else {
				const match = types.find(
					(t) => (t.title || t.name) === values.notification_type
				);
				send(match ? match.name : null);
			}
		},
	});
	d.show();
}
