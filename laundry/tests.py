import base64
import io
import json
import time
from datetime import date, timedelta
from unittest import mock
from urllib.parse import parse_qs, urlparse

from django.contrib.auth.models import User
from django.core import mail
from django.core.files.uploadedfile import SimpleUploadedFile
from django.test import TestCase, override_settings
from django.urls import reverse
from django.utils import timezone
from PIL import Image

from .models import DailyUsage, LaundrySlip, Profile
from .services import google
from .services.schedule import counter_hours_label, next_pickup_date, weekdays_label

LOCMEM = {"default": {"BACKEND": "django.core.mail.backends.locmem.EmailBackend"}}
TODAY = timezone.localdate()


def extraction(**overrides):
    data = {
        "ok": True, "engine": "test", "error": "",
        "slip_no": "1005", "student_name": "Test Student", "enrollment_no": "2401039999", "room_no": "", "date": "",
        "items": [
            {"name": "Shirt", "quantity": 2, "colour": "White"},
            {"name": "Jeans Pant", "quantity": 1, "colour": "Blue"},
            {"name": "Towel", "quantity": 2, "colour": ""},
        ],
        "total": 5, "notes": "",
    }
    data.update(overrides)
    return data


BLANK = extraction(slip_no="", student_name="", enrollment_no="", items=[], total=None,
                   notes="The slip is blank; no handwritten entries are visible.")


def jpeg_bytes():
    buf = io.BytesIO()
    Image.new("RGB", (40, 40), "white").save(buf, "JPEG")
    return buf.getvalue()


def photo():
    return SimpleUploadedFile("slip.jpg", jpeg_bytes(), content_type="image/jpeg")


def photo_data_url():
    return "data:image/jpeg;base64," + base64.b64encode(jpeg_bytes()).decode()


class ScheduleTests(TestCase):
    def test_tuesday_goes_to_friday(self):
        self.assertEqual(next_pickup_date(date(2026, 11, 17)), date(2026, 11, 20))

    def test_friday_goes_to_next_tuesday(self):
        self.assertEqual(next_pickup_date(date(2026, 11, 20)), date(2026, 11, 24))

    def test_other_days_go_to_next_laundry_day(self):
        self.assertEqual(next_pickup_date(date(2026, 11, 19)), date(2026, 11, 20))  # Thu -> Fri
        self.assertEqual(next_pickup_date(date(2026, 11, 21)), date(2026, 11, 24))  # Sat -> Tue

    def test_each_student_can_have_different_laundry_days(self):
        self.assertEqual(next_pickup_date(date(2026, 11, 16), [0, 3]), date(2026, 11, 19))  # Mon -> Thu
        self.assertEqual(next_pickup_date(date(2026, 11, 19), "0,3"), date(2026, 11, 23))   # Thu -> Mon
        self.assertEqual(weekdays_label("0,3"), "Monday and Thursday")

    def test_counter_hours_are_the_same_for_everyone(self):
        self.assertEqual(counter_hours_label(), "4:00 PM to 6:30 PM")


