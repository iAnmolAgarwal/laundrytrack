import datetime as dt

from django.core.management.base import BaseCommand, CommandError
from django.utils import timezone

from laundry.services.reminders import send_due_reminders


class Command(BaseCommand):
    help = "Email a reminder for every slip whose reminder date is the given day (default: today)."

    def add_arguments(self, parser):
        parser.add_argument("--date", help="YYYY-MM-DD (default: today, local time)")
        parser.add_argument("--force", action="store_true", help="resend even if a reminder was already sent")

    def handle(self, *args, **opts):
        if opts.get("date"):
            try:
                day = dt.date.fromisoformat(opts["date"])
            except ValueError:
                raise CommandError("--date must be YYYY-MM-DD")
        else:
            day = timezone.localdate()
        results = send_due_reminders(day, force=bool(opts.get("force")))
        for result in results:
            style = self.style.SUCCESS if result["ok"] else self.style.ERROR
            self.stdout.write(style(f"{'OK  ' if result['ok'] else 'FAIL'} slip #{result['slip']}: {result['message']}"))
        sent = sum(1 for r in results if r["ok"])
        self.stdout.write(f"{day.isoformat()}: {sent} reminder(s) sent, {len(results) - sent} failed.")
