"""Read a photographed laundry slip into structured data.

extract_slip(image_path) -> dict, never raises. No Django imports so it can be
run/tested standalone.

Engines, in order:
  A. Anthropic API (if ANTHROPIC_API_KEY is set) with JSON-schema structured output.
  B. Locally installed, logged-in Claude Code CLI in headless print mode.
  C. Give up: ok=False, engine "none", empty fields (UI falls back to manual entry).
"""
from __future__ import annotations

import base64
import datetime as _dt
import json
import os
import re
import shutil
import subprocess
import tempfile

PROMPT = """You are reading a photo of a hostel laundry slip from an Indian college (JIIT). The student fills it by hand and gives it to the laundry man.

The usual printed layout is:
  Title "LAUNDRY SLIP"
  Name ______   Laundry No. ______
  Roll No. ______   Date ______
  A table with columns PARTICULARS | QTY. | COLOUR and these printed rows, in order:
    Shirt, T-Shirt, Pant, Jeans Pant, Lower, Inner UPR+LOWER, Bed Sheet+P. Cover, Towel, Shorts, (a blank row),
    "(Dry Clean Only)" section: Coat/Tie, Sweater H/S, Sweater F/S, Jacket+S/Shirt, Blanket,
    then a TOTAL row.
The printed text is fixed; only the handwriting (usually pen) carries the data. The slip may be rotated, skewed, shadowed,
partly covered, or a second copy of the slip may be partly visible at the edge: read only the main, complete slip.
If the layout is different from the above, still extract the same fields from whatever is there.

Extract:
- student_name: the handwritten name ("" if blank).
- slip_no: the handwritten "Laundry No." ("" if blank).
- enrollment_no: the handwritten "Roll No." / enrollment number, digits and letters exactly as written, no spaces ("" if blank). Read each digit carefully.
- room_no: a room number only if one is written somewhere ("" otherwise).
- date: the handwritten date as YYYY-MM-DD ("" if blank or unreadable). Indian slips are day first: DD/MM/YY or DD/MM/YYYY.
- items: one entry for every row that has a handwritten quantity greater than zero.
  name = for a printed row, use EXACTLY the printed label from the list above (e.g. "Jeans Pant", "Bed Sheet+P. Cover", "Inner UPR+LOWER");
         for something handwritten in the blank row, the item name in Title Case English.
  quantity = the handwritten integer count in the QTY. column.
  colour = whatever is handwritten in the COLOUR column for that row, as written (e.g. "Blue, White"), else "".
  Skip rows whose QTY. is blank, crossed out, "-", "0" or "nil". Do not invent items. Never include the TOTAL row or the "(Dry Clean Only)" heading as an item.
- total: the handwritten number in the TOTAL row (integer), or null if blank.
- notes: anything else useful (an illegible entry, a total that does not match the rows, your uncertainty about a digit), else "".
"""

SCHEMA = {
    "type": "object",
    "properties": {
        "slip_no": {"type": "string"},
        "student_name": {"type": "string"},
        "enrollment_no": {"type": "string"},
        "room_no": {"type": "string"},
        "date": {"type": "string"},
        "items": {
            "type": "array",
            "items": {
                "type": "object",
                "properties": {
                    "name": {"type": "string"},
                    "quantity": {"type": "integer"},
                    "colour": {"type": "string"},
                },
                "required": ["name", "quantity", "colour"],
                "additionalProperties": False,
            },
        },
        "total": {"type": ["integer", "null"]},
        "notes": {"type": "string"},
    },
    "required": ["slip_no", "student_name", "enrollment_no", "room_no", "date", "items", "total", "notes"],
    "additionalProperties": False,
}

MAX_SIDE = 1800
CLI_TIMEOUT = 120


def _empty(engine="none", error=""):
    return {
        "ok": False, "engine": engine, "error": error,
        "slip_no": "", "student_name": "", "enrollment_no": "", "room_no": "", "date": "",
        "items": [], "total": None, "notes": "",
    }


# ---------------------------------------------------------------- image prep

