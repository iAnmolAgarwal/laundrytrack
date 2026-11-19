"""Emails for LaundryTrack. Every public function returns (ok, message) and never raises.

HTML is built in Python with inline styles + tables only (Gmail strips <style>).
The look follows the web app: paper background, text wordmark, small uppercase
labels, status as a bordered "stamp", the items laid out like the paper slip,
and one ink-blue button.
"""
from __future__ import annotations

import datetime as dt
from html import escape
from urllib.parse import urlencode
from zoneinfo import ZoneInfo

from django.conf import settings
from django.core.mail import EmailMultiAlternatives

BRAND = "LaundryTrack"
ACCENT = "#1d3fa6"
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
# Colours and sizes mirror laundry/static/laundry/app.css.

PAPER, SURFACE, TEXT, TEXT_2, MUTED, LINE = "#f6f5f1", "#ffffff", "#1b1c1e", "#4f5256", "#676a6e", "#e1dfd8"
GOOD, GOOD_SOFT, BAD, BAD_SOFT, ACCENT_SOFT = "#1d6b37", "#e8f3eb", "#b42318", "#fcedeb", "#eaeef9"
FONT = "-apple-system,BlinkMacSystemFont,'Segoe UI',Roboto,'Helvetica Neue',Arial,sans-serif"
LABEL = (f"font-family:{FONT};font-size:11px;font-weight:700;letter-spacing:.08em;"
         f"text-transform:uppercase;color:{MUTED};")
STAMPS = {"good": (GOOD, GOOD_SOFT), "bad": (BAD, BAD_SOFT), "accent": (ACCENT, ACCENT_SOFT), "plain": (TEXT_2, SURFACE)}


def _stamp(text, kind="plain"):
    color, background = STAMPS.get(kind, STAMPS["plain"])
    return (f'<span style="display:inline-block;padding:2px 8px;border:1px solid {color};border-radius:4px;'
            f'background:{background};color:{color};font-family:{FONT};font-size:11px;font-weight:700;'
            f'letter-spacing:.06em;text-transform:uppercase;line-height:1.6;white-space:nowrap;">{escape(text)}</span>')


def _button(href, label):
    return (f'<table role="presentation" cellpadding="0" cellspacing="0" border="0" style="margin:20px 0 0;">'
            f'<tr><td style="border-radius:4px;background:{ACCENT};">'
            f'<a href="{escape(href)}" style="display:inline-block;padding:13px 22px;font-family:{FONT};'
            f'font-size:15px;font-weight:700;color:#ffffff;text-decoration:none;border-radius:4px;">{escape(label)}</a>'
            f'</td></tr></table>')


def _link(href, label):
    return (f'<p style="margin:14px 0 0;font-family:{FONT};font-size:14px;">'
            f'<a href="{escape(href)}" style="color:{ACCENT};font-weight:600;text-decoration:none;">{escape(label)}</a></p>')


def _rows(pairs):
    """Label and value rows, like the summary on the slip page."""
    out = ""
    for label, value in pairs:
        if value in (None, ""):
            continue
        out += (f'<tr><td style="padding:11px 16px 11px 0;border-top:1px solid {LINE};width:118px;'
                f'vertical-align:top;{LABEL}">{escape(label)}</td>'
                f'<td style="padding:9px 0;border-top:1px solid {LINE};font-family:{FONT};font-size:15px;'
                f'color:{TEXT};vertical-align:top;">{value}</td></tr>')
    return (f'<table role="presentation" width="100%" cellpadding="0" cellspacing="0" border="0" '
            f'style="border-collapse:collapse;border-bottom:1px solid {LINE};">{out}</table>')


