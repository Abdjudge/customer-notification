# Copyright (c) 2026, a and contributors
# For license information, please see license.txt

import frappe
from frappe.model.document import Document


class ERPWebhookLog(Document):
	@frappe.whitelist()
	def retry(self):
		"""Manual retry button: re-queue delivery for this log entry."""
		from customer_notification.customer_notification.webhook import _retry_delivery

		if self.status == "Delivered":
			frappe.throw("This webhook was already delivered successfully.")
		if self.attempts >= 10:
			frappe.throw("Maximum retry limit (10) reached for this webhook.")

		frappe.enqueue(
			"customer_notification.customer_notification.webhook._retry_delivery",
			queue="default",
			log_name=self.name,
			timeout=60,
		)
		frappe.msgprint(f"Retry queued for {self.name}.", indicator="green", alert=True)
