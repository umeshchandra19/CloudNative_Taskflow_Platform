from datetime import date, timedelta

from tests.base import ApiTestCase


class DashboardTests(ApiTestCase):
    def setUp(self):
        super().setUp()
        self.token, self.user = self.register("owner@x.io", "Olive")
        self.pid = self.new_project(self.token)
        self.h = self.auth(self.token)

    def dash(self):
        r = self.client.get(f"/api/projects/{self.pid}/dashboard", headers=self.h)
        self.assertEqual(r.status_code, 200)
        return r.get_json()

    def test_empty_project(self):
        d = self.dash()
        self.assertEqual((d["total"], d["open"], d["done"], d["overdue"]), (0, 0, 0, 0))
        self.assertEqual(d["by_status"], {"todo": 0, "in_progress": 0, "review": 0, "done": 0})
        self.assertEqual(len(d["completed_last_7_days"]), 7)

    def test_counts(self):
        past = (date.today() - timedelta(days=3)).isoformat()
        me = self.user["id"]
        self.new_task(self.token, self.pid, status="todo", priority="high", due_date=past, assignee_id=me)
        self.new_task(self.token, self.pid, status="in_progress", priority="high")
        self.new_task(self.token, self.pid, status="done", priority="low", due_date=past)
        d = self.dash()
        self.assertEqual((d["total"], d["open"], d["done"]), (3, 2, 1))
        self.assertEqual(d["overdue"], 1)      # the done task is not overdue
        self.assertEqual(d["unassigned"], 1)
        self.assertEqual(d["by_priority"]["high"], 2)
        self.assertEqual(d["workload"][0]["name"], "Olive")
        self.assertEqual(d["completed_last_7_days"][-1]["count"], 1)  # completed today

    def test_dashboard_updates_after_changes(self):
        task = self.new_task(self.token, self.pid)
        self.assertEqual(self.dash()["done"], 0)
        self.client.patch(f"/api/tasks/{task['id']}", json={"status": "done"}, headers=self.h)
        self.assertEqual(self.dash()["done"], 1)
        self.client.patch(f"/api/tasks/{task['id']}", json={"status": "todo"}, headers=self.h)
        d = self.dash()
        self.assertEqual((d["done"], d["completed_last_7_days"][-1]["count"]), (0, 0))  # completion time cleared

    def test_my_dashboard(self):
        soon = (date.today() + timedelta(days=2)).isoformat()
        past = (date.today() - timedelta(days=1)).isoformat()
        me = self.user["id"]
        self.new_task(self.token, self.pid, title="soon", assignee_id=me, due_date=soon)
        self.new_task(self.token, self.pid, title="late", assignee_id=me, due_date=past)
        self.new_task(self.token, self.pid, title="not mine")
        r = self.client.get("/api/dashboard", headers=self.h).get_json()
        self.assertEqual((r["projects"], r["overdue"]), (1, 1))
        self.assertEqual(r["assigned_by_status"]["todo"], 2)
        self.assertEqual([t["title"] for t in r["due_soon"]], ["soon"])