def _prepare_image(image_path: str):
    """Return (path_to_send, media_type, temp_files_to_delete)."""
    temps = []
    src = image_path
    try:
        from PIL import Image, ImageOps
    except Exception:
        return image_path, _guess_media_type(image_path), temps
    try:  # iPhone HEIC photos, when pillow-heif is installed
        import pillow_heif
        pillow_heif.register_heif_opener()
    except Exception:
        pass

    img = None
    try:
        img = Image.open(src)
        img.load()
    except Exception:
        img = None
        # HEIC etc: try macOS sips
        sips = shutil.which("sips")
        if sips:
            try:
                fd, conv = tempfile.mkstemp(suffix=".jpg", prefix="slip_conv_")
                os.close(fd)
                temps.append(conv)
                subprocess.run([sips, "-s", "format", "jpeg", src, "--out", conv],
                               capture_output=True, timeout=60, check=True)
                img = Image.open(conv)
                img.load()
            except Exception:
                img = None
    if img is None:
        return image_path, _guess_media_type(image_path), temps

    try:
        img = ImageOps.exif_transpose(img)
        if img.mode not in ("RGB", "L"):
            img = img.convert("RGB")
        w, h = img.size
        scale = min(1.0, MAX_SIDE / float(max(w, h)))
        if scale < 1.0:
            img = img.resize((max(1, int(w * scale)), max(1, int(h * scale))), Image.LANCZOS)
        fd, out = tempfile.mkstemp(suffix=".jpg", prefix="slip_",
                                   dir=os.path.dirname(os.path.abspath(image_path)) or None)
        os.close(fd)
        temps.append(out)
        img.save(out, "JPEG", quality=88)
        return out, "image/jpeg", temps
    except Exception:
        try:
            fd, out = tempfile.mkstemp(suffix=".jpg", prefix="slip_")
            os.close(fd)
            temps.append(out)
            img.convert("RGB").save(out, "JPEG", quality=88)
            return out, "image/jpeg", temps
        except Exception:
            return image_path, _guess_media_type(image_path), temps


def _guess_media_type(path):
    ext = os.path.splitext(path)[1].lower()
    return {".png": "image/png", ".gif": "image/gif", ".webp": "image/webp"}.get(ext, "image/jpeg")


# ---------------------------------------------------------------- parsing

def _first_json_object(text: str):
    if not text:
        return None
    text = text.strip()
    try:
        obj = json.loads(text)
        if isinstance(obj, dict):
            return obj
    except Exception:
        pass
    start = text.find("{")
    while start != -1:
        depth, in_str, esc = 0, False, False
        for i in range(start, len(text)):
            c = text[i]
            if in_str:
                if esc:
                    esc = False
                elif c == "\\":
                    esc = True
                elif c == '"':
                    in_str = False
            elif c == '"':
                in_str = True
            elif c == "{":
                depth += 1
            elif c == "}":
                depth -= 1
                if depth == 0:
                    try:
                        obj = json.loads(text[start:i + 1])
                        if isinstance(obj, dict):
                            return obj
                    except Exception:
                        break
        start = text.find("{", start + 1)
    return None


def _to_int(v):
    if v is None or isinstance(v, bool):
        return None
    if isinstance(v, int):
        return v
    if isinstance(v, float):
        return int(round(v))
    m = re.search(r"-?\d+", str(v))
    return int(m.group()) if m else None


def _norm_date(v) -> str:
    s = str(v or "").strip()
    if not s:
        return ""
    for fmt in ("%Y-%m-%d", "%d/%m/%Y", "%d/%m/%y", "%d-%m-%Y", "%d-%m-%y",
                "%d.%m.%Y", "%d.%m.%y", "%d %b %Y", "%d %B %Y", "%Y/%m/%d"):
        try:
            return _dt.datetime.strptime(s, fmt).date().isoformat()
        except ValueError:
            continue
    return ""


def _norm_name(n: str) -> str:
    n = re.sub(r"\s+", " ", str(n or "")).strip(" .:-*")
    if not n:
        return ""
    words = []
    for w in n.split(" "):
        words.append("-".join(p[:1].upper() + p[1:].lower() for p in w.split("-")))
    n = " ".join(words)
    # crude singularisation for common plurals
    keep = {"Jeans", "Shorts", "Pyjamas", "Trousers", "Pants"}
    if n == "Pants":
        return "Pant"
    if n not in keep and len(n) > 3 and n.endswith("s") and not n.endswith("ss"):
        n = n[:-1]
    return n


def _coerce(raw: dict, engine: str) -> dict:
    out = _empty(engine)
    if not isinstance(raw, dict):
        out["error"] = "model returned no JSON object"
        return out
    out["slip_no"] = str(raw.get("slip_no") or "").strip()
    out["student_name"] = str(raw.get("student_name") or "").strip()
    out["room_no"] = str(raw.get("room_no") or "").strip()
    out["enrollment_no"] = "".join(str(raw.get("enrollment_no") or "").split())
    out["date"] = _norm_date(raw.get("date"))
    out["notes"] = str(raw.get("notes") or "").strip()
    out["total"] = _to_int(raw.get("total"))
    merged = {}
    for it in raw.get("items") or []:
        if not isinstance(it, dict):
            continue
        name = " ".join(str(it.get("name") or "").split())
        qty = _to_int(it.get("quantity"))
        if not name or not qty or qty <= 0:
            continue
        colour = " ".join(str(it.get("colour") or "").split())
        entry = merged.setdefault(name, {"name": name, "quantity": 0, "colour": ""})
        entry["quantity"] += qty
        if colour and colour not in entry["colour"]:
            entry["colour"] = f"{entry['colour']}, {colour}".strip(", ")
    out["items"] = list(merged.values())
    out["ok"] = True
    return out


