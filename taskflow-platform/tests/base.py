"""Shared test helpers. Runs on SQLite by default; set TEST_DATABASE_URL=postgresql://... to run on PostgreSQL."""
import os
import shutil
import tempfile
import unittest

from app import create_app


class ApiTestCase(unittest.TestCase):
    poller = False  # tests that need the real-time poller set this to True

    def setUp(self):
        self.tmp = tempfile.mkdtemp()
        self.url = os.environ.get("TEST_DATABASE_URL") or f"sqlite:///{self.tmp}/test.db"
        self.app = self.make_app()
        if self.url.startswith("postgres"):
            with self.app.extensions["db"].session() as db:
                db.execute("TRUNCATE events, tasks, project_members, projects, users RESTART IDENTITY CASCADE")
                db.commit()
        self.client = self.app.test_client()

    def make_app(self, **extra):
        opts = {"database_url": self.url, "scrypt_n": 1024, "start_event_poller": self.poller,
                "auto_init_db": True, "event_poll_interval": 0.1}
        opts.update(extra)
        return create_app(opts)

    def tearDown(self):
        for app in [self.app] + getattr(self, "extra_apps", []):
            app.extensions["bus"].stop()
            app.extensions["db"].close()
        shutil.rmtree(self.tmp, ignore_errors=True)

    # ---- helpers ----
    @staticmethod
    def auth(token):
        return {"Authorization": f"Bearer {token}"}

    def register(self, email, name=None, password="password123"):
        r = self.client.post("/api/auth/register", json={"email": email, "name": name or email.split("@")[0], "password": password})
        self.assertEqual(r.status_code, 201, r.get_json())
        body = r.get_json()
        return body["token"], body["user"]

    def make_admin(self, user_id):
        with self.app.extensions["db"].session() as db:
            db.execute("UPDATE users SET role = 'admin' WHERE id = ?", (user_id,))
            db.commit()

    def new_project(self, token, name="Project"):
        r = self.client.post("/api/projects", json={"name": name}, headers=self.auth(token))
        self.assertEqual(r.status_code, 201, r.get_json())
        return r.get_json()["id"]

    def add_member(self, owner_token, pid, email, role):
        r = self.client.post(f"/api/projects/{pid}/members", json={"email": email, "role": role}, headers=self.auth(owner_token))
        self.assertEqual(r.status_code, 201, r.get_json())

    def new_task(self, token, pid, expect=201, **fields):
        fields.setdefault("title", "A task")
        r = self.client.post(f"/api/projects/{pid}/tasks", json=fields, headers=self.auth(token))
        self.assertEqual(r.status_code, expect, r.get_json())
        return r.get_json()

    def team(self):
        """owner + manager + member + viewer + outsider, all in one project. Returns (pid, tokens dict, users dict)."""
        tokens, users = {}, {}
        for name in ("owner", "manager", "member", "viewer", "outsider"):
            tokens[name], users[name] = self.register(f"{name}@x.io", name)
        pid = self.new_project(tokens["owner"], "Team project")
        for name in ("manager", "member", "viewer"):
            self.add_member(tokens["owner"], pid, f"{name}@x.io", name)
        return pid, tokens, users
