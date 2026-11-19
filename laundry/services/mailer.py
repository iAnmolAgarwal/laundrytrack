"""Emails for LaundryTrack. Every public function returns (ok, message) and never raises.

HTML is built in Python with inline styles + tables only (Gmail strips <style>).
"""
from __future__ import annotations

import datetime as dt
from html import escape
from urllib.parse import urlencode
from zoneinfo import ZoneInfo

from django.conf import settings
from django.core.mail import EmailMultiAlternatives

BRAND = "LaundryTrack"
ACCENT = "#0b7a75"
TZ_NAME = "Asia/Kolkata"


# ------------------------------------------------------------------ helpers

def _site_url():
    return str(getattr(settings, "SITE_URL", "http://127.0.0.1:8000")).rstrip("/")


def _slip_url(slip):
    return f"{_site_url()}/slips/{slip.pk}/"


def _counter_hours():
    """(opens, closes) as datetime.time; the laundry counter hours are the same for everyone."""
    from .schedule import counter_hours
    return counter_hours()


def _clock(t):
    return t.strftime("%I:%M %p").lstrip("0")


def _hours_label():
    opens, closes = _counter_hours()
    return f"{_clock(opens)} to {_clock(closes)}"


def _tz():
    try:
        return ZoneInfo(getattr(settings, "TIME_ZONE", TZ_NAME) or TZ_NAME)
    except Exception:
        return ZoneInfo(TZ_NAME)


def _day(d):  # Tue 17 Nov
    return f"{d:%a} {d.day} {d:%b}"


def _long_day(d):  # Tuesday, 17 November 2026
    return f"{d:%A}, {d.day} {d:%B %Y}"


def _plural(n, word="cloth", plural="clothes"):
    return f"{n} {word if n == 1 else plural}"


def _items(slip):
    return list(slip.items.all())


def _greeting_name(slip):
    u = slip.user
    return (slip.student_name or getattr(u, "first_name", "") or getattr(u, "username", "") or "there").split(" ")[0]


def _pickup_window(slip):
    tz = _tz()
    opens, closes = _counter_hours()
    return (dt.datetime.combine(slip.pickup_date, opens, tzinfo=tz),
            dt.datetime.combine(slip.pickup_date, closes, tzinfo=tz))


def _items_text(items):
    return "\n".join(f"- {i.name}: {i.quantity}" + (f" ({i.colour})" if getattr(i, "colour", "") else "") for i in items)


def gcal_link(slip):
    start, end = _pickup_window(slip)
    fmt = "%Y%m%dT%H%M%S"
    items = _items(slip)
    details = (f"Collect {_plural(slip.total_items)} (slip {slip.slip_no or slip.pk}). Count them at the counter:\n"
               f"{_items_text(items)}\n\n{_slip_url(slip)}")
    q = urlencode({
        "action": "TEMPLATE",
        "text": f"Pick up laundry ({_plural(slip.total_items)})",
        "dates": f"{start.strftime(fmt)}/{end.strftime(fmt)}",
        "details": details,
        "ctz": TZ_NAME,
    })
    return f"https://calendar.google.com/calendar/render?{q}"


def _ics_escape(s):
    return (str(s).replace("\\", "\\\\").replace(";", "\\;").replace(",", "\\,").replace("\n", "\\n"))


def _fold(line):
    out, b = [], line.encode("utf-8")
    while len(b) > 74:
        cut = 74
        while cut > 0 and (b[cut] & 0xC0) == 0x80:  # don't split a UTF-8 char
            cut -= 1
        out.append(b[:cut].decode("utf-8"))
        b = b" " + b[cut:]
    out.append(b.decode("utf-8"))
    return "\r\n".join(out)


