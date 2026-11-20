import base64
import binascii
import hmac
import os
import secrets
import tempfile
from datetime import date

from django.conf import settings
from django.contrib import messages
from django.contrib.auth import login
from django.contrib.auth.decorators import login_required
from django.http import HttpResponseForbidden, JsonResponse
from django.shortcuts import get_object_or_404, redirect, render
from django.urls import reverse
from django.utils import timezone
from django.utils.http import url_has_allowed_host_and_scheme
from django.views.decorators.http import require_GET, require_POST

from .forms import ProfileForm, SlipForm, UploadForm, build_slip_rows, clean_roll
from .models import DailyUsage, LaundryItem, LaundrySlip, Profile, student_email_for
from .services import google, mailer
from .services.extract import extract_slip
from .services.reminders import send_due_reminders
from .services.schedule import next_pickup_date, weekdays_label

Status = LaundrySlip.Status
MAX_PHOTO_BYTES = 3 * 1024 * 1024


def _user_slip(request, pk):
    return get_object_or_404(LaundrySlip, pk=pk, user=request.user)


def _profile(user):
    profile, _ = Profile.objects.get_or_create(user=user)
    return profile


def _parse_date(value):
    try:
        return date.fromisoformat(str(value)[:10])
    except (TypeError, ValueError):
        return None


def _notify(request, result, success_text):
    """Turn a mailer (ok, message) result into a flash message."""
    ok, detail = result
    if ok:
        if success_text:
            messages.success(request, success_text)
    else:
        messages.warning(request, f"Email not sent: {detail}")
    return ok


def _send_limited(request, send, success_text):
    """Send an email unless the user has used up today's allowance."""
    if DailyUsage.today(request.user).emails >= settings.LAUNDRY_DAILY_EMAIL_LIMIT:
        messages.warning(request, "Email not sent: daily email limit reached.")
        return False
    DailyUsage.bump(request.user, "emails")
    return _notify(request, send(), success_text)


def _photo_from_data_url(value):
    """Decode the browser's copy of the slip photo (a JPEG data URL). Returns bytes or None."""
    prefix = "data:image/jpeg;base64,"
    if not value or not value.startswith(prefix):
        return None
    try:
        data = base64.b64decode(value[len(prefix):], validate=True)
    except (binascii.Error, ValueError):
        return None
    if not data.startswith(b"\xff\xd8") or len(data) > MAX_PHOTO_BYTES:
        return None
    return data


def _read_slip(uploaded):
    """Run the slip reader on an uploaded photo. The file only ever lives in a temp file."""
    suffix = os.path.splitext(uploaded.name or "")[1].lower() or ".jpg"
    with tempfile.NamedTemporaryFile(suffix=suffix, prefix="slip_", delete=False) as handle:
        for chunk in uploaded.chunks():
            handle.write(chunk)
        path = handle.name
    try:
        return extract_slip(path)
    finally:
        try:
            os.remove(path)
        except OSError:
            pass


def _is_blank(data):
    return not (data.get("student_name") or data.get("slip_no") or data.get("enrollment_no") or data.get("items"))


def _safe_next(request, value):
    """A local path to return to after signing in, or "" when the value is not one."""
    ok = value and url_has_allowed_host_and_scheme(value, allowed_hosts={request.get_host()}, require_https=request.is_secure())
    return value if ok else ""


def _google_redirect_uri(request):
    return request.build_absolute_uri(reverse("google_callback"))


@require_GET
def login_page(request):
    if request.user.is_authenticated:
        return redirect("dashboard")
    context = {
        "next": _safe_next(request, request.GET.get("next", "")),
        "google_ready": google.is_configured(),
        "email_domain": settings.STUDENT_EMAIL_DOMAIN,
    }
    return render(request, "registration/login.html", context)


@require_GET
def google_login(request):
    """Send the browser to Google to pick an institute account."""
    if request.user.is_authenticated:
        return redirect("dashboard")
    if not google.is_configured():
        return redirect("login")
    state, nonce = secrets.token_urlsafe(24), secrets.token_urlsafe(24)
    request.session["google_oauth"] = {
        "state": state,
        "nonce": nonce,
        "next": _safe_next(request, request.GET.get("next", "")),
    }
    return redirect(google.auth_url(_google_redirect_uri(request), state, nonce))


