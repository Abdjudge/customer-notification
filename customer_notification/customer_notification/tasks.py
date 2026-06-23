# Copyright (c) 2026, a and contributors
# For license information, please see license.txt
"""Scheduled dispatch of customer notifications based on each type's repetition + hour."""

import frappe
from frappe.utils import cint, get_datetime, now_datetime, today


def _is_due(nt, now):
	"""Return True if `nt` should fire in the current hour."""
	if cint(nt.hour_of_send) != now.hour:
		return False

	if nt.repetition == "Daily":
		return True
	if nt.repetition == "Weekly":
		# %A -> full weekday name (Monday..Sunday) matching the field options
		return now.strftime("%A") == (nt.day_of_week or "Monday")
	if nt.repetition == "Monthly":
		return now.day == cint(nt.day_of_month or 1)
	return False


def _already_sent_today(notification_type):
	"""Guard against duplicate scheduler runs within the same day."""
	return bool(
		frappe.db.exists(
			"Customer Notification Log",
			{
				"notification_type": notification_type,
				"triggered_by": "Scheduler",
				"sent_on": (">=", today() + " 00:00:00"),
			},
		)
	)


def send_scheduled_notifications():
	"""Hourly job: dispatch every enabled Notification Type whose schedule matches now."""
	from customer_notification.customer_notification.notification import run_for_type

	now = now_datetime()
	types = frappe.get_all("Notification Type", filters={"enabled": 1}, pluck="name")

	for name in types:
		try:
			nt = frappe.get_doc("Notification Type", name)
			if not _is_due(nt, now):
				continue
			if _already_sent_today(name):
				continue
			run_for_type(name, triggered_by="Scheduler")
		except Exception:
			frappe.log_error(
				title="Customer Notification: scheduler failed",
				message=f"{name}\n{frappe.get_traceback()}",
			)
