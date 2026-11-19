from flask import Blueprint, g, jsonify, request

from .. import rbac
from ..db import get_db, iso
from ..errors import bad_request, not_found
from ..helpers import ext
from ..security import auth_required
from ..validation import get_enum, json_body, page_response, paging

bp = Blueprint("admin", __name__, url_prefix="/api")


@bp.get("/users")
@auth_required
def list_users():
    rbac.require_admin(g.user)
    db = get_db()
    page, size = paging(request.args)
    total = db.scalar("SELECT COUNT(*) FROM users")
    rows = db.query("SELECT id, email, name, role, created_at FROM users ORDER BY id LIMIT ? OFFSET ?",
                    (size, (page - 1) * size))
    for r in rows:
        r["created_at"] = iso(r["created_at"])
    return jsonify(page_response(rows, total, page, size))


@bp.put("/users/<int:uid>/role")
@auth_required
def set_user_role(uid):
    rbac.require_admin(g.user)
    role = get_enum(json_body(), "role", ("admin", "member"), required=True)
    db = get_db()
    if uid == g.user["id"] and role != "admin":
        raise bad_request("you cannot remove your own admin role")
    if not db.one("SELECT 1 AS x FROM users WHERE id = ?", (uid,)):
        raise not_found("user not found")
    db.execute("UPDATE users SET role = ? WHERE id = ?", (role, uid))
    db.commit()
    return jsonify({"id": uid, "role": role})


@bp.get("/admin/stats")
@auth_required
def stats():
    rbac.require_admin(g.user)
    db = get_db()
    return jsonify({
        "database": ext("db").dialect,
        "cache": ext("cache").stats(),
        "sse_subscribers": ext("bus").subscriber_count,
        "counts": {"users": db.scalar("SELECT COUNT(*) FROM users"),
                   "projects": db.scalar("SELECT COUNT(*) FROM projects"),
                   "tasks": db.scalar("SELECT COUNT(*) FROM tasks")},
    })
