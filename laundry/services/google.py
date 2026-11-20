"""Sign in with Google, limited to institute accounts.

The standard OAuth 2.0 authorization-code flow, written against the standard
library so the app needs no extra dependency:

1. `auth_url` sends the browser to Google's consent screen.
2. Google sends it back with a one-time code, which `exchange_code` swaps for an
   ID token and checks.
3. `user_for_claims` finds or creates the matching account.
"""
import base64
import binascii
import hmac
import json
import re
import time
import urllib.error
import urllib.parse
import urllib.request

from django.conf import settings
from django.contrib.auth import get_user_model

from ..models import Profile

AUTH_ENDPOINT = "https://accounts.google.com/o/oauth2/v2/auth"
TOKEN_ENDPOINT = "https://oauth2.googleapis.com/token"
ISSUERS = ("https://accounts.google.com", "accounts.google.com")
ROLL_RE = re.compile(r"^[0-9]{6,15}$")


class GoogleAuthError(Exception):
    """Sign-in could not be completed. The message is safe to show to the user."""


def is_configured():
    return bool(settings.GOOGLE_CLIENT_ID and settings.GOOGLE_CLIENT_SECRET)


def wrong_account_message():
    return f"Use your JIIT account, the one ending in @{settings.STUDENT_EMAIL_DOMAIN}."


def auth_url(redirect_uri, state, nonce):
    query = {
        "client_id": settings.GOOGLE_CLIENT_ID,
        "redirect_uri": redirect_uri,
        "response_type": "code",
        "scope": "openid email profile",
        "state": state,
        "nonce": nonce,
        "hd": settings.STUDENT_EMAIL_DOMAIN,   # only a hint to Google; the domain is enforced in check_claims
        "prompt": "select_account",
    }
    return f"{AUTH_ENDPOINT}?{urllib.parse.urlencode(query)}"


def _decode_id_token(token):
    try:
        payload = token.split(".")[1]
        payload += "=" * (-len(payload) % 4)
        claims = json.loads(base64.urlsafe_b64decode(payload))
    except (AttributeError, IndexError, ValueError, binascii.Error):
        raise GoogleAuthError("Google sign-in failed. Try again.") from None
    if not isinstance(claims, dict):
        raise GoogleAuthError("Google sign-in failed. Try again.")
    return claims


def check_claims(claims, nonce):
    """Accept only a fresh token, issued to this app, for a verified institute account."""
    fresh = (
        claims.get("iss") in ISSUERS
        and claims.get("aud") == settings.GOOGLE_CLIENT_ID
        and float(claims.get("exp") or 0) > time.time()
        and hmac.compare_digest(str(claims.get("nonce") or ""), nonce)
    )
    if not fresh:
        raise GoogleAuthError("Google sign-in failed. Try again.")
    email = str(claims.get("email") or "").strip().lower()
    # `hd` is present only on accounts managed by an organisation, so a personal
    # Google account registered with a look-alike address is turned away.
    institute = (
        email.endswith("@" + settings.STUDENT_EMAIL_DOMAIN)
        and claims.get("email_verified") in (True, "true")
        and bool(claims.get("hd"))
    )
    if not institute:
        raise GoogleAuthError(wrong_account_message())
    return {**claims, "email": email}


def exchange_code(code, redirect_uri, nonce):
    """Swap the one-time code for the signed-in person's verified claims."""
    body = urllib.parse.urlencode({
        "code": code,
        "client_id": settings.GOOGLE_CLIENT_ID,
        "client_secret": settings.GOOGLE_CLIENT_SECRET,
        "redirect_uri": redirect_uri,
        "grant_type": "authorization_code",
    }).encode()
    request = urllib.request.Request(TOKEN_ENDPOINT, data=body, method="POST")
    try:
        with urllib.request.urlopen(request, timeout=10) as response:
            tokens = json.load(response)
    except (urllib.error.URLError, TimeoutError, ValueError):
        raise GoogleAuthError("Could not reach Google. Try again.") from None
    # The token comes straight from Google over HTTPS, so its signature does not
    # need to be checked again here; its claims still are.
    return check_claims(_decode_id_token(tokens.get("id_token")), nonce)


def roll_from_email(email):
    """Student mailboxes are <roll number>@domain; anything else has no roll number."""
    local = email.split("@")[0]
    return local if ROLL_RE.match(local) else ""


def name_from_email(email):
    """Staff mailboxes are usually name based: "asha.verma2@..." gives "Asha Verma"."""
    words = re.findall(r"[a-z]+", email.split("@")[0].lower())
    return " ".join(word.capitalize() for word in words)


def user_for_claims(claims):
    """Find the account for a verified institute email, creating it on first sign-in.

    Returns (user, created). An account made before Google sign-in is picked up
    by its email address, so its slips stay with it.
    """
    User = get_user_model()
    email = claims["email"]
    user = User.objects.filter(username__iexact=email).first()
    if user is None:
        earlier = list(User.objects.filter(email__iexact=email)[:2])
        if len(earlier) == 1:
            user = earlier[0]
    if user is not None:
        return user, False

    name = " ".join(str(claims.get("name") or "").split()) or name_from_email(email)
    user = User(username=email, email=email, first_name=name[:60])
    user.set_unusable_password()
    user.save()
    Profile.objects.create(user=user, enrollment_no=roll_from_email(email))
    return user, True
