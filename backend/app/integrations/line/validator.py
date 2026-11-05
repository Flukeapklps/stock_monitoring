"""Local validation of LINE message objects against the Messaging API schema.

Covers the subset of the spec this platform generates (text + Flex bubble/carousel
with box/text/span/button/separator/filler/icon/image). Messages failing
validation are never sent. When credentials are configured the admin "test"
endpoint additionally calls LINE's /v2/bot/message/validate/push.
"""

from __future__ import annotations

import json
import re

MAX_MESSAGES_PER_REQUEST = 5
MAX_TEXT_LENGTH = 5000
MAX_ALT_TEXT = 1500
MAX_BUBBLE_BYTES = 30_000
MAX_CAROUSEL_BYTES = 50_000
MAX_CAROUSEL_BUBBLES = 12

COLOR_RE = re.compile(r"^#[0-9a-fA-F]{6}([0-9a-fA-F]{2})?$")
BUBBLE_SIZES = {"nano", "micro", "deca", "hecto", "kilo", "mega", "giga"}
LAYOUTS = {"horizontal", "vertical", "baseline"}
TEXT_SIZES = {"xxs", "xs", "sm", "md", "lg", "xl", "xxl", "3xl", "4xl", "5xl"}
SPACINGS = {"none", "xs", "sm", "md", "lg", "xl", "xxl"}
WEIGHTS = {"regular", "bold"}
BUTTON_STYLES = {"primary", "secondary", "link"}
ALIGN = {"start", "end", "center"}
GRAVITY = {"top", "bottom", "center"}
URI_SCHEMES = ("http://", "https://", "line://", "tel:")
COMPONENTS_IN_BOX = {"box", "button", "image", "icon", "text", "separator", "filler", "video"}


class FlexValidationError(ValueError):
    def __init__(self, errors: list[str]):
        super().__init__("; ".join(errors[:10]))
        self.errors = errors


def _size_ok(v) -> bool:
    return isinstance(v, str) and (v in TEXT_SIZES or v in {"full"} or re.match(r"^\d+(\.\d+)?(px|%)$", v) is not None)


def _check_color(obj: dict, key: str, path: str, errors: list[str]) -> None:
    if key in obj and not (isinstance(obj[key], str) and COLOR_RE.match(obj[key])):
        errors.append(f"{path}.{key}: invalid color {obj[key]!r}")


def _check_action(a: dict, path: str, errors: list[str], max_label: int = 40) -> None:
    if not isinstance(a, dict):
        errors.append(f"{path}: action must be an object")
        return
    t = a.get("type")
    label = a.get("label")
    if label is not None and (not isinstance(label, str) or len(label) > max_label):
        errors.append(f"{path}.label: max {max_label} characters")
    if t == "uri":
        uri = a.get("uri", "")
        if not isinstance(uri, str) or not uri.startswith(URI_SCHEMES) or len(uri) > 1000:
            errors.append(f"{path}.uri: must start with http(s)://, line:// or tel: and be <= 1000 chars")
    elif t == "postback":
        if not isinstance(a.get("data"), str) or not 1 <= len(a["data"]) <= 300:
            errors.append(f"{path}.data: 1-300 characters required")
    elif t == "message":
        if not isinstance(a.get("text"), str) or not 1 <= len(a["text"]) <= 300:
            errors.append(f"{path}.text: 1-300 characters required")
    else:
        errors.append(f"{path}.type: unsupported action type {t!r}")


def _check_component(c: dict, path: str, errors: list[str]) -> None:
    if not isinstance(c, dict):
        errors.append(f"{path}: component must be an object")
        return
    t = c.get("type")
    if t == "box":
        if c.get("layout") not in LAYOUTS:
            errors.append(f"{path}.layout: must be one of {sorted(LAYOUTS)}")
        contents = c.get("contents")
        if not isinstance(contents, list):
            errors.append(f"{path}.contents: array required")
            contents = []
        for i, child in enumerate(contents):
            ct = child.get("type") if isinstance(child, dict) else None
            if ct not in COMPONENTS_IN_BOX:
                errors.append(f"{path}.contents[{i}]: {ct!r} not allowed in box")
                continue
            if c.get("layout") == "baseline" and ct not in {"icon", "text", "filler"}:
                errors.append(f"{path}.contents[{i}]: baseline boxes may only contain icon/text/filler")
            _check_component(child, f"{path}.contents[{i}]", errors)
        for k in ("spacing", "margin"):
            if k in c and c[k] not in SPACINGS and not re.match(r"^\d+px$", str(c[k])):
                errors.append(f"{path}.{k}: invalid value {c[k]!r}")
        for k in ("backgroundColor", "borderColor"):
            _check_color(c, k, path, errors)
        if "action" in c:
            _check_action(c["action"], f"{path}.action", errors)
    elif t == "text":
        has_spans = isinstance(c.get("contents"), list) and len(c["contents"]) > 0
        if not has_spans and (not isinstance(c.get("text"), str) or c["text"] == ""):
            errors.append(f"{path}.text: non-empty text required")
        if "size" in c and not _size_ok(c["size"]):
            errors.append(f"{path}.size: invalid size {c['size']!r}")
        if "weight" in c and c["weight"] not in WEIGHTS:
            errors.append(f"{path}.weight: invalid")
        if "align" in c and c["align"] not in ALIGN:
            errors.append(f"{path}.align: invalid")
        if "gravity" in c and c["gravity"] not in GRAVITY:
            errors.append(f"{path}.gravity: invalid")
        _check_color(c, "color", path, errors)
        for i, sp in enumerate(c.get("contents") or []):
            if not isinstance(sp, dict) or sp.get("type") != "span" or not isinstance(sp.get("text"), str):
                errors.append(f"{path}.contents[{i}]: span with text required")
            else:
                _check_color(sp, "color", f"{path}.contents[{i}]", errors)
        if "action" in c:
            _check_action(c["action"], f"{path}.action", errors)
    elif t == "button":
        if "action" not in c:
            errors.append(f"{path}.action: required")
        else:
            _check_action(c["action"], f"{path}.action", errors)
        if "style" in c and c["style"] not in BUTTON_STYLES:
            errors.append(f"{path}.style: invalid")
        _check_color(c, "color", path, errors)
        if c.get("height") not in (None, "sm", "md"):
            errors.append(f"{path}.height: must be sm or md")
    elif t == "separator":
        _check_color(c, "color", path, errors)
    elif t == "filler":
        pass
    elif t in ("image", "icon"):
        url = c.get("url", "")
        if not isinstance(url, str) or not url.startswith("https://") or len(url) > 2000:
            errors.append(f"{path}.url: HTTPS URL required")
    else:
        errors.append(f"{path}.type: unsupported component {t!r}")