@override_settings(MAILERS=LOCMEM)
class FlowTests(TestCase):
    def signup(self, username="tester", roll="2401031111", days=("1", "4")):
        """Make an account the way a first Google sign-in does, and sign it in."""
        user = User.objects.create(username=username, first_name="Test", email=f"{roll}@mail.jiit.ac.in")
        Profile.objects.create(user=user, enrollment_no=roll, hostel="H4", room_no="B-204", laundry_days=",".join(days))
        self.client.force_login(user)
        return user

    def upload(self, data=None):
        with mock.patch("laundry.views.extract_slip", return_value=data or extraction()):
            return self.client.post(reverse("upload"), {"image": photo(), "photo_key": "abc123def456ghij"})

    def confirm(self, slip, **overrides):
        earliest = next_pickup_date(TODAY)
        data = {
            "slip_no": "1005", "student_name": "Test Student", "enrollment_no": "2401039999",
            "drop_date": TODAY.isoformat(), "pickup_date": earliest.isoformat(), "notes": "",
            "item_name": ["Shirt", "T-Shirt", "Jeans Pant", "Towel"], "item_qty": ["2", "", "1", "2"],
            "item_colour": ["White", "", "Blue", ""],
        }
        data.update(overrides)
        return self.client.post(reverse("review", args=[slip.pk]), data)

    def test_dashboard_requires_login(self):
        self.assertEqual(self.client.get(reverse("dashboard")).status_code, 302)

    def test_full_cycle_upload_confirm_collect(self):
        self.signup()
        self.assertEqual(self.client.get(reverse("dashboard")).status_code, 200)
        self.assertEqual(self.client.get(reverse("upload")).status_code, 200)

        response = self.upload()
        slip = LaundrySlip.objects.get()
        self.assertRedirects(response, reverse("review", args=[slip.pk]))
        self.assertEqual(slip.status, "draft")
        self.assertEqual(slip.enrollment_no, "2401039999")

        page = self.client.get(reverse("review", args=[slip.pk]))
        self.assertContains(page, "Jeans Pant")
        self.assertContains(page, "Blanket")            # every printed slip row is shown
        self.assertContains(page, "abc123def456ghij")   # key of the photo kept in the browser

        response = self.confirm(slip, photo_data=photo_data_url())
        self.assertRedirects(response, reverse("slip_detail", args=[slip.pk]))
        slip.refresh_from_db()
        self.assertEqual(slip.status, "at_laundry")
        self.assertEqual(slip.total_items, 5)
        self.assertEqual(slip.items.count(), 3)          # the blank T-Shirt row is ignored
        self.assertEqual(slip.items.get(name="Jeans Pant").colour, "Blue")
        self.assertTrue(slip.email_sent)

        # The mail goes to the roll number on the slip, with the calendar file and the photo.
        self.assertEqual(len(mail.outbox), 1)
        self.assertEqual(mail.outbox[0].to, ["2401039999@mail.jiit.ac.in"])
        self.assertIn("5", mail.outbox[0].subject)
        names = [attachment[0] for attachment in mail.outbox[0].attachments]
        self.assertTrue(any(name.endswith(".ics") for name in names))
        self.assertTrue(any(name.endswith(".jpg") for name in names))

        self.assertEqual(self.client.get(reverse("slip_detail", args=[slip.pk])).status_code, 200)
        self.assertEqual(self.client.get(reverse("dashboard")).status_code, 200)
        self.assertEqual(self.client.get(reverse("collect", args=[slip.pk])).status_code, 200)

        self.client.post(reverse("send_reminder", args=[slip.pk]))
        self.assertEqual(len(mail.outbox), 2)

        items = list(slip.items.all())
        data = {f"returned_{item.id}": item.quantity for item in items}
        data[f"returned_{items[0].id}"] = items[0].quantity - 1  # one shirt short
        self.assertRedirects(self.client.post(reverse("collect", args=[slip.pk]), data),
                             reverse("slip_detail", args=[slip.pk]))
        slip.refresh_from_db()
        self.assertEqual(slip.status, "mismatch")
        self.assertEqual(slip.missing_count, 1)
        self.assertEqual(len(mail.outbox), 3)
        self.assertContains(self.client.get(reverse("slip_detail", args=[slip.pk])), "missing")

    def test_blank_slip_is_not_filled_from_the_account(self):
        self.signup()
        self.upload(BLANK)
        slip = LaundrySlip.objects.get()
        self.assertEqual((slip.student_name, slip.slip_no, slip.enrollment_no, slip.student_email), ("", "", "", ""))
        page = self.client.get(reverse("review", args=[slip.pk]))
        self.assertContains(page, "looks blank")
        # Nothing from the account (name "Test", roll 2401031111) is pre-filled into the slip.
        self.assertNotContains(page, 'value="2401031111"')
        self.assertNotContains(page, 'value="Test"')

    def test_confirm_needs_a_roll_number_and_an_item(self):
        self.signup()
        self.upload(BLANK)
        slip = LaundrySlip.objects.get()
        self.assertEqual(self.confirm(slip, enrollment_no="").status_code, 200)
        self.assertEqual(self.confirm(slip, item_qty=["", "", "", ""]).status_code, 200)
        self.assertEqual(LaundrySlip.objects.get().status, "draft")
        self.assertEqual(len(mail.outbox), 0)

    def test_reminder_date_cannot_be_before_the_next_laundry_day(self):
        self.signup()
        self.upload()
        slip = LaundrySlip.objects.get()
        earliest = next_pickup_date(TODAY)
        too_early = self.confirm(slip, pickup_date=(earliest - timedelta(days=1)).isoformat())
        self.assertEqual(too_early.status_code, 200)
        self.assertEqual(LaundrySlip.objects.get().status, "draft")
        later = self.confirm(slip, pickup_date=(earliest + timedelta(days=5)).isoformat())  # any later day is fine
        self.assertRedirects(later, reverse("slip_detail", args=[slip.pk]))
        self.assertEqual(LaundrySlip.objects.get().pickup_date, earliest + timedelta(days=5))

    def test_reminder_for_an_old_slip_is_never_in_the_past(self):
        self.signup()
        old_date = TODAY - timedelta(days=9)
        self.upload(extraction(date=old_date.isoformat()))
        slip = LaundrySlip.objects.get()
        self.assertEqual(slip.drop_date, old_date)
        self.assertEqual(slip.pickup_date, TODAY)        # already ready, so the default is today
        yesterday = self.confirm(slip, drop_date=old_date.isoformat(), pickup_date=(TODAY - timedelta(days=1)).isoformat())
        self.assertEqual(yesterday.status_code, 200)
        self.assertEqual(LaundrySlip.objects.get().status, "draft")

    def test_laundry_days_from_settings_change_the_earliest_date(self):
        self.signup()
        response = self.client.post(reverse("settings"), {
            "first_name": "Test", "enrollment_no": "2401031111", "hostel": "H4", "room_no": "", "laundry_days": ["0", "3"],
        })
        self.assertRedirects(response, reverse("settings"))
        self.upload()
        slip = LaundrySlip.objects.get()
        self.assertEqual(slip.pickup_date, next_pickup_date(TODAY, [0, 3]))
        self.assertContains(self.client.get(reverse("dashboard")), "Monday and Thursday")

    def test_draft_reminder_date_follows_a_later_change_of_laundry_days(self):
        self.signup()
        self.upload()
        slip = LaundrySlip.objects.get()
        self.client.post(reverse("settings"), {
            "first_name": "Test", "enrollment_no": "2401031111", "hostel": "", "room_no": "", "laundry_days": ["6"],
        })
        # The reminder moves to the new laundry day unless it was already later than that.
        earliest = max(slip.pickup_date, next_pickup_date(slip.drop_date, [6]))
        page = self.client.get(reverse("review", args=[slip.pk]))
        self.assertContains(page, f'value="{earliest.isoformat()}"')

    @override_settings(LAUNDRY_DAILY_UPLOAD_LIMIT=2)
    def test_daily_limit_on_slip_reads(self):
        self.signup()
        self.upload()
        self.upload()
        self.assertEqual(LaundrySlip.objects.count(), 1)   # the second upload replaced the first draft
        with mock.patch("laundry.views.extract_slip") as reader:
            response = self.client.post(reverse("upload"), {"image": photo()})
        self.assertEqual(response.status_code, 200)
        reader.assert_not_called()
        self.assertEqual(LaundrySlip.objects.count(), 1)
        self.assertEqual(DailyUsage.objects.get().uploads, 2)

    def test_a_new_upload_replaces_an_unconfirmed_one(self):
        self.signup()
        self.upload()
        first = LaundrySlip.objects.get()
        self.confirm(first)                       # confirmed slips are kept
        self.upload()
        self.upload()                             # the second unconfirmed upload replaces the first
        self.assertEqual(LaundrySlip.objects.filter(status="draft").count(), 1)
        self.assertEqual(LaundrySlip.objects.filter(status="at_laundry").count(), 1)

    def test_other_users_slip_is_not_visible(self):
        self.signup()
        self.upload()
        slip = LaundrySlip.objects.get()
        self.client.post(reverse("logout"))
        self.signup(username="other", roll="2401032222")
        self.assertEqual(self.client.get(reverse("review", args=[slip.pk])).status_code, 404)
        self.assertEqual(self.client.get(reverse("slip_detail", args=[slip.pk])).status_code, 404)

    @override_settings(CRON_SECRET="s3cret")
    def test_scheduled_reminders_need_the_secret_and_send_only_what_is_due(self):
        self.signup()
        self.upload()
        slip = LaundrySlip.objects.get()
        self.confirm(slip)
        mail.outbox.clear()
        url = reverse("cron_send_reminders")
        self.assertEqual(self.client.get(url).status_code, 403)
        self.assertEqual(self.client.get(url, headers={"Authorization": "Bearer wrong"}).status_code, 403)

        ok = {"Authorization": "Bearer s3cret"}
        self.assertEqual(self.client.get(url, headers=ok).json()["sent"], 0)   # reminder date is not today
        LaundrySlip.objects.update(pickup_date=TODAY)
        self.assertEqual(self.client.get(url, headers=ok).json()["sent"], 1)
        self.assertEqual(mail.outbox[0].to, ["2401039999@mail.jiit.ac.in"])
        self.assertEqual(self.client.get(url, headers=ok).json()["sent"], 0)   # never sent twice