def build_ics(slip):
    start, end = _pickup_window(slip)
    s_utc, e_utc = start.astimezone(dt.timezone.utc), end.astimezone(dt.timezone.utc)
    f = "%Y%m%dT%H%M%SZ"
    now = dt.datetime.now(dt.timezone.utc).strftime(f)
    items = _items(slip)
    desc = (f"Collect {_plural(slip.total_items)} given on {_long_day(slip.drop_date)} "
            f"(slip {slip.slip_no or slip.pk}). Count them at the counter:\n{_items_text(items)}\n"
            f"Total: {slip.total_items}\n\n{_slip_url(slip)}")
    title = f"Pick up laundry ({_plural(slip.total_items)})"
    lines = [
        "BEGIN:VCALENDAR", "VERSION:2.0", f"PRODID:-//{BRAND}//EN", "CALSCALE:GREGORIAN", "METHOD:PUBLISH",
        "BEGIN:VEVENT",
        f"UID:laundry-slip-{slip.pk}@laundrytrack",
        f"DTSTAMP:{now}",
        f"DTSTART:{s_utc.strftime(f)}",
        f"DTEND:{e_utc.strftime(f)}",
        f"SUMMARY:{_ics_escape(title)}",
        f"DESCRIPTION:{_ics_escape(desc)}",
        f"URL:{_slip_url(slip)}",
        "STATUS:CONFIRMED", "TRANSP:TRANSPARENT",
        "BEGIN:VALARM", "ACTION:DISPLAY", f"DESCRIPTION:{_ics_escape(title + ' in 2 hours')}",
        "TRIGGER:-PT2H", "END:VALARM",
        "BEGIN:VALARM", "ACTION:DISPLAY", f"DESCRIPTION:{_ics_escape(title + ' now')}",
        "TRIGGER:PT0M", "END:VALARM",
        "END:VEVENT", "END:VCALENDAR",
    ]
    return "\r\n".join(_fold(l) for l in lines) + "\r\n"


# ------------------------------------------------------------------ HTML pieces

def _button(href, label, color=ACCENT):
    return (f'<table role="presentation" cellpadding="0" cellspacing="0" border="0" style="margin:8px 0;">'
            f'<tr><td style="border-radius:8px;background:{color};">'
            f'<a href="{escape(href)}" style="display:inline-block;padding:12px 20px;font-family:Arial,Helvetica,sans-serif;'
            f'font-size:15px;font-weight:bold;color:#ffffff;text-decoration:none;border-radius:8px;">{escape(label)}</a>'
            f'</td></tr></table>')


def _table(headers, rows, footer=None):
    th = "".join(
        f'<th align="{"left" if i == 0 else "right"}" style="padding:10px 12px;font-size:12px;text-transform:uppercase;'
        f'letter-spacing:.5px;color:#64748b;border-bottom:2px solid #e2e8f0;">{escape(h)}</th>'
        for i, h in enumerate(headers))
    body = ""
    for r in rows:
        cells, style = r if isinstance(r, tuple) else (r, "")
        body += "<tr>" + "".join(
            f'<td align="{"left" if i == 0 else "right"}" style="padding:10px 12px;font-size:15px;color:#0f172a;'
            f'border-bottom:1px solid #f1f5f9;{style}">{escape(str(c))}</td>' for i, c in enumerate(cells)) + "</tr>"
    foot = ""
    if footer:
        foot = "<tr>" + "".join(
            f'<td align="{"left" if i == 0 else "right"}" style="padding:12px;font-size:15px;font-weight:bold;'
            f'color:#0f172a;border-top:2px solid #e2e8f0;">{escape(str(c))}</td>' for i, c in enumerate(footer)) + "</tr>"
    return (f'<table role="presentation" width="100%" cellpadding="0" cellspacing="0" border="0" '
            f'style="border-collapse:collapse;font-family:Arial,Helvetica,sans-serif;margin:12px 0;">'
            f'<tr>{th}</tr>{body}{foot}</table>')


def _facts(pairs):
    rows = "".join(
        f'<tr><td style="padding:4px 0;font-size:14px;color:#64748b;width:110px;">{escape(k)}</td>'
        f'<td style="padding:4px 0;font-size:14px;color:#0f172a;font-weight:bold;">{escape(str(v))}</td></tr>'
        for k, v in pairs if v not in (None, ""))
    return (f'<table role="presentation" cellpadding="0" cellspacing="0" border="0" '
            f'style="font-family:Arial,Helvetica,sans-serif;margin:8px 0 4px;">{rows}</table>')


def _wrap(heading, inner_html, banner_color=ACCENT, preheader=""):
    return f"""<!doctype html><html><head><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1"></head>
<body style="margin:0;padding:0;background:#f1f5f9;">
<div style="display:none;max-height:0;overflow:hidden;">{escape(preheader)}</div>
<table role="presentation" width="100%" cellpadding="0" cellspacing="0" border="0" style="background:#f1f5f9;">
<tr><td align="center" style="padding:16px 8px;">
<table role="presentation" width="100%" cellpadding="0" cellspacing="0" border="0" style="max-width:560px;background:#ffffff;border-radius:12px;overflow:hidden;">
<tr><td style="background:{banner_color};padding:18px 20px;font-family:Arial,Helvetica,sans-serif;color:#ffffff;">
<div style="font-size:13px;opacity:.85;letter-spacing:.5px;">{BRAND}</div>
<div style="font-size:20px;font-weight:bold;margin-top:4px;line-height:1.3;">{escape(heading)}</div>
</td></tr>
<tr><td style="padding:20px;font-family:Arial,Helvetica,sans-serif;font-size:15px;line-height:1.5;color:#0f172a;">
{inner_html}
</td></tr>
<tr><td style="padding:14px 20px;background:#f8fafc;font-family:Arial,Helvetica,sans-serif;font-size:12px;color:#94a3b8;">
Sent by {BRAND} so you can prove what you handed in. Keep this email until your clothes are back.
</td></tr>
</table></td></tr></table></body></html>"""


