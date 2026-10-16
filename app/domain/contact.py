"""Contact details people type into forms: names, phone numbers and LinkedIn profile links."""

import re
import unicodedata

import phonenumbers

MAX_NAME_LENGTH = 100
_LINKEDIN_RE = re.compile(
    r"^(?:https?://)?(?:[a-z]{2,3}\.)?linkedin\.com/in/([A-Za-z0-9\-_%]{3,100})/?(?:[?#].*)?$", re.I
)


class ContactRejectedError(ValueError):
    """The value is not usable. The message is safe to show the user."""


def normalize_person_name(text: str) -> str:
    name = re.sub(r"\s+", " ", unicodedata.normalize("NFKC", text)).strip()
    if not 1 <= len(name) <= MAX_NAME_LENGTH:
        raise ContactRejectedError(f"A name should be 1 to {MAX_NAME_LENGTH} characters.")
    # Letters from any script, with the joiners real names use ("O'Brien", "Anne-Marie", "Jr.").
    if not all(ch.isalpha() or ch in " .'-" for ch in name) or not any(ch.isalpha() for ch in name):
        raise ContactRejectedError(
            "A name can only contain letters, spaces, apostrophes, dots and hyphens."
        )
    return name


def normalize_phone(text: str, default_region: str) -> str:
    """E.164 form (+923001234567). Numbers without a country code are read in `default_region`."""
    try:
        number = phonenumbers.parse(text, default_region)
    except phonenumbers.NumberParseException as exc:
        raise ContactRejectedError("That doesn't look like a phone number.") from exc
    if not phonenumbers.is_valid_number(number):
        raise ContactRejectedError(
            "That phone number isn't valid. Include the country code, e.g. +92 300 1234567."
        )
    return phonenumbers.format_number(number, phonenumbers.PhoneNumberFormat.E164)


def normalize_linkedin(text: str) -> str:
    """Canonical https://www.linkedin.com/in/<handle> URL."""
    match = _LINKEDIN_RE.match(text.strip())
    if not match:
        raise ContactRejectedError(
            "Use your LinkedIn profile link, like linkedin.com/in/your-name."
        )
    return f"https://www.linkedin.com/in/{match.group(1)}"
