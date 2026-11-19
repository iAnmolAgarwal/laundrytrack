"""Email backend that sends through a Google Apps Script web app over HTTPS.

Campus networks often block the SMTP ports (25, 465, 587). This backend posts
each message as JSON to a small Apps Script (see mail_relay.gs) which sends it
from the owner's Gmail account, so only port 443 is needed.
"""
import base64
import json
import urllib.request

from django.core.mail.backends.base import BaseEmailBackend


class AppsScriptEmailBackend(BaseEmailBackend):
    def __init__(self, url=None, token="", timeout=30, **kwargs):
        super().__init__(**kwargs)
        self.url = url
        self.token = token
        self.timeout = timeout

    def _payload(self, message):
        html = ""
        for alternative in getattr(message, "alternatives", []) or []:
            content, mimetype = alternative[0], alternative[1]
            if mimetype == "text/html":
                html = content
        attachments = []
        for attachment in message.attachments or []:
            if not isinstance(attachment, (tuple, list)) and not hasattr(attachment, "filename"):
                continue  # pre-built MIME parts are not supported by the relay
            filename, content, mimetype = attachment[0], attachment[1], attachment[2]
            if isinstance(content, str):
                content = content.encode("utf-8")
            attachments.append({
                "filename": filename,
                "mimetype": (mimetype or "application/octet-stream").split(";")[0],
                "content": base64.b64encode(content).decode("ascii"),
            })
        return {
            "token": self.token,
            "to": list(message.to) + list(message.cc) + list(message.bcc),
            "subject": message.subject,
            "text": message.body,
            "html": html,
            "from_name": "LaundryTrack",
            "attachments": attachments,
        }

    def _post(self, payload):
        request = urllib.request.Request(
            self.url,
            data=json.dumps(payload).encode("utf-8"),
            headers={"Content-Type": "application/json"},
            method="POST",
        )
        # Apps Script answers the POST with a redirect to the result; urllib follows it.
        with urllib.request.urlopen(request, timeout=self.timeout) as response:
            body = response.read().decode("utf-8", "replace")
        try:
            result = json.loads(body)
        except ValueError:
            raise RuntimeError(f"mail relay returned a non-JSON reply: {body[:200]}")
        if not result.get("ok"):
            raise RuntimeError(f"mail relay refused the message: {result.get('error', 'unknown error')}")

    def send_messages(self, email_messages):
        sent = 0
        for message in email_messages or []:
            if not message.recipients():
                continue
            self._post(self._payload(message))
            sent += 1
        return sent