# ---------------------------------------------------------------- engines

def _via_api(path: str, media_type: str) -> dict:
    import anthropic

    with open(path, "rb") as fh:
        data = base64.standard_b64encode(fh.read()).decode()
    client = anthropic.Anthropic()
    resp = client.messages.create(
        model=os.environ.get("LAUNDRY_VISION_MODEL", "claude-haiku-5-5"),
        max_tokens=4000,
        messages=[{
            "role": "user",
            "content": [
                {"type": "image", "source": {"type": "base64", "media_type": media_type, "data": data}},
                {"type": "text", "text": PROMPT},
            ],
        }],
        output_config={"format": {"type": "json_schema", "schema": SCHEMA}},
    )
    if resp.stop_reason not in ("end_turn", "stop_sequence"):
        raise RuntimeError(f"API stop_reason={resp.stop_reason}")
    text = next((b.text for b in resp.content if getattr(b, "type", "") == "text"), "")
    return json.loads(text)


def _claude_bin():
    p = shutil.which("claude")
    if p:
        return p
    p = os.path.expanduser("~/.local/bin/claude")
    return p if os.path.exists(p) else None


def cli_command(abs_image: str) -> list:
    exe = _claude_bin()
    prompt = (
        f"Use the Read tool to open the image file at this absolute path: {abs_image}\n\n"
        + PROMPT
        + "\nReply with ONLY a single JSON object (no prose, no code fences) with exactly these keys: "
        '{"slip_no": str, "student_name": str, "enrollment_no": str, "room_no": str, "date": "YYYY-MM-DD" or "", '
        '"items": [{"name": str, "quantity": int, "colour": str}], "total": int or null, "notes": str}'
    )
    return [
        exe, "-p", prompt,
        "--output-format", "json",
        "--model", os.environ.get("LAUNDRY_CLI_MODEL", "sonnet"),
        "--safe-mode",                 # no plugins/hooks/MCP/skills/CLAUDE.md; auth kept
        "--strict-mcp-config",         # no MCP servers at all
        "--tools", "Read",             # only the Read tool exists
        "--allowedTools", "Read",      # ...and it runs without a prompt
        "--no-session-persistence",
        "--add-dir", os.path.dirname(abs_image),
    ]


def _via_cli(path: str) -> dict:
    exe = _claude_bin()
    if not exe:
        raise RuntimeError("claude CLI not found")
    abs_image = os.path.abspath(path)
    env = {k: v for k, v in os.environ.items() if not k.startswith("CLAUDE")}
    proc = subprocess.run(
        cli_command(abs_image), capture_output=True, text=True, timeout=CLI_TIMEOUT,
        env=env, cwd=os.path.dirname(abs_image), stdin=subprocess.DEVNULL,
    )
    out = (proc.stdout or "").strip()
    envelope = _first_json_object(out)
    if envelope is None:
        raise RuntimeError(f"CLI exit {proc.returncode}: {(proc.stderr or out)[:300]}")
    if "result" in envelope or envelope.get("type") == "result":
        if envelope.get("is_error"):
            raise RuntimeError(f"CLI error: {str(envelope.get('result'))[:300]}")
        if isinstance(envelope.get("structured_output"), dict):
            return envelope["structured_output"]
        obj = _first_json_object(str(envelope.get("result") or ""))
        if obj is None:
            raise RuntimeError(f"CLI reply had no JSON: {str(envelope.get('result'))[:300]}")
        return obj
    return envelope


def extract_slip(image_path: str) -> dict:
    temps = []
    errors = []
    try:
        if not image_path or not os.path.exists(image_path):
            return _empty(error=f"file not found: {image_path}")
        try:
            send_path, media_type, temps = _prepare_image(image_path)
        except Exception as e:  # pragma: no cover
            send_path, media_type = image_path, _guess_media_type(image_path)
            errors.append(f"prep: {e}")

        if os.environ.get("ANTHROPIC_API_KEY"):
            try:
                return _coerce(_via_api(send_path, media_type), "anthropic-api")
            except Exception as e:
                errors.append(f"anthropic-api: {type(e).__name__}: {e}"[:400])

        try:
            return _coerce(_via_cli(send_path), "claude-cli")
        except subprocess.TimeoutExpired:
            errors.append(f"claude-cli: timed out after {CLI_TIMEOUT}s")
        except Exception as e:
            errors.append(f"claude-cli: {type(e).__name__}: {e}"[:400])

        return _empty(error="; ".join(errors) or "no extraction engine available")
    except Exception as e:  # never raise
        return _empty(error=f"unexpected: {type(e).__name__}: {e}")
    finally:
        for t in temps:
            try:
                os.remove(t)
            except Exception:
                pass
