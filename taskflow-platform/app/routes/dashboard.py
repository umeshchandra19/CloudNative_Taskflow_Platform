from datetime import datetime, timedelta, timezone

from flask import Blueprint, g, jsonify

from .. import rbac
from ..db import get_db, iso
from ..helpers import etag_for, ext, not_modified
from ..security import auth_required
from .tasks import STATUSES, PRIORITIES

bp = Blueprint("dashboard", __name__, url_prefix="/api")


def _today():
    return datetime.now(timezone.utc).date()


def _respond(body, etag):
    resp = jsonify(body)
    resp.headers["ETag"] = etag
    resp.headers["Cache-Control"] = "private, no-cache"
    return resp


@bp.get("/projects/<int:pid>/dashboard")
@auth_required
def project_dashboard(pid):
    db = get_db()
    project, role = rbac.project_access(db, g.user, pid)
    rbac.require(role, "project:read")
    today = _today()
    version = ext("versions").get(pid)
    etag = etag_for("dash", pid, version, today)
    if not_modified(etag):
        return "", 304, {"ETag": etag}
    cache, key = ext("cache"), ("dash", pid, version, today)
    body = cache.get(key)
    if body is None:
        body = _compute_project(db, project, today)
        cache.set(key, body, ttl=60)
    return _respond(body, etag)


def _compute_project(db, project, today):
    pid = project["id"]
    by_status = {s: 0 for s in STATUSES}
    for r in db.query("SELECT status, COUNT(*) AS n FROM tasks WHERE project_id = ? GROUP BY status", (pid,)):
        by_status[r["status"]] = r["n"]
    by_priority = {p: 0 for p in PRIORITIES}
    for r in db.query("SELECT priority, COUNT(*) AS n FROM tasks WHERE project_id = ? AND status != 'done' "
                      "GROUP BY priority", (pid,)):
        by_priority[r["priority"]] = r["n"]
    overdue = db.scalar("SELECT COUNT(*) FROM tasks WHERE project_id = ? AND status != 'done' "
                        "AND due_date IS NOT NULL AND due_date < ?", (pid, today.isoformat()))
    workload = db.query("SELECT a.id, a.name, COUNT(*) AS open_tasks FROM tasks t JOIN users a ON a.id = t.assignee_id "
                        "WHERE t.project_id = ? AND t.status != 'done' GROUP BY a.id, a.name "
                        "ORDER BY open_tasks DESC, a.name LIMIT 10", (pid,))
    unassigned = db.scalar("SELECT COUNT(*) FROM tasks WHERE project_id = ? AND status != 'done' "
                           "AND assignee_id IS NULL", (pid,))
    days = [(today - timedelta(days=i)).isoformat() for i in range(6, -1, -1)]
    since = datetime.combine(today - timedelta(days=6), datetime.min.time(), tzinfo=timezone.utc)
    done_counts = {d: 0 for d in days}
    for r in db.query("SELECT completed_at FROM tasks WHERE project_id = ? AND completed_at >= ?",
                      (pid, since.strftime("%Y-%m-%dT%H:%M:%S.%f+00:00"))):
        day = iso(r["completed_at"])[:10]
        if day in done_counts:
            done_counts[day] += 1
    total = sum(by_status.values())
    return {"project_id": pid, "total": total, "open": total - by_status["done"], "done": by_status["done"],
            "overdue": overdue, "unassigned": unassigned, "by_status": by_status, "by_priority": by_priority,
            "workload": workload, "completed_last_7_days": [{"date": d, "count": c} for d, c in done_counts.items()]}


@bp.get("/dashboard")
@auth_required
def my_dashboard():
    """Cross-project summary of the tasks assigned to the current user."""
    db, user = get_db(), g.user
    today = _today()
    project_ids = [r["project_id"] for r in db.query("SELECT project_id FROM project_members WHERE user_id = ?", (user["id"],))]
    versions = ext("versions")
    stamp = sum(versions.get(p) for p in project_ids)
    etag = etag_for("my", user["id"], stamp, today)
    if not_modified(etag):
        return "", 304, {"ETag": etag}
    cache, key = ext("cache"), ("my", user["id"], stamp, today)
    body = cache.get(key)
    if body is None:
        by_status = {s: 0 for s in STATUSES}
        for r in db.query("SELECT status, COUNT(*) AS n FROM tasks WHERE assignee_id = ? GROUP BY status", (user["id"],)):
            by_status[r["status"]] = r["n"]
        overdue = db.scalar("SELECT COUNT(*) FROM tasks WHERE assignee_id = ? AND status != 'done' "
                            "AND due_date IS NOT NULL AND due_date < ?", (user["id"], today.isoformat()))
        soon = db.query("SELECT t.id, t.title, t.due_date, t.priority, p.id AS project_id, p.name AS project_name "
                        "FROM tasks t JOIN projects p ON p.id = t.project_id "
                        "WHERE t.assignee_id = ? AND t.status != 'done' AND t.due_date IS NOT NULL "
                        "AND t.due_date >= ? AND t.due_date <= ? ORDER BY t.due_date, t.id LIMIT 10",
                        (user["id"], today.isoformat(), (today + timedelta(days=7)).isoformat()))
        for r in soon:
            r["due_date"] = iso(r["due_date"])
        body = {"projects": len(project_ids), "assigned_by_status": by_status, "overdue": overdue, "due_soon": soon}
        cache.set(key, body, ttl=60)
    return _respond(body, etag)
