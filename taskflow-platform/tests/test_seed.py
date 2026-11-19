import threading

from app.config import Config
from app.seed import DEMO_PASSWORD, ensure_admin, seed_demo
from tests.base import ApiTestCase


class SeedTests(ApiTestCase):
    def cfg(self, **kw):
        return Config(scrypt_n=1024, admin_email="Root@Example.com", admin_password="root-password-1", **kw)

    def test_admin_is_created_once_and_can_log_in(self):
        database = self.app.extensions["db"]
        self.assertTrue(ensure_admin(database, self.cfg()))
        self.assertFalse(ensure_admin(database, self.cfg()))
        r = self.client.post("/api/auth/login", json={"email": "root@example.com", "password": "root-password-1"})
        self.assertEqual(r.status_code, 200)
        self.assertEqual(r.get_json()["user"]["role"], "admin")

    def test_no_admin_without_credentials(self):
        self.assertFalse(ensure_admin(self.app.extensions["db"], Config(scrypt_n=1024)))

    def test_parallel_startup_does_not_crash(self):
        database, outcomes = self.app.extensions["db"], []
        barrier = threading.Barrier(4)

        def start():
            barrier.wait()
            outcomes.append((ensure_admin(database, self.cfg()), seed_demo(database, self.cfg(), tasks_per_project=5)))
        threads = [threading.Thread(target=start) for _ in range(4)]
        [t.start() for t in threads]
        [t.join() for t in threads]
        self.assertEqual(len(outcomes), 4)                      # nobody raised
        self.assertEqual(sum(1 for a, _ in outcomes if a), 1)   # exactly one created the admin
        self.assertEqual(sum(1 for _, s in outcomes if s), 1)   # exactly one seeded the demo data

    def test_demo_data_is_usable(self):
        database = self.app.extensions["db"]
        self.assertTrue(seed_demo(database, self.cfg(), tasks_per_project=20))
        self.assertFalse(seed_demo(database, self.cfg(), tasks_per_project=20))
        tokens = {}
        for who in ("alice", "bob", "carol", "dave"):
            r = self.client.post("/api/auth/login", json={"email": f"{who}@demo.local", "password": DEMO_PASSWORD})
            self.assertEqual(r.status_code, 200, who)
            tokens[who] = r.get_json()["token"]
        projects = self.client.get("/api/projects", headers=self.auth(tokens["dave"])).get_json()
        self.assertEqual(projects["total"], 2)
        self.assertEqual({p["my_role"] for p in projects["items"]}, {"viewer"})
        pid = projects["items"][0]["id"]
        self.assertEqual(self.client.get(f"/api/projects/{pid}/tasks", headers=self.auth(tokens["dave"])).get_json()["total"], 20)
        self.new_task(tokens["dave"], pid, expect=403)  # the demo viewer really is read-only
        self.assertGreater(self.client.get(f"/api/projects/{pid}/dashboard", headers=self.auth(tokens["alice"])).get_json()["total"], 0)


class ConfigTests(ApiTestCase):
    def test_db_password_with_special_characters_is_url_encoded(self):
        import os
        from unittest import mock
        env = {"DB_HOST": "db.example", "DB_USER": "u", "DB_PASSWORD": "p@ss/w:rd#1", "DB_NAME": "n"}
        with mock.patch.dict(os.environ, env, clear=False):
            os.environ.pop("DATABASE_URL", None)
            self.assertEqual(Config().database_url, "postgresql://u:p%40ss%2Fw%3Ard%231@db.example:5432/n")
