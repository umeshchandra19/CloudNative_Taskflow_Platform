from datetime import date, timedelta

from tests.base import ApiTestCase


class TaskApiTests(ApiTestCase):
    def setUp(self):
        super().setUp()
        self.token, self.user = self.register("owner@x.io", "Olive")
        self.pid = self.new_project(self.token)
        self.h = self.auth(self.token)

    def listing(self, query="", **kw):
        r = self.client.get(f"/api/projects/{self.pid}/tasks{query}", headers=self.h)
        self.assertEqual(r.status_code, kw.get("expect", 200), r.get_json())
        return r.get_json()

    def test_pagination(self):
        for i in range(25):
            self.new_task(self.token, self.pid, title=f"Task {i:02d}")
        page1 = self.listing("?page_size=10")
        self.assertEqual((page1["total"], page1["total_pages"], len(page1["items"])), (25, 3, 10))
        self.assertEqual(page1["items"][0]["title"], "Task 24")  # newest first by default
        page3 = self.listing("?page_size=10&page=3")
        self.assertEqual(len(page3["items"]), 5)
        self.assertEqual(len(self.listing("?page=99")["items"]), 0)
        self.assertEqual(self.listing("?page_size=1000")["page_size"], 100)  # capped
        self.listing("?page=abc", expect=400)

    def test_filters(self):
        me = self.user["id"]
        self.new_task(self.token, self.pid, title="Fix login bug", status="in_progress", priority="urgent", assignee_id=me)
        self.new_task(self.token, self.pid, title="Write docs", status="todo", priority="low")
        self.new_task(self.token, self.pid, title="100% done_ish", status="done")
        self.assertEqual(self.listing("?status=todo")["total"], 1)
        self.assertEqual(self.listing("?priority=urgent")["total"], 1)
        self.assertEqual(self.listing("?assignee=me")["total"], 1)
        self.assertEqual(self.listing("?assignee=none")["total"], 2)
        self.assertEqual(self.listing("?q=LOGIN")["total"], 1)
        self.assertEqual(self.listing("?q=%25")["total"], 1)   # a literal %, not a wildcard
        self.assertEqual(self.listing("?q=_")["total"], 1)     # a literal _, not "any character"
        self.listing("?status=bogus", expect=400)
        self.listing("?assignee=abc", expect=400)

    def test_overdue_filter(self):
        yesterday = (date.today() - timedelta(days=2)).isoformat()
        tomorrow = (date.today() + timedelta(days=5)).isoformat()
        self.new_task(self.token, self.pid, title="late", due_date=yesterday)
        self.new_task(self.token, self.pid, title="late but done", due_date=yesterday, status="done")
        self.new_task(self.token, self.pid, title="future", due_date=tomorrow)
        self.assertEqual([t["title"] for t in self.listing("?overdue=1")["items"]], ["late"])

    def test_sorting_puts_tasks_without_due_date_last(self):
        self.new_task(self.token, self.pid, title="none")
        self.new_task(self.token, self.pid, title="later", due_date="2030-01-02")
        self.new_task(self.token, self.pid, title="sooner", due_date="2030-01-01")
        asc = [t["title"] for t in self.listing("?sort=due_date")["items"]]
        desc = [t["title"] for t in self.listing("?sort=-due_date")["items"]]
        self.assertEqual(asc, ["sooner", "later", "none"])
        self.assertEqual(desc, ["later", "sooner", "none"])
        self.listing("?sort=password", expect=400)
        self.listing("?sort=id;drop", expect=400)

    def test_validation_on_create_and_update(self):
        self.new_task(self.token, self.pid, expect=400, title="")
        self.new_task(self.token, self.pid, expect=400, title="x" * 201)
        self.new_task(self.token, self.pid, expect=400, status="nope")
        self.new_task(self.token, self.pid, expect=400, due_date="31/12/2030")
        self.new_task(self.token, self.pid, expect=400, due_date="2030-02-31")
        task = self.new_task(self.token, self.pid)
        for body in ({}, {"status": "weird"}, {"assignee_id": "x"}):
            r = self.client.patch(f"/api/tasks/{task['id']}", json=body, headers=self.h)
            self.assertEqual(r.status_code, 400, body)

    def test_update_and_delete(self):
        task = self.new_task(self.token, self.pid, title="old")
        r = self.client.patch(f"/api/tasks/{task['id']}", json={"title": "new", "status": "review", "due_date": "2031-05-06"}, headers=self.h)
        body = r.get_json()
        self.assertEqual((body["title"], body["status"], body["due_date"]), ("new", "review", "2031-05-06"))
        self.assertEqual(self.client.delete(f"/api/tasks/{task['id']}", headers=self.h).status_code, 204)
        self.assertEqual(self.client.get(f"/api/tasks/{task['id']}", headers=self.h).status_code, 404)

    def test_sql_injection_attempts_are_inert(self):
        self.new_task(self.token, self.pid, title="safe")
        self.assertEqual(self.listing("?q=%27%20OR%201%3D1%20--")["total"], 0)
        self.assertEqual(self.listing("?status=todo%27%20OR%20%271%27%3D%271", expect=400), self.listing("?status=x", expect=400))
        self.assertEqual(self.listing()["total"], 1)

    # ---- caching ----
    def test_cache_serves_repeat_reads_and_is_invalidated_by_writes(self):
        self.new_task(self.token, self.pid, title="one")
        cache = self.app.extensions["cache"]
        self.listing(); self.listing(); self.listing()
        self.assertGreaterEqual(cache.hits, 2)
        self.new_task(self.token, self.pid, title="two")
        self.assertEqual(self.listing()["total"], 2)  # not the stale cached list
        task = self.listing()["items"][0]
        self.client.patch(f"/api/tasks/{task['id']}", json={"title": "renamed"}, headers=self.h)
        self.assertEqual(self.listing()["items"][0]["title"], "renamed")

    def test_etag_gives_304_until_something_changes(self):
        self.new_task(self.token, self.pid)
        url = f"/api/projects/{self.pid}/tasks?page_size=5"
        first = self.client.get(url, headers=self.h)
        etag = first.headers["ETag"]
        again = self.client.get(url, headers={**self.h, "If-None-Match": etag})
        self.assertEqual(again.status_code, 304)
        self.assertEqual(again.data, b"")
        self.new_task(self.token, self.pid, title="change")
        after = self.client.get(url, headers={**self.h, "If-None-Match": etag})
        self.assertEqual(after.status_code, 200)
        self.assertNotEqual(after.headers["ETag"], etag)

    def test_304_does_not_bypass_auth(self):
        first = self.client.get(f"/api/projects/{self.pid}/tasks", headers=self.h)
        outsider, _ = self.register("out@x.io")
        r = self.client.get(f"/api/projects/{self.pid}/tasks", headers={**self.auth(outsider), "If-None-Match": first.headers["ETag"]})
        self.assertEqual(r.status_code, 404)

    def test_other_projects_cache_unaffected(self):
        other = self.new_project(self.token, "Other")
        self.new_task(self.token, other, title="in other")
        self.listing()
        versions = self.app.extensions["versions"]
        before = versions.get(self.pid)
        self.new_task(self.token, other, title="another")
        self.assertEqual(versions.get(self.pid), before)