def _slip_table(headers, rows, footer=None):
    """The items, ruled like the paper slip. A row may be (cells, True) to mark it as short."""
    cell = f"border:1px solid {TEXT};font-family:Arial,Helvetica,sans-serif;"
    th = "".join(
        f'<th align="center" style="{cell}padding:8px 6px;font-size:12px;font-weight:400;'
        f'letter-spacing:.02em;color:{TEXT};">{escape(str(h))}</th>' for h in headers)
    body = ""
    for row in rows:
        cells, short = row if isinstance(row, tuple) else (row, False)
        ink = BAD if short else ACCENT
        background = f"background:{BAD_SOFT};" if short else ""
        tds = ""
        for index, value in enumerate(cells):
            if index == 0:
                tds += (f'<td align="left" style="{cell}{background}padding:9px 10px;font-size:15px;'
                        f'color:{BAD if short else TEXT};">{escape(str(value))}</td>')
            else:
                # Counts are centred like the QTY. column; written text (colours) reads from the left.
                is_count = str(value).strip().isdigit() or str(value).strip() in ("", "-")
                tds += (f'<td align="{"center" if is_count else "left"}" style="{cell}{background}'
                        f'padding:9px {"6px" if is_count else "10px"};font-size:15px;'
                        f'font-weight:600;color:{ink};">{escape(str(value))}</td>')
        body += f"<tr>{tds}</tr>"
    foot = ""
    if footer:
        tds = ""
        for index, value in enumerate(footer):
            if index == 0:
                tds += (f'<td align="right" style="{cell}padding:9px 10px;font-size:14px;color:{TEXT};">'
                        f'{escape(str(value))}</td>')
            else:
                tds += (f'<td align="center" style="{cell}padding:9px 6px;font-size:15px;font-weight:700;'
                        f'color:{ACCENT};">{escape(str(value))}</td>')
        foot = f"<tr>{tds}</tr>"
    return (f'<table role="presentation" width="100%" cellpadding="0" cellspacing="0" border="0" '
            f'style="border-collapse:collapse;border:2px solid {TEXT};margin:18px 0 0;background:{SURFACE};">'
            f'<tr>{th}</tr>{body}{foot}</table>')


def _note(text):
    return f'<p style="margin:16px 0 0;font-family:{FONT};font-size:13px;line-height:1.5;color:{MUTED};">{text}</p>'


def _wrap(heading, inner_html, stamp="", preheader=""):
    return f"""<!doctype html><html><head><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1"></head>
<body style="margin:0;padding:0;background:{PAPER};">
<div style="display:none;max-height:0;overflow:hidden;">{escape(preheader)}</div>
<table role="presentation" width="100%" cellpadding="0" cellspacing="0" border="0" style="background:{PAPER};">
<tr><td align="center" style="padding:24px 12px 32px;">
<table role="presentation" width="100%" cellpadding="0" cellspacing="0" border="0" style="max-width:520px;">
<tr><td style="padding:0 4px 14px;font-family:{FONT};font-size:17px;color:{TEXT};"><span style="font-weight:800;">Laundry</span>Track</td></tr>
<tr><td style="background:{SURFACE};border:1px solid {LINE};border-radius:4px;padding:24px 22px 26px;">
<table role="presentation" width="100%" cellpadding="0" cellspacing="0" border="0"><tr>
<td style="font-family:{FONT};font-size:22px;font-weight:700;line-height:1.25;color:{TEXT};padding:0 0 16px;">{escape(heading)}</td>
<td align="right" style="padding:0 0 16px;vertical-align:top;">{stamp}</td>
</tr></table>
{inner_html}
</td></tr>
<tr><td style="padding:14px 4px 0;font-family:{FONT};font-size:12px;line-height:1.5;color:{MUTED};">
<a href="{_site_url()}" style="color:{MUTED};text-decoration:none;">{BRAND}</a> · a record of your laundry slip
</td></tr>
</table></td></tr></table></body></html>"""


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


def _slip_facts(slip):
    return [("Laundry no.", escape(slip.slip_no or "-")), ("Roll no.", escape(getattr(slip, "enrollment_no", "") or "-"))]


# ------------------------------------------------------------------ public API

