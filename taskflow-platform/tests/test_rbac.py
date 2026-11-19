from tests.base import ApiTestCase


class RbacTests(ApiTestCase):
    def setUp(self):
        super().setUp()
        self.pid, self.t, self.u = self.team()

    def patch(self, who, tid, **body):
        return self.client.patch(f"/api/tasks/{tid}", json=body, headers=self.auth(self.t[who]))

    def test_non_member_gets_404_everywhere(self):
        task = self.new_task(self.t["owner"], self.pid)
        h = self.auth(self.t["outsider"])
        for method, url in [("get", f"/api/projects/{self.pid}"), ("get", f"/api/projects/{self.pid}/tasks"),
                            ("get", f"/api/tasks/{task['id']}"), ("get", f"/api/projects/{self.pid}/dashboard"),
                            ("get", f"/api/projects/{self.pid}/members")]:
            self.assertEqual(getattr(self.client, method)(url, headers=h).status_code, 404, url)
        self.assertEqual(self.client.patch(f"/api/tasks/{task['id']}", json={"title": "x"}, headers=h).status_code, 404)
        self.assertEqual(self.client.delete(f"/api/tasks/{task['id']}", headers=h).status_code, 404)

    def test_viewer_is_read_only(self):
        task = self.new_task(self.t["owner"], self.pid)
        h = self.auth(self.t["viewer"])
        self.assertEqual(self.client.get(f"/api/projects/{self.pid}/tasks", headers=h).status_code, 200)
        self.new_task(self.t["viewer"], self.pid, expect=403)
        self.assertEqual(self.patch("viewer", task["id"], title="x").status_code, 403)
        self.assertEqual(self.client.delete(f"/api/tasks/{task['id']}", headers=h).status_code, 403)

    def test_member_can_create_and_edit_own_tasks_only(self):
        mine = self.new_task(self.t["member"], self.pid, title="mine")
        theirs = self.new_task(self.t["owner"], self.pid, title="theirs")
        self.assertEqual(self.patch("member", mine["id"], status="in_progress").status_code, 200)
        self.assertEqual(self.patch("member", theirs["id"], status="done").status_code, 403)

    def test_member_can_edit_task_assigned_to_them(self):
        task = self.new_task(self.t["manager"], self.pid, assignee_id=self.u["member"]["id"])
        self.assertEqual(self.patch("member", task["id"], status="done").status_code, 200)

    def test_member_assign_rules(self):
        me, other = self.u["member"]["id"], self.u["manager"]["id"]
        self.new_task(self.t["member"], self.pid, assignee_id=me)                    # to self: ok
        self.new_task(self.t["member"], self.pid, expect=403, assignee_id=other)     # to others: needs assign permission
        self.new_task(self.t["manager"], self.pid, assignee_id=me)                   # manager may assign anyone

    def test_assignee_must_be_project_member(self):
        self.new_task(self.t["manager"], self.pid, expect=400, assignee_id=self.u["outsider"]["id"])
        self.new_task(self.t["manager"], self.pid, expect=400, assignee_id=99999)

    def test_only_manager_or_owner_can_delete_tasks(self):
        task = self.new_task(self.t["member"], self.pid)
        self.assertEqual(self.client.delete(f"/api/tasks/{task['id']}", headers=self.auth(self.t["member"])).status_code, 403)
        self.assertEqual(self.client.delete(f"/api/tasks/{task['id']}", headers=self.auth(self.t["manager"])).status_code, 204)

    def test_manager_can_edit_any_task(self):
        task = self.new_task(self.t["member"], self.pid)
        self.assertEqual(self.patch("manager", task["id"], priority="urgent").status_code, 200)

    def test_only_owner_manages_members_and_project(self):
        h = self.auth(self.t["manager"])
        r = self.client.post(f"/api/projects/{self.pid}/members", json={"email": "outsider@x.io", "role": "member"}, headers=h)
        self.assertEqual(r.status_code, 403)
        self.assertEqual(self.client.patch(f"/api/projects/{self.pid}", json={"name": "x"}, headers=h).status_code, 403)
        self.assertEqual(self.client.delete(f"/api/projects/{self.pid}", headers=h).status_code, 403)
        ho = self.auth(self.t["owner"])
        self.assertEqual(self.client.post(f"/api/projects/{self.pid}/members",
                                          json={"email": "outsider@x.io", "role": "viewer"}, headers=ho).status_code, 201)
        self.assertEqual(self.client.put(f"/api/projects/{self.pid}/members/{self.u['outsider']['id']}",
                                         json={"role": "member"}, headers=ho).status_code, 200)

    def test_cannot_make_someone_owner_or_touch_the_owner(self):
        ho = self.auth(self.t["owner"])
        r = self.client.post(f"/api/projects/{self.pid}/members", json={"email": "outsider@x.io", "role": "owner"}, headers=ho)
        self.assertEqual(r.status_code, 400)
        owner_id = self.u["owner"]["id"]
        self.assertEqual(self.client.delete(f"/api/projects/{self.pid}/members/{owner_id}", headers=ho).status_code, 400)
        self.assertEqual(self.client.put(f"/api/projects/{self.pid}/members/{owner_id}", json={"role": "viewer"}, headers=ho).status_code, 400)

    def test_removed_member_loses_access_and_is_unassigned(self):
        task = self.new_task(self.t["manager"], self.pid, assignee_id=self.u["member"]["id"])
        ho = self.auth(self.t["owner"])
        self.assertEqual(self.client.delete(f"/api/projects/{self.pid}/members/{self.u['member']['id']}", headers=ho).status_code, 204)
        self.assertEqual(self.client.get(f"/api/projects/{self.pid}", headers=self.auth(self.t["member"])).status_code, 404)
        self.assertIsNone(self.client.get(f"/api/tasks/{task['id']}", headers=ho).get_json()["assignee_id"])

    def test_admin_sees_everything_and_manages_users(self):
        admin_token, admin = self.register("boss@x.io", "boss")
        self.make_admin(admin["id"])
        h = self.auth(admin_token)
        self.assertEqual(self.client.get(f"/api/projects/{self.pid}", headers=h).status_code, 200)
        self.assertEqual(self.client.get("/api/projects", headers=h).get_json()["total"], 1)
        self.new_task(admin_token, self.pid)
        self.assertEqual(self.client.put(f"/api/users/{self.u['member']['id']}/role", json={"role": "admin"}, headers=h).status_code, 200)
        self.assertEqual(self.client.put(f"/api/users/{admin['id']}/role", json={"role": "member"}, headers=h).status_code, 400)
        self.assertEqual(self.client.get("/api/admin/stats", headers=h).status_code, 200)
        self.assertEqual(self.client.get("/api/admin/stats", headers=self.auth(self.t["owner"])).status_code, 403)

    def test_project_list_only_shows_my_projects(self):
        other = self.new_project(self.t["outsider"], "Private")
        mine = self.client.get("/api/projects", headers=self.auth(self.t["member"])).get_json()
        self.assertEqual([p["id"] for p in mine["items"]], [self.pid])
        self.assertEqual(mine["items"][0]["my_role"], "member")
        self.assertNotIn(other, [p["id"] for p in mine["items"]])