def id_token(**overrides):
    claims = {
        "iss": "https://accounts.google.com", "aud": "client-id", "exp": time.time() + 600, "nonce": "",
        "email": "2401031111@mail.jiit.ac.in", "email_verified": True, "hd": "mail.jiit.ac.in", "name": "Asha Verma",
    }
    claims.update(overrides)
    body = base64.urlsafe_b64encode(json.dumps(claims).encode()).decode().rstrip("=")
    return f"header.{body}.signature"


@override_settings(GOOGLE_CLIENT_ID="client-id", GOOGLE_CLIENT_SECRET="client-secret")
class GoogleSignInTests(TestCase):
    def start(self, **params):
        """Begin sign-in; returns the query Google would receive."""
        response = self.client.get(reverse("google_login"), params)
        self.assertEqual(response.status_code, 302)
        self.assertTrue(response["Location"].startswith(google.AUTH_ENDPOINT))
        return parse_qs(urlparse(response["Location"]).query)

    def finish(self, sent, **claims):
        """Come back from Google with a code, with Google's token reply stubbed."""
        reply = io.BytesIO(json.dumps({"id_token": id_token(nonce=sent["nonce"][0], **claims)}).encode())
        with mock.patch("laundry.services.google.urllib.request.urlopen", return_value=reply) as urlopen:
            response = self.client.get(reverse("google_callback"), {"code": "one-time", "state": sent["state"][0]})
        self.token_request = urlopen.call_args[0][0] if urlopen.called else None
        return response

    def test_login_page_offers_only_google(self):
        page = self.client.get(reverse("login"))
        self.assertContains(page, "Continue with Google")
        self.assertNotContains(page, "<input")

    def test_first_sign_in_creates_the_account_from_the_institute_email(self):
        sent = self.start()
        self.assertEqual(sent["hd"], ["mail.jiit.ac.in"])
        self.assertEqual(sent["redirect_uri"], ["http://testserver/accounts/google/callback/"])
        self.assertRedirects(self.finish(sent), reverse("dashboard"))
        self.assertIn(b"client_secret=client-secret", self.token_request.data)

        user = User.objects.get()
        self.assertEqual((user.username, user.email, user.first_name), ("2401031111@mail.jiit.ac.in",) * 2 + ("Asha Verma",))
        self.assertFalse(user.has_usable_password())
        self.assertEqual(user.profile.enrollment_no, "2401031111")   # a student mailbox is the roll number
        self.assertEqual(self.client.get(reverse("dashboard")).status_code, 200)

        self.client.post(reverse("logout"))
        self.assertRedirects(self.finish(self.start()), reverse("dashboard"))
        self.assertEqual(User.objects.count(), 1)                     # signing in again reuses the account

    def test_staff_name_comes_from_the_mailbox_when_google_gives_none(self):
        self.finish(self.start(), email="Asha.Verma2@mail.jiit.ac.in", name="")
        user = User.objects.get()
        self.assertEqual(user.first_name, "Asha Verma")
        self.assertEqual(user.profile.enrollment_no, "")

    def test_an_account_made_before_google_sign_in_keeps_its_slips(self):
        old = User.objects.create_user("tester", email="2401031111@mail.jiit.ac.in", password="laundry-pass-91")
        self.finish(self.start())
        self.assertEqual(User.objects.get(), old)
        self.assertEqual(int(self.client.session["_auth_user_id"]), old.pk)

    def test_only_verified_institute_accounts_get_in(self):
        for claims in (
            {"email": "someone@gmail.com", "hd": None},
            {"email": "someone@other.ac.in", "hd": "other.ac.in"},
            {"email": "x@mail.jiit.ac.in.evil.com", "hd": "evil.com"},
            {"email_verified": False},
            {"hd": None},                       # a personal Google account using an institute-looking address
        ):
            response = self.finish(self.start(), **claims)
            self.assertRedirects(response, reverse("login"))
            self.assertContains(self.client.get(reverse("login")), "@mail.jiit.ac.in")
        self.assertFalse(User.objects.exists())

    def test_a_forged_or_stale_reply_is_refused(self):
        sent = self.start()
        for claims in ({"aud": "another-app"}, {"exp": time.time() - 5}, {"iss": "https://evil.example"}, {"nonce": "x"}):
            reply = io.BytesIO(json.dumps({"id_token": id_token(**{"nonce": sent["nonce"][0], **claims})}).encode())
            with mock.patch("laundry.services.google.urllib.request.urlopen", return_value=reply):
                self.client.get(reverse("google_callback"), {"code": "c", "state": sent["state"][0]})
            sent = self.start()
        self.assertRedirects(self.finish(sent | {"state": ["wrong"]}), reverse("login"))   # state must match
        self.assertRedirects(self.finish(sent), reverse("login"))                           # and works only once
        self.assertIsNone(self.token_request)
        self.assertRedirects(self.client.get(reverse("google_callback"), {"error": "access_denied"}), reverse("login"))
        self.assertFalse(User.objects.exists())

    def test_returns_to_the_page_that_asked_for_sign_in_but_never_off_site(self):
        self.assertRedirects(self.finish(self.start(next="/settings/")), reverse("settings"))
        self.client.post(reverse("logout"))
        self.assertRedirects(self.finish(self.start(next="https://evil.example/")), reverse("dashboard"))

    @override_settings(GOOGLE_CLIENT_ID="")
    def test_without_credentials_the_button_is_replaced_by_a_note(self):
        self.assertContains(self.client.get(reverse("login")), "not set up")
        self.assertRedirects(self.client.get(reverse("google_login")), reverse("login"))
