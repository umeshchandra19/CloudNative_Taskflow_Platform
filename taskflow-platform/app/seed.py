"""Optional seed data: an admin account from env vars, and demo users/projects/tasks for screenshots and load tests.

Both functions are idempotent and safe when several containers start at the same moment.
"""
import random
from datetime import datetime, timedelta, timezone

from .db import now_iso
from .security import hash_password

DEMO_PASSWORD = "demo-pass-123"
_TS = "%Y-%m-%dT%H:%M:%S.%f+00:00"


def ensure_admin(database, cfg):
    if not (cfg.admin_email and cfg.admin_password):
        return False
    email = cfg.admin_email.lower()
    with database.session() as db:
        if db.one("SELECT 1 AS x FROM users WHERE email = ?", (email,)):
            return False
        try:
            db.insert("INSERT INTO users (email, name, password_hash, role, created_at) VALUES (?, ?, ?, 'admin', ?)",
                      (email, "Administrator", hash_password(cfg.admin_password, n=cfg.scrypt_n), now_iso()))
            db.commit()
        except db.IntegrityError:  # another container created it a moment ago: that is fine
            db.rollback()
            return False
    return True


def seed_demo(database, cfg, tasks_per_project=60, seed=7):
    with database.session() as db:
        if db.one("SELECT 1 AS x FROM users WHERE email = ?", ("alice@demo.local",)):
            return False
        try:
            _seed(db, cfg, random.Random(seed), tasks_per_project)
            db.commit()
        except db.IntegrityError:  # a parallel container seeded first; our whole transaction is discarded
            db.rollback()
            return False
    return True


def _seed(db, cfg, rnd, tasks_per_project):
    people = [("alice", "Alice Rao", "owner"), ("bob", "Bob Mehta", "manager"),
              ("carol", "Carol Das", "member"), ("dave", "Dave Khan", "viewer")]
    ids = {}
    for handle, name, _ in people:
        ids[handle] = db.insert("INSERT INTO users (email, name, password_hash, role, created_at) VALUES (?, ?, ?, 'member', ?)",
                                (f"{handle}@demo.local", name, hash_password(DEMO_PASSWORD, n=cfg.scrypt_n), now_iso()))
    now = datetime.now(timezone.utc)
    today = now.date()
    for pname, pdesc in [("Website Relaunch", "Redesign and ship the new marketing site"),
                         ("Mobile App v2", "Second major release of the mobile app")]:
        pid = db.insert("INSERT INTO projects (name, description, owner_id, created_at) VALUES (?, ?, ?, ?)",
                        (pname, pdesc, ids["alice"], now_iso()))
        for handle, _, role in people:
            db.execute("INSERT INTO project_members (project_id, user_id, role) VALUES (?, ?, ?)", (pid, ids[handle], role))
        workers = [ids["alice"], ids["bob"], ids["carol"], None]
        for i in range(tasks_per_project):
            status = rnd.choices(["todo", "in_progress", "review", "done"], [4, 3, 2, 5])[0]
            due = (today + timedelta(days=rnd.randint(-10, 20))).isoformat() if rnd.random() < 0.8 else None
            created = (now - timedelta(days=rnd.randint(0, 20))).strftime(_TS)
            done_at = (now - timedelta(days=rnd.randint(0, 6))).strftime(_TS) if status == "done" else None
            db.execute("INSERT INTO tasks (project_id, title, description, status, priority, assignee_id, creator_id, "
                       "due_date, created_at, updated_at, completed_at) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
                       (pid, f"{pname.split()[0]} task {i + 1}", "Demo task", status,
                        rnd.choice(["low", "medium", "medium", "high", "urgent"]), rnd.choice(workers),
                        ids["alice"], due, created, now_iso(), done_at))
