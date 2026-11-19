"""Real-time updates.

Every write inserts a row into the `events` table in the SAME transaction as the change. Each web process runs one
background poller that reads new rows and (a) bumps cache versions, (b) pushes them to its connected SSE clients.
Because the database is the shared channel, this works with several gunicorn workers or several ECS tasks and needs
no extra service (Redis, message broker). Latency: ~instant for clients on the writing process, up to the poll
interval (default 0.5 s) for clients on other processes.
"""
import json
import logging
import queue
import threading
from datetime import datetime, timedelta, timezone

from .db import iso, now_iso

log = logging.getLogger("taskflow.events")
HEARTBEAT_SECONDS = 15
RECENT_WINDOW_SECONDS = 5  # re-read recent rows so a transaction that commits late (lower id) is not missed


def publish(db, project_id, event_type, payload):
    """Insert an event inside the caller's transaction. Call EventBus.after_commit() after db.commit()."""
    return db.insert("INSERT INTO events (project_id, type, payload, created_at) VALUES (?, ?, ?, ?)",
                     (project_id, event_type, json.dumps(payload), now_iso()))


def format_sse(event):
    data = json.dumps({"id": event["id"], "project_id": event["project_id"], "type": event["type"],
                       "payload": event["payload"], "created_at": event["created_at"]})
    return f"id: {event['id']}\nevent: {event['type']}\ndata: {data}\n\n"


class Subscriber:
    def __init__(self, user_id, projects):
        self.user_id = user_id
        self.projects = projects  # None = every project (admin), else a set of project ids
        self.queue = queue.Queue(maxsize=200)
        self.overflowed = False

    def wants(self, event):
        return self.projects is None or event["project_id"] in self.projects


class EventBus:
    def __init__(self, database, versions, interval=0.5, retention_hours=24):
        self.database, self.versions = database, versions
        self.interval, self.retention = interval, timedelta(hours=retention_hours)
        self._subs, self._subs_lock = set(), threading.Lock()
        self._wake, self._stop = threading.Event(), threading.Event()
        self._thread = None
        self._last_id, self._seen = 0, set()
        self._poll_lock = threading.Lock()
        self._last_purge = datetime.now(timezone.utc)

    # ---- called from request handlers ----
    def after_commit(self, project_ids):
        for pid in set(project_ids):
            self.versions.bump(pid)  # read-your-writes on this process
        self._wake.set()             # wake the poller now instead of waiting for the next tick

    def subscribe(self, user_id, projects):
        sub = Subscriber(user_id, projects)
        with self._subs_lock:
            self._subs.add(sub)
        return sub

    def unsubscribe(self, sub):
        with self._subs_lock:
            self._subs.discard(sub)

    @property
    def subscriber_count(self):
        return len(self._subs)

    # ---- poller ----
    def prime(self):
        """Start from 'now': events that already exist are history, not news (also for the recent-rows window)."""
        cutoff = (datetime.now(timezone.utc) - timedelta(seconds=RECENT_WINDOW_SECONDS)).strftime("%Y-%m-%dT%H:%M:%S.%f+00:00")
        with self.database.session() as conn:
            self._last_id = conn.scalar("SELECT COALESCE(MAX(id), 0) FROM events") or 0
            self._seen = {r["id"] for r in conn.query("SELECT id FROM events WHERE created_at > ?", (cutoff,))}

    def start(self):
        if self._thread and self._thread.is_alive():
            return
        self.prime()
        self._stop.clear()
        self._thread = threading.Thread(target=self._run, name="event-poller", daemon=True)
        self._thread.start()

    def stop(self):
        self._stop.set()
        self._wake.set()
        if self._thread:
            self._thread.join(timeout=3)

    def _run(self):
        while not self._stop.is_set():
            self._wake.wait(self.interval)
            self._wake.clear()
            if self._stop.is_set():
                break
            try:
                self.poll_once()
            except Exception:  # keep the poller alive through transient DB errors
                log.exception("event poll failed")
                self._stop.wait(2)

    def poll_once(self):
        with self._poll_lock:
            return self._poll_once()

    def _poll_once(self):
        cutoff = (datetime.now(timezone.utc) - timedelta(seconds=RECENT_WINDOW_SECONDS)).strftime("%Y-%m-%dT%H:%M:%S.%f+00:00")
        with self.database.session() as conn:
            rows = conn.query("SELECT id, project_id, type, payload, created_at FROM events "
                              "WHERE id > ? OR created_at > ? ORDER BY id LIMIT 1000", (self._last_id, cutoff))
            self._maybe_purge(conn)
        new = []
        for r in rows:
            if r["id"] in self._seen:
                continue
            self._seen.add(r["id"])
            new.append({"id": r["id"], "project_id": r["project_id"], "type": r["type"],
                        "payload": json.loads(r["payload"]), "created_at": iso(r["created_at"])})
        if rows:
            self._last_id = max(self._last_id, rows[-1]["id"])
            self._seen = {i for i in self._seen if i > self._last_id - 5000}
        for ev in new:
            self.versions.bump(ev["project_id"])
            self._dispatch(ev)
        return len(new)

    def _dispatch(self, ev):
        with self._subs_lock:
            subs = list(self._subs)
        for sub in subs:
            # a user who was just added to a project starts receiving its events immediately
            if (ev["type"] == "member.added" and sub.projects is not None
                    and ev["payload"].get("user_id") == sub.user_id):
                sub.projects.add(ev["project_id"])
            if not sub.wants(ev):
                continue
            try:
                sub.queue.put_nowait(ev)
            except queue.Full:
                sub.overflowed = True  # slow client: tell it to refetch instead of blocking everyone

    def _maybe_purge(self, conn):
        now = datetime.now(timezone.utc)
        if now - self._last_purge < timedelta(minutes=10):
            return
        self._last_purge = now
        limit = (now - self.retention).strftime("%Y-%m-%dT%H:%M:%S.%f+00:00")
        conn.execute("DELETE FROM events WHERE created_at < ?", (limit,))
        conn.commit()
