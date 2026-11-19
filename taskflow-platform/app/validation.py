"""Small hand-written request validation helpers (keeps dependencies minimal)."""
import re
from datetime import date

from flask import request

from .errors import bad_request

EMAIL_RE = re.compile(r"^[^@\s]+@[^@\s]+\.[^@\s]+$")
DATE_RE = re.compile(r"^\d{4}-\d{2}-\d{2}$")
MAX_PAGE_SIZE = 100


def json_body():
    data = request.get_json(silent=True)
    if not isinstance(data, dict):
        raise bad_request("request body must be a JSON object")
    return data


def get_str(data, key, *, required=True, min_len=1, max_len=255, default=None):
    if key not in data or data[key] is None:
        if required:
            raise bad_request(f"{key} is required")
        return default
    value = data[key]
    if not isinstance(value, str):
        raise bad_request(f"{key} must be a string")
    value = value.strip()
    if len(value) < min_len or len(value) > max_len:
        raise bad_request(f"{key} must be {min_len}-{max_len} characters")
    return value


def get_email(data, key="email"):
    value = get_str(data, key, max_len=255).lower()
    if not EMAIL_RE.match(value):
        raise bad_request("email is not valid")
    return value


def get_enum(data, key, allowed, *, required=False, default=None):
    if key not in data or data[key] is None:
        if required:
            raise bad_request(f"{key} is required")
        return default
    if data[key] not in allowed:
        raise bad_request(f"{key} must be one of: {', '.join(sorted(allowed))}")
    return data[key]


def get_date(data, key):
    """Returns ISO date string, or None if the key is absent/null. Raises on bad format."""
    value = data.get(key)
    if value is None or value == "":
        return None
    if not isinstance(value, str) or not DATE_RE.match(value):
        raise bad_request(f"{key} must be YYYY-MM-DD")
    try:
        date.fromisoformat(value)
    except ValueError:
        raise bad_request(f"{key} is not a real date")
    return value


def get_optional_int(data, key):
    value = data.get(key)
    if value is None:
        return None
    if isinstance(value, bool) or not isinstance(value, int):
        raise bad_request(f"{key} must be an integer")
    return value


def paging(args):
    try:
        page = max(int(args.get("page", 1)), 1)
        size = min(max(int(args.get("page_size", 20)), 1), MAX_PAGE_SIZE)
    except ValueError:
        raise bad_request("page and page_size must be integers")
    return page, size


def page_response(items, total, page, size):
    return {"items": items, "page": page, "page_size": size, "total": total,
            "total_pages": (total + size - 1) // size if total else 0}
