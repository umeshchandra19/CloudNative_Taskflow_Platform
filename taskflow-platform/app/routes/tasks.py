from datetime import datetime, timezone
from urllib.parse import urlencode

from flask import Blueprint, g, jsonify, request

from .. import rbac
from ..db import get_db, now_iso
from ..errors import bad_request, forbidden, not_found
from ..events import publish
from ..helpers import etag_for, ext, finish_write, not_modified, task_json
from ..security import auth_required
from ..validation import (get_date, get_enum, get_optional_int, get_str, json_body, page_response, paging)

bp = Blueprint("tasks", __name__, url_prefix="/api")

STATUSES = ("todo", "in_progress", "review", "done")
PRIORITIES = ("low", "medium", "high", "urgent")
SORT_COLUMNS = {"id": "t.id", "created_at": "t.created_at", "updated_at": "t.updated_at",
                "title": "LOWER(t.title)", "due_date": "t.due_date"}
TASK_SELECT = ("SELECT t.id, t.project_id, t.title, t.description, t.status, t.priority, t.assignee_id, "
               "a.name AS assignee_name, t.creator_id, t.due_date, t.created_at, t.updated_at "
               "FROM tasks t LEFT JOIN users a ON a.id = t.assignee_id")


def _today():
    return datetime.now(timezone.utc).date().isoformat()


def _load_task(db, tid):
    row = db.one(TASK_SELECT + " WHERE t.id = ?", (tid,))
    if not row:
        raise not_found("task not found")
    return row


def _check_assignee(db, project_id, assignee_id, role, user):
    if assignee_id is None:
        return
    if assignee_id != user["id"]:
        rbac.require(role, "task:assign")  # members may only assign to themselves
    if not db.one("SELECT 1 AS x FROM project_members WHERE project_id = ? AND user_id = ?", (project_id, assignee_id)):
        raise bad_request("assignee must be a member of the project")


def _event_payload(task_row, previous_status=None, fields=None):
    return {"task_id": task_row["id"], "title": task_row["title"], "status": task_row["status"],
            "previous_status": previous_status, "fields": fields or [],
            "actor_id": g.user["id"], "actor_name": g.user["name"]}


# ---------- list (paginated, filtered, cached) ----------

@bp.get("/projects/<int:pid>/tasks")
@auth_required
def list_tasks(pid):
    db, user = get_db(), g.user
    _, role = rbac.project_access(db, user, pid)
    rbac.require(role, "project:read")

    query = urlencode(sorted(request.args.items(multi=True)))
    version = ext("versions").get(pid)
    etag = etag_for("tasks", pid, version, _today(), query, user["id"] if request.args.get("assignee") == "me" else "")
    if not_modified(etag):
        return "", 304, {"ETag": etag}

    cache = ext("cache")
    key = ("tasks", pid, version, _today(), query, user["id"] if request.args.get("assignee") == "me" else 0)
    body = cache.get(key)
    if body is None:
        body = _query_tasks(db, pid, user)
        cache.set(key, body)
    resp = jsonify(body)
    resp.headers["ETag"] = etag
    resp.headers["Cache-Control"] = "private, no-cache"  # client may keep it but must revalidate (cheap 304)
    return resp


def _query_tasks(db, pid, user):
    args = request.args
    page, size = paging(args)
    where, params = ["t.project_id = ?"], [pid]

    status = args.get("status")
    if status:
        if status not in STATUSES:
            raise bad_request("invalid status")
        where.append("t.status = ?"); params.append(status)
    priority = args.get("priority")
    if priority:
        if priority not in PRIORITIES:
            raise bad_request("invalid priority")
        where.append("t.priority = ?"); params.append(priority)
    assignee = args.get("assignee")
    if assignee == "none":
        where.append("t.assignee_id IS NULL")
    elif assignee:
        try:
            aid = user["id"] if assignee == "me" else int(assignee)
        except ValueError:
            raise bad_request("assignee must be an id, 'me' or 'none'")
        where.append("t.assignee_id = ?"); params.append(aid)
    q = (args.get("q") or "").strip().lower()[:100]
    if q:
        like = "%" + q.replace("\\", "\\\\").replace("%", "\\%").replace("_", "\\_") + "%"
        where.append("(LOWER(t.title) LIKE ? ESCAPE '\\' OR LOWER(t.description) LIKE ? ESCAPE '\\')")
        params += [like, like]
    if args.get("overdue") in ("1", "true"):
        where.append("t.status != 'done' AND t.due_date IS NOT NULL AND t.due_date < ?")
        params.append(_today())

    sort = args.get("sort", "-id")
    column = SORT_COLUMNS.get(sort.lstrip("-"))
    if column is None:
        raise bad_request("sort must be one of: " + ", ".join(SORT_COLUMNS) + " (prefix with - for descending)")
    direction = "DESC" if sort.startswith("-") else "ASC"
    order = f"{column} {direction}, t.id DESC"
    if column == "t.due_date":  # tasks without a due date always last, on both databases
        order = f"(t.due_date IS NULL), {order}"

    where_sql = " AND ".join(where)
    total = db.scalar(f"SELECT COUNT(*) FROM tasks t WHERE {where_sql}", params)
    rows = db.query(f"{TASK_SELECT} WHERE {where_sql} ORDER BY {order} LIMIT ? OFFSET ?",
                    params + [size, (page - 1) * size])
    return page_response([task_json(r) for r in rows], total, page, size)


