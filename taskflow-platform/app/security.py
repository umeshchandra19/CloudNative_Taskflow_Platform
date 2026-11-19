"""Password hashing (scrypt), JWT handling and the auth decorator."""
import base64
import hashlib
import hmac
import os
from datetime import datetime, timedelta, timezone
from functools import wraps

import jwt
from flask import current_app, g, request

from .db import get_db
from .errors import unauthorized


def _b64(b):
    return base64.b64encode(b).decode()


def hash_password(password, n=2 ** 14, r=8, p=1):
    salt = os.urandom(16)
    dk = hashlib.scrypt(password.encode(), salt=salt, n=n, r=r, p=p, dklen=32, maxmem=64 * 1024 * 1024)
    return f"scrypt${n}${r}${p}${_b64(salt)}${_b64(dk)}"


def verify_password(password, stored):
    try:
        scheme, n, r, p, salt, expected = stored.split("$")
        if scheme != "scrypt":
            return False
        dk = hashlib.scrypt(password.encode(), salt=base64.b64decode(salt), n=int(n), r=int(r), p=int(p),
                            dklen=32, maxmem=64 * 1024 * 1024)
        return hmac.compare_digest(dk, base64.b64decode(expected))
    except (ValueError, TypeError):
        return False


def make_token(secret, user_id, role, *, seconds, kind="access"):
    now = datetime.now(timezone.utc)
    payload = {"sub": str(user_id), "role": role, "typ": kind, "iat": now, "exp": now + timedelta(seconds=seconds)}
    return jwt.encode(payload, secret, algorithm="HS256")


def read_token(secret, token, kind="access"):
    """Raises jwt.PyJWTError if invalid/expired or if it is the wrong kind of token."""
    data = jwt.decode(token, secret, algorithms=["HS256"], options={"require": ["exp", "sub"]})
    if data.get("typ") != kind:
        raise jwt.InvalidTokenError("wrong token type")
    return data


def user_from_token(token, kind="access"):
    """Loads the user row for a token. The role is read from the DB, so role changes apply immediately."""
    cfg = current_app.config["CFG"]
    try:
        data = read_token(cfg.jwt_secret, token, kind)
        user_id = int(data["sub"])
    except (jwt.PyJWTError, ValueError):
        raise unauthorized("invalid or expired token")
    user = get_db().one("SELECT id, email, name, role FROM users WHERE id = ?", (user_id,))
    if not user:
        raise unauthorized("user no longer exists")
    return user


def auth_required(fn):
    @wraps(fn)
    def wrapper(*args, **kwargs):
        header = request.headers.get("Authorization", "")
        if not header.startswith("Bearer "):
            raise unauthorized("missing bearer token")
        g.user = user_from_token(header[7:].strip())
        return fn(*args, **kwargs)
    return wrapper
