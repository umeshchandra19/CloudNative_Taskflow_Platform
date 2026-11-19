from flask import Blueprint, g, jsonify, request

from .. import rbac
from ..db import get_db, iso, now_iso
from ..errors import bad_request, conflict, not_found
from ..events import publish
from ..helpers import finish_write
from ..security import auth_required
from ..validation import get_enum, get_email, get_str, json_body, page_response, paging

bp = Blueprint("projects", __name__, url_prefix="/api/projects")


def _project_json(row, role):
    return {"id": row["id"], "name": row["name"], "description": row["description"], "owner_id": row["owner_id"],
            "my_role": role, "created_at": iso(row["created_at"]), "open_tasks": row.get("open_tasks")}


@bp.get("")
@auth_required
def list_projects():
    db, user = get_db(), g.user
    page, size = paging(request.args)
    open_sql = "(SELECT COUNT(*) FROM tasks t WHERE t.project_id = p.id AND t.status != 'done') AS open_tasks"
    if user["role"] == "admin":
        total = db.scalar("SELECT COUNT(*) FROM projects")
        rows = db.query(f"SELECT p.*, 'owner' AS my_role, {open_sql} FROM projects p ORDER BY p.id DESC LIMIT ? OFFSET ?",
                        (size, (page - 1) * size))
    else:
        total = db.scalar("SELECT COUNT(*) FROM project_members WHERE user_id = ?", (user["id"],))
        rows = db.query(f"SELECT p.*, m.role AS my_role, {open_sql} FROM projects p "
                        "JOIN project_members m ON m.project_id = p.id AND m.user_id = ? "
                        "ORDER BY p.id DESC LIMIT ? OFFSET ?", (user["id"], size, (page - 1) * size))
    return jsonify(page_response([_project_json(r, r["my_role"]) for r in rows], total, page, size))


@bp.post("")
@auth_required
def create_project():
    data, db, user = json_body(), get_db(), g.user
    name = get_str(data, "name", max_len=120)
    description = get_str(data, "description", required=False, min_len=0, max_len=2000, default="")
    pid = db.insert("INSERT INTO projects (name, description, owner_id, created_at) VALUES (?, ?, ?, ?)",
                    (name, description, user["id"], now_iso()))
    db.execute("INSERT INTO project_members (project_id, user_id, role) VALUES (?, ?, 'owner')", (pid, user["id"]))
    publish(db, pid, "project.created", {"name": name, "actor_id": user["id"]})
    finish_write(db, [pid])
    row = db.one("SELECT * FROM projects WHERE id = ?", (pid,))
    return jsonify(_project_json(row, "owner")), 201


@bp.get("/<int:pid>")
@auth_required
def get_project(pid):
    project, role = rbac.project_access(get_db(), g.user, pid)
    return jsonify(_project_json(project, role))


@bp.patch("/<int:pid>")
@auth_required
def update_project(pid):
    db = get_db()
    project, role = rbac.project_access(db, g.user, pid)
    rbac.require(role, "project:update")
    data = json_body()
    name = get_str(data, "name", required=False, max_len=120, default=project["name"])
    description = get_str(data, "description", required=False, min_len=0, max_len=2000, default=project["description"])
    db.execute("UPDATE projects SET name = ?, description = ? WHERE id = ?", (name, description, pid))
    publish(db, pid, "project.updated", {"name": name, "actor_id": g.user["id"]})
    finish_write(db, [pid])
    return jsonify(_project_json(db.one("SELECT * FROM projects WHERE id = ?", (pid,)), role))


@bp.delete("/<int:pid>")
@auth_required
def delete_project(pid):
    db = get_db()
    _, role = rbac.project_access(db, g.user, pid)
    rbac.require(role, "project:delete")
    db.execute("DELETE FROM projects WHERE id = ?", (pid,))  # tasks and memberships cascade
    publish(db, pid, "project.deleted", {"actor_id": g.user["id"]})
    finish_write(db, [pid])
    return "", 204


# ---------- members ----------

@bp.get("/<int:pid>/members")
@auth_required
def list_members(pid):
    db = get_db()
    _, role = rbac.project_access(db, g.user, pid)
    rbac.require(role, "project:read")
    rows = db.query("SELECT u.id, u.email, u.name, m.role FROM project_members m JOIN users u ON u.id = m.user_id "
                    "WHERE m.project_id = ? ORDER BY u.name", (pid,))
    return jsonify({"items": rows})


@bp.post("/<int:pid>/members")
@auth_required
def add_member(pid):
    db = get_db()
    _, role = rbac.project_access(db, g.user, pid)
    rbac.require(role, "members:manage")
    data = json_body()
    email = get_email(data)
    new_role = get_enum(data, "role", rbac.ASSIGNABLE_ROLES, default="member")
    user = db.one("SELECT id, email, name FROM users WHERE email = ?", (email,))
    if not user:
        raise not_found("no user with that email")
    if db.one("SELECT 1 AS x FROM project_members WHERE project_id = ? AND user_id = ?", (pid, user["id"])):
        raise conflict("user is already a member")
    db.execute("INSERT INTO project_members (project_id, user_id, role) VALUES (?, ?, ?)", (pid, user["id"], new_role))
    publish(db, pid, "member.added", {"user_id": user["id"], "name": user["name"], "role": new_role, "actor_id": g.user["id"]})
    finish_write(db, [pid])
    return jsonify({"id": user["id"], "email": user["email"], "name": user["name"], "role": new_role}), 201


def _member_target(db, pid, uid):
    project = db.one("SELECT owner_id FROM projects WHERE id = ?", (pid,))
    member = db.one("SELECT role FROM project_members WHERE project_id = ? AND user_id = ?", (pid, uid))
    if not member:
        raise not_found("member not found")
    if project["owner_id"] == uid:
        raise bad_request("the project owner cannot be changed or removed")
    return member


@bp.put("/<int:pid>/members/<int:uid>")
@auth_required
def change_member_role(pid, uid):
    db = get_db()
    _, role = rbac.project_access(db, g.user, pid)
    rbac.require(role, "members:manage")
    _member_target(db, pid, uid)
    new_role = get_enum(json_body(), "role", rbac.ASSIGNABLE_ROLES, required=True)
    db.execute("UPDATE project_members SET role = ? WHERE project_id = ? AND user_id = ?", (new_role, pid, uid))
    publish(db, pid, "member.role_changed", {"user_id": uid, "role": new_role, "actor_id": g.user["id"]})
    finish_write(db, [pid])
    return jsonify({"id": uid, "role": new_role})


@bp.delete("/<int:pid>/members/<int:uid>")
@auth_required
def remove_member(pid, uid):
    db = get_db()
    _, role = rbac.project_access(db, g.user, pid)
    rbac.require(role, "members:manage")
    _member_target(db, pid, uid)
    db.execute("UPDATE tasks SET assignee_id = NULL WHERE project_id = ? AND assignee_id = ?", (pid, uid))
    db.execute("DELETE FROM project_members WHERE project_id = ? AND user_id = ?", (pid, uid))
    publish(db, pid, "member.removed", {"user_id": uid, "actor_id": g.user["id"]})
    finish_write(db, [pid])
    return "", 204
