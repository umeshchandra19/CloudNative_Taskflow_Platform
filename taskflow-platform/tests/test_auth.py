import time
import unittest

from app.security import hash_password, make_token, verify_password
from tests.base import ApiTestCase


class AuthTests(ApiTestCase):
    def test_register_login_me(self):
        token, user = self.register("ann@x.io", "Ann")
        self.assertEqual(user["role"], "member")
        r = self.client.post("/api/auth/login", json={"email": "ANN@x.io", "password": "password123"})
        self.assertEqual(r.status_code, 200)
        r = self.client.get("/api/auth/me", headers=self.auth(r.get_json()["token"]))
        self.assertEqual(r.get_json()["email"], "ann@x.io")

    def test_duplicate_email_conflict(self):
        self.register("ann@x.io")
        r = self.client.post("/api/auth/register", json={"email": "ann@x.io", "name": "A", "password": "password123"})
        self.assertEqual(r.status_code, 409)

    def test_validation(self):
        for body in ({"email": "nope", "name": "A", "password": "password123"},
                     {"email": "a@x.io", "name": "A", "password": "short"},
                     {"email": "a@x.io", "password": "password123"}, "not json"):
            r = self.client.post("/api/auth/register", json=body)
            self.assertEqual(r.status_code, 400, body)
            self.assertIn("error", r.get_json())

    def test_wrong_password_and_unknown_user_look_the_same(self):
        self.register("ann@x.io")
        a = self.client.post("/api/auth/login", json={"email": "ann@x.io", "password": "wrong-password"})
        b = self.client.post("/api/auth/login", json={"email": "ghost@x.io", "password": "wrong-password"})
        self.assertEqual((a.status_code, b.status_code), (401, 401))
        self.assertEqual(a.get_json(), b.get_json())

    def test_protected_routes_need_a_valid_token(self):
        self.assertEqual(self.client.get("/api/projects").status_code, 401)
        self.assertEqual(self.client.get("/api/projects", headers=self.auth("garbage")).status_code, 401)
        token, _ = self.register("ann@x.io")
        tampered = token[:-3] + ("aaa" if not token.endswith("aaa") else "bbb")
        self.assertEqual(self.client.get("/api/projects", headers=self.auth(tampered)).status_code, 401)

    def test_expired_token_rejected(self):
        _, user = self.register("ann@x.io")
        secret = self.app.config["CFG"].jwt_secret
        expired = make_token(secret, user["id"], "member", seconds=-5)
        self.assertEqual(self.client.get("/api/auth/me", headers=self.auth(expired)).status_code, 401)

    def test_token_signed_with_other_secret_rejected(self):
        _, user = self.register("ann@x.io")
        forged = make_token("x" * 40, user["id"], "admin", seconds=60)
        self.assertEqual(self.client.get("/api/auth/me", headers=self.auth(forged)).status_code, 401)

    def test_stream_token_cannot_be_used_as_api_token(self):
        token, _ = self.register("ann@x.io")
        stream = self.client.post("/api/auth/stream-token", headers=self.auth(token)).get_json()["token"]
        self.assertEqual(self.client.get("/api/auth/me", headers=self.auth(stream)).status_code, 401)
        # ...and a normal access token cannot open the stream
        self.assertEqual(self.client.get(f"/api/stream?token={token}").status_code, 401)

    def test_role_comes_from_database_not_token(self):
        token, user = self.register("ann@x.io")
        self.assertEqual(self.client.get("/api/users", headers=self.auth(token)).status_code, 403)
        self.make_admin(user["id"])
        self.assertEqual(self.client.get("/api/users", headers=self.auth(token)).status_code, 200)

    def test_password_hash_roundtrip(self):
        h = hash_password("s3cret-pass", n=1024)
        self.assertTrue(verify_password("s3cret-pass", h))
        self.assertFalse(verify_password("other", h))
        self.assertFalse(verify_password("s3cret-pass", "garbage"))
        self.assertNotEqual(h, hash_password("s3cret-pass", n=1024))  # random salt


if __name__ == "__main__":
    unittest.main()
