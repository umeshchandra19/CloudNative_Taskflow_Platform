import json
import queue
import time

from flask import Blueprint, Response, request

from ..db import get_db, iso, release_request_db
from ..errors import unauthorized
from ..events import HEARTBEAT_SECONDS, format_sse
from ..helpers import ext
from ..security import user_from_token

bp = Blueprint("stream", __name__, url_prefix="/api")
MAX_REPLAY = 200
REFRESH_SECONDS = 30


def _load_projects(conn, user_id, is_admin):
    if is_admin:
        return None
    return {r["project_id"] for r in conn.query("SELECT project_id FROM project_members WHERE user_id = ?", (user_id,))}


def _replay(conn, projects, last_id):
    if projects is not None and not projects:
        return []
    sql = "SELECT id, project_id, type, payload, created_at FROM events WHERE id > ?"
    params = [last_id]
    if projects is not None:
        sql += " AND project_id IN (" + ",".join("?" * len(projects)) + ")"
        params += sorted(projects)
    rows = conn.query(sql + " ORDER BY id LIMIT ?", params + [MAX_REPLAY])
    return [{"id": r["id"], "project_id": r["project_id"], "type": r["type"],
             "payload": json.loads(r["payload"]), "created_at": iso(r["created_at"])} for r in rows]


def sse_events(bus, database, sub, user_id, is_admin, replay):
    """Generator behind the /api/stream response. Yields SSE text frames until the client disconnects."""
    last_sent = 0
    yield "retry: 3000\n\n"
    try:
        for ev in replay:
            last_sent = max(last_sent, ev["id"])
            yield format_sse(ev)
        if len(replay) >= MAX_REPLAY:
            yield "event: resync\ndata: {}\n\n"
        last_refresh = time.monotonic()
        while True:
            try:
                ev = sub.queue.get(timeout=HEARTBEAT_SECONDS)
            except queue.Empty:
                ev = None
                yield ": ping\n\n"  # keeps proxies/load balancers from closing an idle connection
            if time.monotonic() - last_refresh > REFRESH_SECONDS:
                last_refresh = time.monotonic()
                with database.session() as conn:
                    fresh = _load_projects(conn, user_id, is_admin)
                if fresh is not None and sub.projects is not None:
                    sub.projects = set(fresh)
            if sub.overflowed:
                sub.overflowed = False
                yield "event: resync\ndata: {}\n\n"
            if ev is None or ev["id"] <= last_sent:
                continue
            last_sent = ev["id"]
            yield format_sse(ev)
    finally:
        bus.unsubscribe(sub)


@bp.get("/stream")
def stream():
    token = request.args.get("token", "")
    if not token:
        raise unauthorized("missing stream token")
    user = user_from_token(token, kind="stream")
    is_admin = user["role"] == "admin"
    db, bus, database = get_db(), ext("bus"), ext("db")
    projects = _load_projects(db, user["id"], is_admin)
    sub = bus.subscribe(user["id"], projects)  # subscribe BEFORE replaying so nothing is lost in between
    raw_last = request.headers.get("Last-Event-ID") or request.args.get("last_event_id")
    replay = []
    if raw_last and raw_last.isdigit():
        replay = _replay(db, projects, int(raw_last))
    release_request_db()  # a long-lived stream must not hold a database connection
    resp = Response(sse_events(bus, database, sub, user["id"], is_admin, replay), mimetype="text/event-stream")
    resp.headers["Cache-Control"] = "no-cache"
    resp.headers["X-Accel-Buffering"] = "no"
    return resp