def send_drop_confirmation(slip, photo=None):
    """Confirmation email. `photo` is optional JPEG bytes of the slip, attached as proof and never stored."""
    try:
        items = _items(slip)
        n = slip.total_items
        hours = _hours_label()
        subject = (f"Laundry submitted: {_plural(n)} on {_day(slip.drop_date)}, "
                   f"pick up {_day(slip.pickup_date)}")
        url, cal = _slip_url(slip), gcal_link(slip)
        name = _greeting_name(slip)

        text = (f"Hi {name},\n\nYou submitted {_plural(n)} to the laundry on {_long_day(slip.drop_date)}.\n"
                f"Reminder set for {_long_day(slip.pickup_date)}. The counter is open {hours}.\n\n"
                f"{_items_text(items)}\nTotal: {n}\n\n"
                f"Laundry no: {slip.slip_no or '-'}\nRoll no: {getattr(slip, 'enrollment_no', '') or '-'}\n\n"
                f"View slip: {url}\nAdd to Google Calendar: {cal}\n"
                f"A calendar file (laundry-pickup.ics) is attached.\n"
                + ("The photo of your slip is attached too. Keep this email as proof of what you handed in.\n" if photo else ""))
        attached = "Attached: calendar file" + (" and the photo of your slip." if photo else ".")
        inner = (
            _rows(_slip_facts(slip) + [
                ("Given", escape(_day(slip.drop_date))),
                ("Collect on", f"{escape(_day(slip.pickup_date))}"
                               f'<br><span style="font-size:13px;color:{MUTED};">{escape(hours)}</span>'),
            ])
            + _slip_table(["PARTICULARS", "QTY.", "COLOUR"],
                          [[i.name, i.quantity, getattr(i, "colour", "") or ""] for i in items],
                          footer=["TOTAL", n, ""])
            + _button(cal, "Add to Google Calendar")
            + _link(url, "View slip")
            + _note(attached + " Keep this email as proof of what you handed in.")
        )
        html = _wrap(f"{_plural(n).capitalize()} at the laundry", inner, stamp=_stamp("At laundry"),
                     preheader=f"Collect from {_day(slip.pickup_date)}, {hours}")
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
            _rows(_slip_facts(slip) + [
                ("Given", escape(_day(slip.drop_date))),
                ("Counter open", escape(hours)),
            ])
            + _slip_table(["PARTICULARS", "QTY.", "COLOUR"],
                          [[i.name, i.quantity, getattr(i, "colour", "") or ""] for i in items],
                          footer=["TOTAL", n, ""])
            + _button(url, "Count what came back")
            + _note("Count each item at the counter before you leave.")
        )
        html = _wrap(f"Collect {_plural(n)} today", inner, stamp=_stamp("Collect today", "accent"),
                     preheader=f"Counter open {hours}")
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
        facts = _slip_facts(slip) + [("Given", escape(_day(slip.drop_date)))]
        if missing > 0:
            subject = f"Laundry mismatch: {missing} item{'s' if missing != 1 else ''} missing"
            rows = []
            for i in items:
                ret = i.returned_quantity if i.returned_quantity is not None else "-"
                rows.append(([i.name, i.quantity, ret, i.missing or ""], bool(i.missing)))
            text = (f"Hi {name},\n\nYou gave {n} and got back {back}. {missing} missing.\n\n"
                    + "\n".join(f"- {i.name}: given {i.quantity}, returned "
                                f"{i.returned_quantity if i.returned_quantity is not None else '-'}, missing {i.missing}"
                                for i in items)
                    + f"\n\nSlip no: {slip.slip_no or '-'}, given {_long_day(slip.drop_date)}, "
                      f"due {_long_day(slip.pickup_date)}.\nShow this email to the laundry staff. Slip: {url}\n")
            inner = (
                _rows(facts + [("Returned", f"{back} of {n}")])
                + _slip_table(["PARTICULARS", "QTY.", "RET.", "MISS."], rows, footer=["TOTAL", n, back, missing])
                + _button(url, "Open slip")
                + _note("Show this email and the slip photo from your confirmation email at the laundry counter.")
            )
            html = _wrap(f"Given {n}, returned {back}", inner,
                         stamp=_stamp(f"{missing} missing", "bad"), preheader=f"{missing} missing")
        else:
            subject = f"All {_plural(n)} collected"
            text = (f"Hi {name},\n\nAll {_plural(n)} from {_long_day(slip.drop_date)} are back.\n\n"
                    f"{_items_text(items)}\nTotal: {n}\n\nSlip: {url}\n")
            inner = (
                _rows(facts)
                + _slip_table(["PARTICULARS", "QTY.", "RET."],
                              [[i.name, i.quantity, i.returned_quantity if i.returned_quantity is not None else i.quantity]
                               for i in items], footer=["TOTAL", n, back or n])
                + _link(url, "View slip")
            )
            html = _wrap(f"All {_plural(n)} collected", inner, stamp=_stamp("Collected", "good"))
        return _send(slip, subject, text, html)
    except Exception as e:
        return False, f"{type(e).__name__}: {e}"