# ---------- create / read / update / delete ----------

@bp.post("/projects/<int:pid>/tasks")
@auth_required
def create_task(pid):
    db, user = get_db(), g.user
    _, role = rbac.project_access(db, user, pid)
    rbac.require(role, "task:create")
    data = json_body()
    title = get_str(data, "title", max_len=200)
    description = get_str(data, "description", required=False, min_len=0, max_len=5000, default="")
    status = get_enum(data, "status", STATUSES, default="todo")
    priority = get_enum(data, "priority", PRIORITIES, default="medium")
    due = get_date(data, "due_date")
    assignee_id = get_optional_int(data, "assignee_id")
    _check_assignee(db, pid, assignee_id, role, user)

    now = now_iso()
    tid = db.insert("INSERT INTO tasks (project_id, title, description, status, priority, assignee_id, creator_id, "
                    "due_date, created_at, updated_at, completed_at) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
                    (pid, title, description, status, priority, assignee_id, user["id"], due, now, now,
                     now if status == "done" else None))
    task = _load_task(db, tid)
    publish(db, pid, "task.created", _event_payload(task))
    finish_write(db, [pid])
    return jsonify(task_json(task)), 201


@bp.get("/tasks/<int:tid>")
@auth_required
def get_task(tid):
    db = get_db()
    task = _load_task(db, tid)
    rbac.project_access(db, g.user, task["project_id"])
    return jsonify(task_json(task))


@bp.patch("/tasks/<int:tid>")
@auth_required
def update_task(tid):
    db, user = get_db(), g.user
    task = _load_task(db, tid)
    _, role = rbac.project_access(db, user, task["project_id"])
    own = user["id"] in (task["assignee_id"], task["creator_id"])
    if not (rbac.can(role, "task:update_any") or (rbac.can(role, "task:update_own") and own)):
        raise forbidden("you can only edit tasks you created or are assigned to")

    data = json_body()
    changes = {}
    if "title" in data:
        changes["title"] = get_str(data, "title", max_len=200)
    if "description" in data:
        changes["description"] = get_str(data, "description", min_len=0, max_len=5000)
    if "status" in data:
        changes["status"] = get_enum(data, "status", STATUSES, required=True)
    if "priority" in data:
        changes["priority"] = get_enum(data, "priority", PRIORITIES, required=True)
    if "due_date" in data:
        changes["due_date"] = get_date(data, "due_date")
    if "assignee_id" in data:
        new_assignee = get_optional_int(data, "assignee_id")
        if new_assignee != task["assignee_id"]:
            _check_assignee(db, task["project_id"], new_assignee, role, user)
            if new_assignee is None:
                rbac.require(role, "task:assign")
            changes["assignee_id"] = new_assignee
    if not changes:
        raise bad_request("no updatable fields given")

    now = now_iso()
    sets = [f"{col} = ?" for col in changes]  # column names come from the fixed set above, never from the client
    params = list(changes.values())
    sets.append("updated_at = ?"); params.append(now)
    if "status" in changes and changes["status"] != task["status"]:
        sets.append("completed_at = ?"); params.append(now if changes["status"] == "done" else None)
    params.append(tid)
    db.execute(f"UPDATE tasks SET {', '.join(sets)} WHERE id = ?", params)

    updated = _load_task(db, tid)
    publish(db, task["project_id"], "task.updated", _event_payload(updated, task["status"], list(changes)))
    finish_write(db, [task["project_id"]])
    return jsonify(task_json(updated))


@bp.delete("/tasks/<int:tid>")
@auth_required
def delete_task(tid):
    db = get_db()
    task = _load_task(db, tid)
    _, role = rbac.project_access(db, g.user, task["project_id"])
    rbac.require(role, "task:delete")
    db.execute("DELETE FROM tasks WHERE id = ?", (tid,))
    publish(db, task["project_id"], "task.deleted", _event_payload(task))
    finish_write(db, [task["project_id"]])
    return "", 204
