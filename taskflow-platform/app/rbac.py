"""Role-based access control.

Global roles:   admin (everything, everywhere) | member
Project roles:  owner > manager > member > viewer
"""
from .errors import forbidden, not_found

PROJECT_ROLES = ("viewer", "member", "manager", "owner")
ASSIGNABLE_ROLES = ("viewer", "member", "manager")  # 'owner' is set only when a project is created

_VIEWER = {"project:read"}
_MEMBER = _VIEWER | {"task:create", "task:update_own"}
_MANAGER = _MEMBER | {"task:update_any", "task:assign", "task:delete"}
_OWNER = _MANAGER | {"project:update", "project:delete", "members:manage"}
PERMISSIONS = {"viewer": _VIEWER, "member": _MEMBER, "manager": _MANAGER, "owner": _OWNER}


def can(role, permission):
    return permission in PERMISSIONS.get(role, ())


def require(role, permission):
    if not can(role, permission):
        raise forbidden(f"your project role '{role}' cannot do '{permission}'")


def project_access(db, user, project_id):
    """Returns (project_row, effective_role). Non-members get 404 so project ids are not leaked."""
    project = db.one("SELECT id, name, description, owner_id, created_at FROM projects WHERE id = ?", (project_id,))
    if not project:
        raise not_found("project not found")
    if user["role"] == "admin":
        return project, "owner"
    member = db.one("SELECT role FROM project_members WHERE project_id = ? AND user_id = ?", (project_id, user["id"]))
    if not member:
        raise not_found("project not found")
    return project, member["role"]


def require_admin(user):
    if user["role"] != "admin":
        raise forbidden("admin only")
