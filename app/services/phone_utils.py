"""
Phone number normalization and formatting utilities.

Consolidates phone formatting from whatsapp_client.py, manual_client.py,
and other files that strip/format Indian phone numbers.
"""

import re


def normalize_phone(phone: str) -> str:
    """
    Strip a phone string down to digits only.

    >>> normalize_phone("+91 98765-43210")
    '919876543210'
    >>> normalize_phone("  098765 43210 ")
    '09876543210'
    """
    if not phone:
        return ""
    return re.sub(r"\D", "", phone.strip())


def format_indian_phone(phone: str) -> str:
    """
    Ensure *phone* is in the ``91XXXXXXXXXX`` format (no ``+`` prefix).

    Strips whitespace, dashes, and the leading ``+``, then prepends ``91``
    if not already present.

    >>> format_indian_phone("+91 98765 43210")
    '919876543210'
    >>> format_indian_phone("9876543210")
    '919876543210'
    """
    phone = phone.strip().replace(" ", "").replace("-", "")
    if phone.startswith("+"):
        phone = phone[1:]
    if not phone.startswith("91"):
        phone = f"91{phone}"
    return phone


def extract_10_digit(phone: str) -> str:
    """
    Extract the last 10 digits from a phone number.

    Useful for database lookups where numbers are stored without country code.

    >>> extract_10_digit("919876543210")
    '9876543210'
    >>> extract_10_digit("9876543210")
    '9876543210'
    """
    digits = re.sub(r"\D", "", phone)
    return digits[-10:] if len(digits) >= 10 else digits