@require_GET
def google_callback(request):
    """Google sends the browser back here with a one-time code."""
    saved = request.session.pop("google_oauth", None) or {}
    if request.user.is_authenticated:
        return redirect("dashboard")
    code = request.GET.get("code", "")
    state = request.GET.get("state", "")
    try:
        if request.GET.get("error") == "access_denied":
            raise google.GoogleAuthError("Sign-in was cancelled.")
        if not code or not saved or not hmac.compare_digest(state, saved["state"]):
            raise google.GoogleAuthError("Google sign-in failed. Try again.")
        claims = google.exchange_code(code, _google_redirect_uri(request), saved["nonce"])
    except google.GoogleAuthError as error:
        messages.error(request, str(error))
        return redirect("login")
    user, created = google.user_for_claims(claims)
    if not user.is_active:
        messages.error(request, "This account is disabled.")
        return redirect("login")
    login(request, user)
    if created:
        days = weekdays_label(_profile(user).laundry_days)
        messages.success(request, f"Welcome, {user.first_name or user.username}. Your laundry days are {days}; change them in Settings.")
    return redirect(saved.get("next") or "dashboard")


@login_required
def profile_settings(request):
    form = ProfileForm(request.POST or None, instance=_profile(request.user))
    if request.method == "POST" and form.is_valid():
        form.save()
        messages.success(request, "Settings saved.")
        return redirect("settings")
    return render(request, "laundry/settings.html", {"form": form})


@login_required
def dashboard(request):
    slips = (
        LaundrySlip.objects.filter(user=request.user)
        .exclude(status=Status.DRAFT)
        .prefetch_related("items")
    )
    active = [s for s in slips if s.status == Status.AT_LAUNDRY]
    next_slip = min(active, key=lambda s: s.pickup_date, default=None)
    stats = {
        "active_count": len(active),
        "clothes_out": sum(s.total_items for s in active),
        "collected_count": sum(1 for s in slips if s.status == Status.COLLECTED),
        "mismatch_count": sum(1 for s in slips if s.status == Status.MISMATCH),
        "next_pickup": next_slip.pickup_date if next_slip else None,
        "next_pickup_slip": next_slip,
    }
    return render(request, "laundry/dashboard.html", {"slips": slips, "stats": stats, "today": timezone.localdate()})


@login_required
def upload(request):
    limit = settings.LAUNDRY_DAILY_UPLOAD_LIMIT
    used = DailyUsage.today(request.user).uploads
    form = UploadForm(request.POST or None, request.FILES or None)
    if request.method == "POST" and used >= limit:
        form.add_error(None, "No slip reads left today.")
    elif request.method == "POST" and form.is_valid():
        DailyUsage.bump(request.user, "uploads")
        today = timezone.localdate()
        days = _profile(request.user).laundry_day_list
        data = _read_slip(form.cleaned_data["image"])
        data["photo_key"] = form.cleaned_data.get("photo_key") or ""
        data["blank"] = bool(data.get("ok")) and _is_blank(data)

        # Only what is written on the slip goes into the form: nothing is taken
        # from the account, so a blank slip stays blank.
        slip = LaundrySlip(user=request.user, drop_date=today, raw_extraction=data)
        if data.get("ok"):
            slip.slip_no = (data.get("slip_no") or "")[:40]
            slip.student_name = (data.get("student_name") or "")[:120]
            slip.room_no = (data.get("room_no") or "")[:20]
            slip.enrollment_no = clean_roll(data.get("enrollment_no"))
            written = _parse_date(data.get("date"))
            # Trust the date on the slip only when it is plausible (not in the
            # future, not more than a fortnight old); otherwise keep today.
            if written and 0 <= (today - written).days <= 14:
                slip.drop_date = written
        slip.pickup_date = max(next_pickup_date(slip.drop_date, days), today)
        slip.student_email = student_email_for(slip.enrollment_no)
        # One slip is checked at a time: an earlier upload that was never confirmed is dropped.
        LaundrySlip.objects.filter(user=request.user, status=Status.DRAFT).delete()
        slip.save()
        return redirect("review", pk=slip.pk)
    context = {"form": form, "uploads_left": max(limit - used, 0), "daily_limit": limit}
    return render(request, "laundry/upload.html", context)


