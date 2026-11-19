"""Shared helpers for route modules."""
import hashlib

from flask import current_app, request

from .db import iso


def ext(name):
    return current_app.extensions[name]


def finish_write(db, project_ids):
    """Commit, then let the event bus bump cache versions and wake the poller."""
    db.commit()
    ext("bus").after_commit(project_ids)


def etag_for(*parts):
    digest = hashlib.sha1("|".join(str(p) for p in parts).encode()).hexdigest()[:20]
    return f'W/"{digest}"'


def not_modified(etag):
    """True when the client already has this version (it sent a matching If-None-Match)."""
    return request.headers.get("If-None-Match") == etag


def user_json(row):
    return {"id": row["id"], "email": row["email"], "name": row["name"], "role": row["role"]}


def task_json(row):
    return {"id": row["id"], "project_id": row["project_id"], "title": row["title"],
            "description": row["description"], "status": row["status"], "priority": row["priority"],
            "assignee_id": row["assignee_id"], "assignee_name": row.get("assignee_name"),
            "creator_id": row["creator_id"], "due_date": iso(row["due_date"]),
            "created_at": iso(row["created_at"]), "updated_at": iso(row["updated_at"])}