def _check_bubble(b: dict, path: str, errors: list[str]) -> None:
    if not isinstance(b, dict) or b.get("type") != "bubble":
        errors.append(f"{path}: bubble required")
        return
    if "size" in b and b["size"] not in BUBBLE_SIZES:
        errors.append(f"{path}.size: invalid bubble size")
    if not any(k in b for k in ("header", "hero", "body", "footer")):
        errors.append(f"{path}: bubble needs at least one block")
    for block in ("header", "body", "footer"):
        if block in b:
            if not isinstance(b[block], dict) or b[block].get("type") != "box":
                errors.append(f"{path}.{block}: must be a box")
            else:
                _check_component(b[block], f"{path}.{block}", errors)
    if "hero" in b:
        _check_component(b["hero"], f"{path}.hero", errors)
    for block, style in (b.get("styles") or {}).items():
        if block not in ("header", "hero", "body", "footer"):
            errors.append(f"{path}.styles.{block}: unknown block")
        elif isinstance(style, dict):
            _check_color(style, "backgroundColor", f"{path}.styles.{block}", errors)
            _check_color(style, "separatorColor", f"{path}.styles.{block}", errors)
    size = len(json.dumps(b, ensure_ascii=False).encode("utf-8"))
    if size > MAX_BUBBLE_BYTES:
        errors.append(f"{path}: bubble is {size} bytes (max {MAX_BUBBLE_BYTES})")


def validate_message(m: dict, path: str = "message") -> list[str]:
    errors: list[str] = []
    t = m.get("type") if isinstance(m, dict) else None
    if t == "text":
        txt = m.get("text")
        if not isinstance(txt, str) or not txt:
            errors.append(f"{path}.text: required")
        elif len(txt) > MAX_TEXT_LENGTH:
            errors.append(f"{path}.text: max {MAX_TEXT_LENGTH} characters")
    elif t == "flex":
        alt = m.get("altText")
        if not isinstance(alt, str) or not alt or len(alt) > MAX_ALT_TEXT:
            errors.append(f"{path}.altText: 1-{MAX_ALT_TEXT} characters required")
        contents = m.get("contents") or {}
        ct = contents.get("type")
        if ct == "bubble":
            _check_bubble(contents, f"{path}.contents", errors)
        elif ct == "carousel":
            bubbles = contents.get("contents") or []
            if not 1 <= len(bubbles) <= MAX_CAROUSEL_BUBBLES:
                errors.append(f"{path}.contents: carousel needs 1-{MAX_CAROUSEL_BUBBLES} bubbles")
            for i, b in enumerate(bubbles):
                _check_bubble(b, f"{path}.contents.contents[{i}]", errors)
            size = len(json.dumps(contents, ensure_ascii=False).encode("utf-8"))
            if size > MAX_CAROUSEL_BYTES:
                errors.append(f"{path}.contents: carousel is {size} bytes (max {MAX_CAROUSEL_BYTES})")
        else:
            errors.append(f"{path}.contents.type: bubble or carousel required")
    else:
        errors.append(f"{path}.type: unsupported message type {t!r}")
    if isinstance(m, dict) and "quickReply" in m:
        items = (m["quickReply"] or {}).get("items") or []
        if not 1 <= len(items) <= 13:
            errors.append(f"{path}.quickReply: 1-13 items")
        for i, it in enumerate(items):
            _check_action(it.get("action", {}), f"{path}.quickReply.items[{i}].action", errors, max_label=20)
    return errors


def validate_messages(messages: list[dict]) -> list[str]:
    if not isinstance(messages, list) or not 1 <= len(messages) <= MAX_MESSAGES_PER_REQUEST:
        return [f"messages: 1-{MAX_MESSAGES_PER_REQUEST} messages required"]
    errors: list[str] = []
    for i, m in enumerate(messages):
        errors += validate_message(m, f"messages[{i}]")
    return errors


def ensure_valid(messages: list[dict]) -> None:
    errors = validate_messages(messages)
    if errors:
        raise FlexValidationError(errors)
