import re

from django import forms
from django.utils import timezone

from .models import LaundrySlip, Profile, student_email_for
from .services.schedule import WEEKDAY_NAMES, default_weekdays, next_pickup_date, parse_weekdays

# The printed rows of the hostel laundry slip, in slip order.
SLIP_ROWS = [
    ("Shirt", "regular"), ("T-Shirt", "regular"), ("Pant", "regular"), ("Jeans Pant", "regular"),
    ("Lower", "regular"), ("Inner UPR+LOWER", "regular"), ("Bed Sheet+P. Cover", "regular"),
    ("Towel", "regular"), ("Shorts", "regular"),
    ("Coat/Tie", "dry_clean"), ("Sweater H/S", "dry_clean"), ("Sweater F/S", "dry_clean"),
    ("Jacket+S/Shirt", "dry_clean"), ("Blanket", "dry_clean"),
]
COMMON_ITEMS = [name for name, _ in SLIP_ROWS]


def _key(name):
    return "".join(ch for ch in str(name).lower() if ch.isalnum())


def build_slip_rows(items):
    """Lay items (dicts with name/quantity/colour) out in the paper slip's row order.

    Printed rows always appear; anything else becomes a custom row placed where
    the paper slip has its blank line (after the regular rows).
    """
    by_key = {}
    for item in items or []:
        by_key.setdefault(_key(item.get("name", "")), []).append(item)
    printed_keys = {_key(name) for name, _ in SLIP_ROWS}

    def row(name, section, custom=False):
        matches = by_key.get(_key(name), [])
        quantity = sum(int(m.get("quantity") or 0) for m in matches)
        colour = ", ".join(c for c in (str(m.get("colour") or "").strip() for m in matches) if c)
        return {"name": name, "quantity": quantity or "", "colour": colour, "section": section, "custom": custom}

    regular = [row(name, section) for name, section in SLIP_ROWS if section == "regular"]
    dry_clean = [row(name, section) for name, section in SLIP_ROWS if section == "dry_clean"]
    custom, seen = [], set()
    for item in items or []:
        key = _key(item.get("name", ""))
        if key and key not in printed_keys and key not in seen:
            seen.add(key)
            custom.append(row(item["name"], "regular", custom=True))
    return regular + custom + dry_clean


INPUT = {"class": "input"}
ROLL_RE = re.compile(r"^[0-9A-Za-z]{6,15}$")
WEEKDAY_CHOICES = [(str(i), name) for i, name in enumerate(WEEKDAY_NAMES)]


def clean_roll(value):
    """Normalise a roll / enrollment number; returns "" when it is not plausible."""
    roll = "".join(str(value or "").split()).upper()
    return roll if ROLL_RE.match(roll) else ""


def laundry_days_field():
    return forms.MultipleChoiceField(
        label="Your laundry days",
        choices=WEEKDAY_CHOICES,
        widget=forms.CheckboxSelectMultiple,
        initial=[str(d) for d in default_weekdays()],
        error_messages={"required": "Pick at least one laundry day."},
    )


class UploadForm(forms.Form):
    image = forms.ImageField()
    photo_key = forms.RegexField(regex=r"^[a-z0-9]{8,32}$", required=False)

    def clean_image(self):
        image = self.cleaned_data["image"]
        if image.size > 4 * 1024 * 1024:
            raise forms.ValidationError("This photo is too large. Take it again or pick a smaller image.")
        return image


class SlipForm(forms.ModelForm):
    """The four header fields of the paper slip plus the reminder date."""

    class Meta:
        model = LaundrySlip
        fields = ["student_name", "slip_no", "enrollment_no", "drop_date", "pickup_date", "notes"]
        widgets = {
            "slip_no": forms.TextInput(attrs=INPUT),
            "student_name": forms.TextInput(attrs=INPUT),
            "enrollment_no": forms.TextInput(attrs={**INPUT, "inputmode": "numeric", "autocomplete": "off"}),
            "drop_date": forms.DateInput(attrs={**INPUT, "type": "date"}, format="%Y-%m-%d"),
            "pickup_date": forms.DateInput(attrs={**INPUT, "type": "date"}, format="%Y-%m-%d"),
            "notes": forms.Textarea(attrs={**INPUT, "rows": 2}),
        }

    def __init__(self, *args, laundry_days=None, **kwargs):
        super().__init__(*args, **kwargs)
        self.laundry_days = parse_weekdays(laundry_days)

    def earliest_pickup(self, drop_date):
        """Ready on the next laundry day after the drop; a reminder is never set in the past."""
        return max(next_pickup_date(drop_date, self.laundry_days), timezone.localdate())

    def clean_enrollment_no(self):
        raw = self.cleaned_data.get("enrollment_no", "")
        roll = clean_roll(raw)
        if not roll:
            raise forms.ValidationError(
                "Enter the Roll No. written on the slip (6 to 15 letters or digits). The email goes to that student."
            )
        return roll

    def clean(self):
        cleaned = super().clean()
        drop, pickup = cleaned.get("drop_date"), cleaned.get("pickup_date")
        if drop and drop > timezone.localdate():
            self.add_error("drop_date", "The date on the slip cannot be in the future.")
        elif drop and pickup:
            earliest = self.earliest_pickup(drop)
            if pickup < earliest:
                self.add_error("pickup_date", f"Earliest is {earliest:%a %-d %b}.")
        return cleaned

    def save(self, commit=True):
        slip = super().save(commit=False)
        slip.student_email = student_email_for(slip.enrollment_no)
        if commit:
            slip.save()
        return slip


class ProfileForm(forms.ModelForm):
    first_name = forms.CharField(label="Name", max_length=60, widget=forms.TextInput(attrs=INPUT))
    laundry_days = laundry_days_field()

    class Meta:
        model = Profile
        fields = ["enrollment_no", "hostel", "room_no"]
        labels = {"enrollment_no": "Enrollment number", "room_no": "Room number"}
        widgets = {
            "enrollment_no": forms.TextInput(attrs={**INPUT, "inputmode": "numeric"}),
            "hostel": forms.TextInput(attrs=INPUT),
            "room_no": forms.TextInput(attrs=INPUT),
        }

    field_order = ["first_name", "enrollment_no", "hostel", "room_no", "laundry_days"]

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.fields["first_name"].initial = self.instance.user.first_name
        self.fields["laundry_days"].initial = [str(d) for d in self.instance.laundry_day_list]

    def clean_enrollment_no(self):
        raw = self.cleaned_data.get("enrollment_no", "")
        if not raw:
            return ""
        roll = clean_roll(raw)
        if not roll:
            raise forms.ValidationError("Use 6 to 15 letters or digits.")
        return roll

    def save(self, commit=True):
        profile = super().save(commit=False)
        profile.laundry_days = ",".join(sorted(self.cleaned_data["laundry_days"]))
        profile.user.first_name = self.cleaned_data["first_name"]
        if commit:
            profile.user.save(update_fields=["first_name"])
            profile.save()
        return profile
