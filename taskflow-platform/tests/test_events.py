import json
import threading
import time
import unittest

import requests
from werkzeug.serving import make_server

from tests.base import ApiTestCase


class EventBusTests(ApiTestCase):
    def test_poller_delivers_only_events_the_subscriber_may_see(self):
        pid, t, u = self.team()
        other_pid = self.new_project(t["outsider"], "Private")
        bus = self.app.extensions["bus"]
        bus.prime()                                   # start from the current end of the events table
        sub = bus.subscribe(u["member"]["id"], {pid})
        self.new_task(t["manager"], pid, title="visible")
        self.new_task(t["outsider"], other_pid, title="secret")
        bus.poll_once()
        events = []
        while not sub.queue.empty():
            events.append(sub.queue.get_nowait())
        self.assertEqual([e["type"] for e in events], ["task.created"])
        self.assertEqual(events[0]["project_id"], pid)
        self.assertEqual(events[0]["payload"]["title"], "visible")
        self.assertEqual(events[0]["payload"]["actor_name"], "manager")

    def test_each_event_is_delivered_once_even_with_overlap_window(self):
        token, _ = self.register("a@x.io")
        pid = self.new_project(token)
        bus = self.app.extensions["bus"]
        sub = bus.subscribe(1, None)
        self.new_task(token, pid)
        self.assertEqual(bus.poll_once(), 2)  # project.created + task.created
        self.assertEqual(bus.poll_once(), 0)  # recent rows are re-read but not re-delivered
        self.assertEqual(sub.queue.qsize(), 2)

    def test_slow_subscriber_is_told_to_resync_instead_of_blocking(self):
        token, _ = self.register("a@x.io")
        pid = self.new_project(token)
        bus = self.app.extensions["bus"]
        sub = bus.subscribe(1, None)
        for i in range(210):
            self.new_task(token, pid, title=f"t{i}")
        bus.poll_once()
        self.assertTrue(sub.overflowed)
        self.assertEqual(sub.queue.qsize(), 200)

    def test_new_member_starts_receiving_their_projects_events_immediately(self):
        owner, _ = self.register("o@x.io")
        guest, guest_user = self.register("g@x.io")
        pid = self.new_project(owner)
        bus = self.app.extensions["bus"]
        sub = bus.subscribe(guest_user["id"], set())        # guest is in no project yet
        self.add_member(owner, pid, "g@x.io", "viewer")
        self.new_task(owner, pid, title="after")
        bus.poll_once()
        types = [sub.queue.get_nowait()["type"] for _ in range(sub.queue.qsize())]
        self.assertEqual(types, ["member.added", "task.created"])

    def test_cache_is_invalidated_across_processes(self):
        """Two app instances = two gunicorn workers / ECS tasks sharing one database."""
        app2 = self.make_app()
        self.extra_apps = [app2]
        c2 = app2.test_client()
        token, _ = self.register("a@x.io")
        pid = self.new_project(token)
        url = f"/api/projects/{pid}/tasks"
        self.assertEqual(c2.get(url, headers=self.auth(token)).get_json()["total"], 0)   # app2 caches the empty list
        self.new_task(token, pid)                                                       # written through app1
        stale = c2.get(url, headers=self.auth(token)).get_json()["total"]
        self.assertEqual(stale, 0)                                                      # app2 has not heard yet
        app2.extensions["bus"].poll_once()                                              # what its poller does every 0.5 s
        self.assertEqual(c2.get(url, headers=self.auth(token)).get_json()["total"], 1)


class StreamTests(ApiTestCase):
    """End-to-end: real HTTP server, real SSE connection, real poller thread."""
    poller = True

    def setUp(self):
        super().setUp()
        self.server = make_server("127.0.0.1", 0, self.app, threaded=True)
        self.base = f"http://127.0.0.1:{self.server.server_port}"
        threading.Thread(target=self.server.serve_forever, daemon=True).start()

    def tearDown(self):
        self.server.shutdown()
        super().tearDown()

    def stream_token(self, token):
        return self.client.post("/api/auth/stream-token", headers=self.auth(token)).get_json()["token"]

    def read_events(self, response, want, timeout=5):
        """Collect `want` data events from an SSE response (runs in a thread so a hang cannot freeze the test)."""
        out, done = [], threading.Event()

        def reader():
            event_type = None
            try:
                for raw in response.iter_lines(decode_unicode=True):
                    if raw.startswith("event:"):
                        event_type = raw[6:].strip()
                    elif raw.startswith("data:") and event_type not in (None, "resync"):
                        out.append(json.loads(raw[5:]))
                        if len(out) >= want:
                            done.set()
                            return
            except Exception:  # connection closed/timed out by the test: nothing more to read
                return
        threading.Thread(target=reader, daemon=True).start()
        done.wait(timeout)
        return out

    def test_member_receives_live_events_and_outsider_does_not(self):
        pid, t, u = self.team()
        member = requests.get(f"{self.base}/api/stream", params={"token": self.stream_token(t["member"])}, stream=True, timeout=3)
        outsider = requests.get(f"{self.base}/api/stream", params={"token": self.stream_token(t["outsider"])}, stream=True, timeout=3)
        self.assertEqual(member.status_code, 200)
        self.assertTrue(member.headers["Content-Type"].startswith("text/event-stream"))
        time.sleep(0.3)  # let both subscriptions register
        started = time.time()
        self.new_task(t["manager"], pid, title="live task")
        got = self.read_events(member, 1)
        latency = time.time() - started
        self.assertEqual(len(got), 1, "member should receive the event")
        self.assertEqual(got[0]["type"], "task.created")
        self.assertEqual(got[0]["payload"]["title"], "live task")
        self.assertLess(latency, 2.0)
        self.assertEqual(self.read_events(outsider, 1, timeout=1), [], "outsider must not receive it")
        member.close(); outsider.close()

    def test_reconnect_replays_missed_events(self):
        pid, t, _ = self.team()
        first = self.new_task(t["manager"], pid, title="before")
        bus = self.app.extensions["bus"]; bus.poll_once()
        with self.app.extensions["db"].session() as db:
            last_seen = db.scalar("SELECT MAX(id) FROM events")
        self.new_task(t["manager"], pid, title="missed while offline")
        r = requests.get(f"{self.base}/api/stream", params={"token": self.stream_token(t["member"])},
                         headers={"Last-Event-ID": str(last_seen)}, stream=True, timeout=3)
        got = self.read_events(r, 1)
        self.assertEqual([e["payload"]["title"] for e in got], ["missed while offline"])
        r.close()

    def test_stream_rejects_bad_tokens(self):
        self.assertEqual(requests.get(f"{self.base}/api/stream").status_code, 401)
        self.assertEqual(requests.get(f"{self.base}/api/stream", params={"token": "nope"}).status_code, 401)

    def test_disconnect_removes_subscriber(self):
        pid, t, _ = self.team()
        bus = self.app.extensions["bus"]
        r = requests.get(f"{self.base}/api/stream", params={"token": self.stream_token(t["member"])}, stream=True, timeout=3)
        time.sleep(0.3)
        self.assertEqual(bus.subscriber_count, 1)
        r.close()
        # the generator notices the closed socket on the next write: trigger one
        self.new_task(t["manager"], pid, title="poke")
        deadline = time.time() + 3
        while bus.subscriber_count and time.time() < deadline:
            time.sleep(0.1)
        self.assertEqual(bus.subscriber_count, 0)


if __name__ == "__main__":
    unittest.main()
