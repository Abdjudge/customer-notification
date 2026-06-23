# Copyright (c) 2026, a and contributors
# For license information, please see license.txt

import frappe
from frappe.model.document import Document


@frappe.whitelist()
def get_customers_by_filters(customer_group=None, territory=None, include_child_groups=1, disabled=0):
	"""Return customers matching the given filters, for bulk-adding to a Notification Type.

	When ``customer_group`` is a parent (group) node, all customers under its
	descendant groups are included unless ``include_child_groups`` is falsy.
	"""
	include_child_groups = int(include_child_groups or 0)
	filters = {"disabled": int(disabled or 0)}

	if customer_group:
		if include_child_groups:
			groups = frappe.db.get_descendants("Customer Group", customer_group) or []
			groups.append(customer_group)
			filters["customer_group"] = ["in", list(set(groups))]
		else:
			filters["customer_group"] = customer_group

	if territory:
		filters["territory"] = territory

	return frappe.get_all(
		"Customer",
		filters=filters,
		fields=["name as customer", "customer_name"],
		order_by="customer_name asc",
	)


class NotificationType(Document):
	def validate(self):
		if self.repetition == "Monthly":
			if not self.day_of_month or not (1 <= int(self.day_of_month) <= 28):
				frappe.throw("Day of Month must be between 1 and 28.")
		if not self.send_email and not self.send_whatsapp:
			frappe.throw("Enable at least one channel: Send Email or Send WhatsApp.")

	@frappe.whitelist()
	def send_to_all_customers(self):
		"""Push the notification to every customer linked to this type. Runs in background."""
		from customer_notification.customer_notification.notification import enqueue_send_for_type

		return enqueue_send_for_type(self.name)
