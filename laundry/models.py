from django.conf import settings
from django.db import models
from django.utils import timezone


def student_email_for(enrollment_no):
    """Institute mailbox for a roll number, e.g. 2401030294@mail.jiit.ac.in."""
    roll = "".join(str(enrollment_no or "").split())
    return f"{roll}@{settings.STUDENT_EMAIL_DOMAIN}".lower() if roll else ""


class Profile(models.Model):
    user = models.OneToOneField(settings.AUTH_USER_MODEL, on_delete=models.CASCADE, related_name="profile")
    hostel = models.CharField(max_length=80, blank=True)
    room_no = models.CharField(max_length=20, blank=True)
    enrollment_no = models.CharField("enrollment / roll number", max_length=20, blank=True)
    laundry_days = models.CharField(max_length=20, default="1,4", help_text="Weekday numbers, Monday=0, comma separated")

    @property
    def laundry_day_list(self):
        from .services.schedule import parse_weekdays
        return parse_weekdays(self.laundry_days)

    def __str__(self):
        return f"{self.user.username} ({self.hostel} {self.room_no})".strip()


class LaundrySlip(models.Model):
    class Status(models.TextChoices):
        DRAFT = "draft", "Draft"
        AT_LAUNDRY = "at_laundry", "At laundry"
        COLLECTED = "collected", "Collected"
        MISMATCH = "mismatch", "Mismatch"

    user = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.CASCADE, related_name="slips")
    slip_no = models.CharField("slip number", max_length=40, blank=True)
    student_name = models.CharField(max_length=120, blank=True)
    room_no = models.CharField("room number", max_length=20, blank=True)
    enrollment_no = models.CharField("roll number", max_length=20, blank=True)
    student_email = models.EmailField("email for notifications", blank=True)
    drop_date = models.DateField(default=timezone.localdate)
    pickup_date = models.DateField()
    status = models.CharField(max_length=20, choices=Status.choices, default=Status.DRAFT)
    notes = models.TextField(blank=True)
    raw_extraction = models.JSONField(default=dict, blank=True)
    email_sent = models.BooleanField(default=False)
    reminder_sent = models.BooleanField(default=False)
    created_at = models.DateTimeField(auto_now_add=True)
    collected_at = models.DateTimeField(null=True, blank=True)

    class Meta:
        ordering = ["-drop_date", "-created_at"]

    def __str__(self):
        return f"Slip {self.slip_no or self.pk} - {self.student_name or self.user.username} ({self.drop_date})"

    @property
    def recipient_email(self):
        """Where notifications go: the slip's email, else rollno@domain, else the account email."""
        if self.student_email:
            return self.student_email
        if self.enrollment_no:
            return student_email_for(self.enrollment_no)
        return self.user.email

    @property
    def total_items(self):
        return sum(item.quantity for item in self.items.all())

    @property
    def total_returned(self):
        return sum(item.returned_quantity or 0 for item in self.items.all())

    @property
    def missing_count(self):
        return sum(item.missing for item in self.items.all())

    @property
    def days_until_pickup(self):
        return (self.pickup_date - timezone.localdate()).days

    @property
    def is_overdue(self):
        return self.status == self.Status.AT_LAUNDRY and self.days_until_pickup < 0


class LaundryItem(models.Model):
    slip = models.ForeignKey(LaundrySlip, on_delete=models.CASCADE, related_name="items")
    name = models.CharField(max_length=80)
    quantity = models.PositiveIntegerField(default=1)
    colour = models.CharField(max_length=60, blank=True)
    returned_quantity = models.PositiveIntegerField(null=True, blank=True)

    class Meta:
        ordering = ["id"]

    def __str__(self):
        return f"{self.quantity} x {self.name}"

    @property
    def missing(self):
        if self.returned_quantity is None:
            return 0
        return max(self.quantity - self.returned_quantity, 0)


class DailyUsage(models.Model):
    """Per-user daily counters that cap AI slip reads and outgoing email."""

    user = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.CASCADE, related_name="usage")
    day = models.DateField(default=timezone.localdate)
    uploads = models.PositiveIntegerField(default=0)
    emails = models.PositiveIntegerField(default=0)

    class Meta:
        constraints = [models.UniqueConstraint(fields=["user", "day"], name="one_usage_row_per_user_per_day")]

    def __str__(self):
        return f"{self.user.username} {self.day}: {self.uploads} reads, {self.emails} emails"

    @classmethod
    def today(cls, user):
        row, _ = cls.objects.get_or_create(user=user, day=timezone.localdate())
        return row

    @classmethod
    def bump(cls, user, field):
        row = cls.today(user)
        cls.objects.filter(pk=row.pk).update(**{field: models.F(field) + 1})
