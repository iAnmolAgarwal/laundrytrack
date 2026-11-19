// LaundryTrack mail relay. Sends email from YOUR Gmail over HTTPS, for networks that block SMTP.
// Setup: script.google.com > New project > paste this > Deploy > New deployment > Web app
//        Execute as: Me   |   Who has access: Anyone   > Deploy > Authorize > copy the Web app URL.
// Uses MailApp (send-only permission), which Google allows for personal scripts.
const TOKEN = "PASTE_A_LONG_RANDOM_STRING_HERE";

function doPost(e) {
  try {
    const d = JSON.parse(e.postData.contents);
    if (d.token !== TOKEN) return reply({ ok: false, error: "bad token" });
    const opts = { name: d.from_name || "LaundryTrack" };
    if (d.html) opts.htmlBody = d.html;
    if (d.attachments && d.attachments.length) {
      opts.attachments = d.attachments.map(function (a) {
        return Utilities.newBlob(Utilities.base64Decode(a.content), a.mimetype, a.filename);
      });
    }
    MailApp.sendEmail(d.to.join(","), d.subject, d.text || "", opts);
    return reply({ ok: true });
  } catch (err) {
    return reply({ ok: false, error: String(err) });
  }
}

function reply(obj) {
  return ContentService.createTextOutput(JSON.stringify(obj)).setMimeType(ContentService.MimeType.JSON);
}
