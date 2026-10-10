"""Recognise anti-bot gates (JS challenges, proof-of-work, captcha walls). Rubrika never solves them;
it stops, pauses the source group and tells the user."""
import re

GATE_COOLDOWN = 3600
# "challenge-platform" alone also appears in the harmless jsd/main.js beacon of ordinary Cloudflare pages.
MARKERS = (b"__uzdbm", b"verifying your browser", b"/human/challenge", b"challenge-platform/h/", b"/orchestrate/")
_FORM = re.compile(rb"<form[^>]*action=[\"'][^\"']*(?:human|challenge|verify)[^\"']*[\"']", re.I)
_POW_INPUT = re.compile(rb"<input[^>]*name=[\"'](?:nonce|pow|difficulty|challenge)[\"']", re.I)
_CAPTCHA_CLASS = re.compile(rb"class=[\"'][^\"']*\b(?:g-recaptcha|h-captcha|cf-turnstile)\b", re.I)
_CAPTCHA_TAG = re.compile(rb"<[a-z][^>]*>", re.I)
_CF_CHL = re.compile(rb"cf[-_]chl[-_]", re.I)
SMALL_PAGE = 20000


def is_bot_gate(body: bytes, location: str | None = None, content_type: str = "") -> bool:
    if location and re.search(r"/(?:human/)?challenge\b", location):
        return True
    head = body[:65536]
    low = head.lower()
    if any(m in low for m in MARKERS):
        return True
    if _CF_CHL.search(head) and b"just a moment" in low:
        return True
    if _FORM.search(head) and _POW_INPUT.search(head):
        return True
    # A captcha widget is a wall only on a small page; a big page merely offering a captcha-protected action is not.
    return len(body) < SMALL_PAGE and any(b"data-sitekey" in t and _CAPTCHA_CLASS.search(t)
                                          for t in _CAPTCHA_TAG.findall(head))