@login_required
def review(request, pk):
    slip = _user_slip(request, pk)
    extraction = slip.raw_extraction or {}
    days = _profile(request.user).laundry_day_list
    if request.method == "GET" and slip.status == Status.DRAFT:
        # The student may have changed their laundry days since the photo was
        # read; never offer a reminder date earlier than the clothes are ready.
        slip.pickup_date = max(slip.pickup_date, next_pickup_date(slip.drop_date, days), timezone.localdate())
    form = SlipForm(request.POST or None, instance=slip, laundry_days=days)

    if request.method == "POST":
        names = request.POST.getlist("item_name")
        qtys = request.POST.getlist("item_qty")
        colours = request.POST.getlist("item_colour")
        colours += [""] * (len(names) - len(colours))
        items = []
        for name, qty, colour in zip(names, qtys, colours):
            name = name.strip()
            try:
                qty = int(qty or 0)
            except ValueError:
                qty = 0
            if name and qty > 0:
                items.append({"name": name[:80], "quantity": qty, "colour": colour.strip()[:60]})
        if not items:
            form.add_error(None, "Add at least one item with a quantity.")
        if form.is_valid():
            slip = form.save(commit=False)
            slip.status = Status.AT_LAUNDRY
            slip.reminder_sent = False
            slip.save()
            slip.items.all().delete()
            LaundryItem.objects.bulk_create(LaundryItem(slip=slip, **item) for item in items)
            total = sum(item["quantity"] for item in items)
            photo = _photo_from_data_url(request.POST.get("photo_data", ""))
            _send_limited(
                request,
                lambda: mailer.send_drop_confirmation(slip, photo=photo),
                f"Saved {total} clothes. Confirmation sent.",
            )
            return redirect("slip_detail", pk=slip.pk)
    elif slip.status != Status.DRAFT:
        items = [{"name": i.name, "quantity": i.quantity, "colour": i.colour} for i in slip.items.all()]
    else:
        items = extraction.get("items") or []

    drop = _parse_date(form["drop_date"].value()) or slip.drop_date
    context = {
        "slip": slip,
        "form": form,
        "items": items,
        "rows": build_slip_rows(items),
        "email_domain": settings.STUDENT_EMAIL_DOMAIN,
        "photo_key": extraction.get("photo_key", ""),
        "earliest_pickup": form.earliest_pickup(drop),
        "extraction": {
            "ok": bool(extraction.get("ok")),
            "engine": extraction.get("engine", ""),
            "error": extraction.get("error", ""),
            "blank": bool(extraction.get("blank")),
        },
    }
    return render(request, "laundry/review.html", context)


@login_required
def slip_detail(request, pk):
    slip = _user_slip(request, pk)
    if slip.status == Status.DRAFT:
        return redirect("review", pk=slip.pk)
    return render(request, "laundry/detail.html", {"slip": slip, "items": slip.items.all()})


@login_required
def collect(request, pk):
    slip = _user_slip(request, pk)
    if slip.status == Status.DRAFT:
        return redirect("review", pk=slip.pk)
    items = list(slip.items.all())
    if request.method == "POST":
        for item in items:
            try:
                returned = int(request.POST.get(f"returned_{item.id}", item.quantity))
            except ValueError:
                returned = item.quantity
            item.returned_quantity = max(returned, 0)
            item.save(update_fields=["returned_quantity"])
        missing = sum(item.missing for item in items)
        slip.status = Status.MISMATCH if missing else Status.COLLECTED
        slip.collected_at = timezone.now()
        slip.save(update_fields=["status", "collected_at"])
        if missing:
            # The page itself shows what is missing; the flash only confirms the save.
            messages.info(request, "Saved.")
        else:
            messages.success(request, "All clothes collected.")
        _send_limited(request, lambda: mailer.send_collection_summary(slip), "")
        return redirect("slip_detail", pk=slip.pk)
    return render(request, "laundry/collect.html", {"slip": slip, "items": items})


@login_required
@require_POST
def resend_email(request, pk):
    slip = _user_slip(request, pk)
    _send_limited(
        request, lambda: mailer.send_drop_confirmation(slip), "Confirmation sent."
    )
    return redirect("slip_detail", pk=slip.pk)


@login_required
@require_POST
def send_reminder(request, pk):
    slip = _user_slip(request, pk)
    _send_limited(
        request, lambda: mailer.send_pickup_reminder(slip), "Reminder sent."
    )
    return redirect("slip_detail", pk=slip.pk)


@login_required
@require_POST
def slip_delete(request, pk):
    slip = _user_slip(request, pk)
    slip.delete()
    messages.info(request, "Slip deleted.")
    return redirect("dashboard")


@require_GET
def cron_send_reminders(request):
    """Called once a day by the host's scheduler. Sends the reminders due today.

    Protected by a shared secret sent as `Authorization: Bearer <CRON_SECRET>`.
    """
    secret = settings.CRON_SECRET
    supplied = request.headers.get("Authorization", "")
    if not secret or not hmac.compare_digest(supplied, f"Bearer {secret}"):
        return HttpResponseForbidden("forbidden")
    results = send_due_reminders()
    sent = sum(1 for r in results if r["ok"])
    return JsonResponse({"date": timezone.localdate().isoformat(), "sent": sent, "failed": len(results) - sent})
