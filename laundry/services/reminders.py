"""Send the collection reminders that are due on a given day."""
from django.utils import timezone

from ..models import LaundrySlip
from .mailer import send_pickup_reminder


def send_due_reminders(day=None, force=False):
    """Email every student whose chosen reminder date is `day`. Returns a list of result dicts."""
    day = day or timezone.localdate()
    slips = LaundrySlip.objects.filter(status=LaundrySlip.Status.AT_LAUNDRY, pickup_date=day).select_related("user")
    if not force:
        slips = slips.filter(reminder_sent=False)
    results = []
    for slip in slips:
        ok, message = send_pickup_reminder(slip)
        results.append({"slip": slip.pk, "laundry_no": slip.slip_no, "ok": ok, "message": message})
    return results