def _p(text):
    return f'<p style="margin:0 0 12px;">{text}</p>'


def _send(slip, subject, text, html, attachments=()):
    to = (getattr(slip, "recipient_email", "") or getattr(slip.user, "email", "") or "").strip()
    if not to:
        return False, "no email on account"
    msg = EmailMultiAlternatives(subject=subject, body=text,
                                 from_email=getattr(settings, "DEFAULT_FROM_EMAIL", None), to=[to])
    msg.attach_alternative(html, "text/html")
    for name, content, mimetype in attachments:
        msg.attach(name, content, mimetype)
    sent = msg.send(fail_silently=False)
    if not sent:
        return False, "email backend reported 0 messages sent"
    return True, f"sent to {to}"


def _mark(slip, field):
    setattr(slip, field, True)
    slip.save(update_fields=[field])


# ------------------------------------------------------------------ public API

def send_drop_confirmation(slip, photo=None):
    """Confirmation email. `photo` is optional JPEG bytes of the slip, attached as proof and never stored."""
    try:
        items = _items(slip)
        n = slip.total_items
        pickup_time = _hours_label()
        subject = (f"Laundry submitted: {_plural(n)} on {_day(slip.drop_date)}, "
                   f"pick up {_day(slip.pickup_date)}")
        url, cal = _slip_url(slip), gcal_link(slip)
        name = _greeting_name(slip)

        text = (f"Hi {name},\n\nYou submitted {_plural(n)} to the laundry on {_long_day(slip.drop_date)}.\n"
                f"Reminder set for {_long_day(slip.pickup_date)}. The counter is open {pickup_time}.\n\n"
                f"{_items_text(items)}\nTotal: {n}\n\n"
                f"Laundry no: {slip.slip_no or '-'}\nRoll no: {getattr(slip, 'enrollment_no', '') or '-'}\n\n"
                f"View slip: {url}\nAdd to Google Calendar: {cal}\n"
                f"A calendar file (laundry-pickup.ics) is attached.\n"
                + ("The photo of your slip is attached too. Keep this email as proof of what you handed in.\n" if photo else ""))
        inner = (
            _p(f"Hi {escape(name)},")
            + _p(f"You handed in <b>{_plural(n)}</b> on <b>{escape(_long_day(slip.drop_date))}</b>. "
                 f"Your reminder is set for <b>{escape(_long_day(slip.pickup_date))}</b>; "
                 f"the counter is open {escape(pickup_time)}.")
            + _facts([("Laundry no", slip.slip_no or "-"), ("Roll no", getattr(slip, "enrollment_no", "") or "-"),
                      ("Given", _day(slip.drop_date)), ("Collect", f"{_day(slip.pickup_date)}, {pickup_time}")])
            + _table(["Particulars", "Qty", "Colour"], [[i.name, i.quantity, getattr(i, "colour", "") or ""] for i in items], footer=["Total", n, ""])
            + _button(cal, "Add pickup reminder to Google Calendar")
            + _p('<span style="font-size:13px;color:#64748b;">Using Apple/Outlook calendar? '
                 'Open the attached <b>laundry-pickup.ics</b>.</span>')
            + _button(url, "View slip", color="#0f172a")
            + (_p('<span style="font-size:13px;color:#64748b;">The photo of your paper slip is attached. '
                  'Keep this email as proof of what you handed in.</span>') if photo else "")
        )
        html = _wrap(f"{_plural(n).capitalize()} at the laundry", inner,
                     preheader=f"Collect from {_day(slip.pickup_date)}")
        attachments = [("laundry-pickup.ics", build_ics(slip), "text/calendar; method=PUBLISH; charset=utf-8")]
        if photo:
            attachments.append((f"laundry-slip-{slip.slip_no or slip.pk}.jpg", photo, "image/jpeg"))
        ok, message = _send(slip, subject, text, html, attachments)
        if ok:
            _mark(slip, "email_sent")
        return ok, message
    except Exception as e:
        return False, f"{type(e).__name__}: {e}"


