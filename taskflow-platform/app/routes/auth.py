from flask import Blueprint, current_app, g, jsonify

from ..db import get_db, now_iso
from ..errors import ApiError, bad_request, conflict, unauthorized
from ..helpers import user_json
from ..security import auth_required, hash_password, make_token, verify_password
from ..validation import get_email, get_str, json_body

bp = Blueprint("auth", __name__, url_prefix="/api/auth")
_DUMMY_HASH = None


def _cfg():
    return current_app.config["CFG"]


def _token_response(user):
    cfg = _cfg()
    token = make_token(cfg.jwt_secret, user["id"], user["role"], seconds=cfg.jwt_expiry_minutes * 60)
    return {"token": token, "expires_in": cfg.jwt_expiry_minutes * 60, "user": user_json(user)}


@bp.post("/register")
def register():
    data = json_body()
    email, name = get_email(data), get_str(data, "name", max_len=100)
    password = get_str(data, "password", min_len=8, max_len=128)
    db = get_db()
    if db.one("SELECT 1 AS x FROM users WHERE email = ?", (email,)):
        raise conflict("email already registered")
    try:
        uid = db.insert("INSERT INTO users (email, name, password_hash, role, created_at) VALUES (?, ?, ?, 'member', ?)",
                        (email, name, hash_password(password, n=_cfg().scrypt_n), now_iso()))
        db.commit()
    except db.IntegrityError:
        raise conflict("email already registered")
    user = {"id": uid, "email": email, "name": name, "role": "member"}
    return jsonify(_token_response(user)), 201


@bp.post("/login")
def login():
    global _DUMMY_HASH
    data = json_body()
    email, password = get_email(data), get_str(data, "password", max_len=128)
    user = get_db().one("SELECT id, email, name, role, password_hash FROM users WHERE email = ?", (email,))
    if _DUMMY_HASH is None:
        _DUMMY_HASH = hash_password("dummy-password", n=_cfg().scrypt_n)
    # always do one hash verification so response time does not reveal whether the email exists
    ok = verify_password(password, user["password_hash"] if user else _DUMMY_HASH)
    if not user or not ok:
        raise unauthorized("invalid email or password")
    return jsonify(_token_response(user))


@bp.get("/me")
@auth_required
def me():
    return jsonify(user_json(g.user))


@bp.post("/stream-token")
@auth_required
def stream_token():
    """Short-lived token for EventSource (browsers cannot send an Authorization header on it)."""
    cfg = _cfg()
    token = make_token(cfg.jwt_secret, g.user["id"], g.user["role"], seconds=cfg.stream_token_seconds, kind="stream")
    return jsonify({"token": token, "expires_in": cfg.stream_token_seconds})