def send_pickup_reminder(slip):
    try:
        items = _items(slip)
        n = slip.total_items
        hours = _hours_label()
        subject = f"Pick up your laundry today ({hours}): {_plural(n)} to count"
        url = _slip_url(slip)
        name = _greeting_name(slip)
        text = (f"Hi {name},\n\nThis is the reminder you set for the laundry you gave on {_long_day(slip.drop_date)}. "
                f"The counter is open {hours} today. Count these before you leave:\n\n"
                + "\n".join(f"[ ] {i.name} x {i.quantity}" for i in items)
                + f"\nTotal: {n}\n\nSlip no: {slip.slip_no or '-'}\nTick them off here: {url}\n")
        inner = (
            _p(f"Hi {escape(name)},")
            + _p(f"This is the reminder you set for the laundry you gave on <b>{escape(_long_day(slip.drop_date))}</b>. "
                 f"The counter is open <b>{escape(hours)}</b> today. Count these before you leave:")
            + _table(["Item", "Qty", "Got it"], [[i.name, i.quantity, "\u2610"] for i in items],
                     footer=["Total", n, ""])
            + _facts([("Laundry no", slip.slip_no or "-"), ("Roll no", getattr(slip, "enrollment_no", "") or "-")])
            + _button(url, "Tick off returned clothes")
        )
        html = _wrap(f"Pick up today: {_plural(n)}", inner, preheader="Count them at the counter")
        ok, message = _send(slip, subject, text, html)
        if ok:
            _mark(slip, "reminder_sent")
        return ok, message
    except Exception as e:
        return False, f"{type(e).__name__}: {e}"


def send_collection_summary(slip):
    try:
        items = _items(slip)
        n, back, missing = slip.total_items, slip.total_returned, slip.missing_count
        url = _slip_url(slip)
        name = _greeting_name(slip)
        if missing > 0:
            subject = f"Laundry mismatch: {missing} item{'s' if missing != 1 else ''} missing"
            heading = f"{missing} item{'s' if missing != 1 else ''} missing"
            rows = []
            for i in items:
                ret = i.returned_quantity if i.returned_quantity is not None else "-"
                style = "color:#b91c1c;font-weight:bold;background:#fef2f2;" if i.missing else ""
                rows.append(([i.name, i.quantity, ret, i.missing or ""], style))
            text = (f"Hi {name},\n\nYou gave {n} and got back {back}. {missing} missing.\n\n"
                    + "\n".join(f"- {i.name}: given {i.quantity}, returned "
                                f"{i.returned_quantity if i.returned_quantity is not None else '-'}, missing {i.missing}"
                                for i in items)
                    + f"\n\nSlip no: {slip.slip_no or '-'}, given {_long_day(slip.drop_date)}, "
                      f"due {_long_day(slip.pickup_date)}.\nShow this email to the laundry staff. Slip: {url}\n")
            inner = (
                _p(f"Hi {escape(name)},")
                + _p(f"You handed in <b>{n}</b> and got back <b>{back}</b>. "
                     f'<span style="color:#b91c1c;font-weight:bold;">{missing} missing.</span> '
                     f"Show this email to the laundry staff as proof.")
                + _facts([("Laundry no", slip.slip_no or "-"), ("Roll no", getattr(slip, "enrollment_no", "") or "-"),
                          ("Given", _long_day(slip.drop_date)), ("Due", _long_day(slip.pickup_date))])
                + _table(["Item", "Given", "Returned", "Missing"], rows, footer=["Total", n, back, missing])
                + _button(url, "Open slip")
            )
            html = _wrap(heading, inner, banner_color="#b91c1c", preheader=f"Given {n}, returned {back}")
        else:
            subject = f"All {_plural(n)} collected"
            text = (f"Hi {name},\n\nAll {_plural(n)} from {_long_day(slip.drop_date)} are back.\n\n"
                    f"{_items_text(items)}\nTotal: {n}\n\nSlip: {url}\n")
            inner = (
                _p(f"Hi {escape(name)},")
                + _p(f"All <b>{_plural(n)}</b> from {escape(_long_day(slip.drop_date))} are back. Nothing missing.")
                + _table(["Item", "Given", "Returned"],
                         [[i.name, i.quantity, i.returned_quantity if i.returned_quantity is not None else i.quantity]
                          for i in items], footer=["Total", n, back or n])
                + _button(url, "View slip", color="#0f172a")
            )
            html = _wrap(f"All {_plural(n)} collected", inner, banner_color="#15803d")
        return _send(slip, subject, text, html)
    except Exception as e:
        return False, f"{type(e).__name__}: {e}"
