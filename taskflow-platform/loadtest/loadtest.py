#!/usr/bin/env python3
"""Load test for TaskFlow. Standard library only, works against any deployment (local, Docker, AWS).

    python loadtest/loadtest.py --base http://localhost:8000 --users 200 --duration 60

What it does
  1. Setup: registers one owner + N users, creates a project, adds the users as members, creates some tasks.
  2. Run:   every virtual user logs in once, then loops for --duration seconds with a realistic mix:
            50% list tasks (paged, sometimes filtered), 10% list again with If-None-Match (expects 304),
            20% project dashboard, 12% move one of its own tasks, 8% create a task.
            A random "think time" between actions mimics a human; --think-ms 0 = hammer as fast as possible.
  3. Optional --sse N: opens N live-update (SSE) connections and counts the events they receive.
  4. Report: requests/s, error rate and p50/p95/p99 latency per action. Use --json to save it.

Run it on a machine OTHER than the server for meaningful numbers, and state the server size when you quote results.
"""
import argparse
import http.client
import json
import random
import statistics
import threading
import time
import urllib.parse
import uuid
from concurrent.futures import ThreadPoolExecutor


class Client:
    def __init__(self, base):
        u = urllib.parse.urlparse(base)
        self.https, self.host, self.port = u.scheme == "https", u.hostname, u.port or (443 if u.scheme == "https" else 80)
        self.conn = None

    def _connect(self):
        cls = http.client.HTTPSConnection if self.https else http.client.HTTPConnection
        self.conn = cls(self.host, self.port, timeout=30)

    def request(self, method, path, body=None, token=None, headers=None):
        h = {"Content-Type": "application/json"}
        if token:
            h["Authorization"] = "Bearer " + token
        h.update(headers or {})
        data = json.dumps(body).encode() if body is not None else None
        for attempt in (1, 2):
            try:
                if self.conn is None:
                    self._connect()
                start = time.perf_counter()
                self.conn.request(method, path, body=data, headers=h)
                resp = self.conn.getresponse()
                payload = resp.read()
                elapsed = time.perf_counter() - start
                return resp.status, dict(resp.getheaders()), payload, elapsed
            except (http.client.HTTPException, OSError):
                self.conn = None
                if attempt == 2:
                    return 0, {}, b"", 0.0

    def json(self, method, path, body=None, token=None):
        status, _, payload, _ = self.request(method, path, body, token)
        return status, (json.loads(payload) if payload else None)


def setup(base, users, tasks_per_user, run_id):
    owner = Client(base)
    status, body = owner.json("POST", "/api/auth/register", {"email": f"owner-{run_id}@load.test", "name": "Load Owner", "password": "load-test-pass"})
    assert status == 201, f"cannot register owner: {status} {body}"
    owner_token = body["token"]
    status, project = owner.json("POST", "/api/projects", {"name": f"loadtest-{run_id}"}, owner_token)
    pid = project["id"]
    for i in range(40):
        owner.json("POST", f"/api/projects/{pid}/tasks", {"title": f"seed {i}", "priority": random.choice(["low", "medium", "high"])}, owner_token)

    def make_user(i):
        c = Client(base)
        email = f"user{i}-{run_id}@load.test"
        status, body = c.json("POST", "/api/auth/register", {"email": email, "name": f"User {i}", "password": "load-test-pass"})
        assert status == 201, f"register failed: {status} {body}"
        return email, body["token"]

    with ThreadPoolExecutor(max_workers=16) as pool:
        made = list(pool.map(make_user, range(users)))
    for email, _ in made:
        status, body = owner.json("POST", f"/api/projects/{pid}/members", {"email": email, "role": "member"}, owner_token)
        assert status == 201, f"add member failed: {status} {body}"
    return pid, [t for _, t in made]


def virtual_user(base, pid, token, deadline, think_ms, results, errors, stop):
    c = Client(base)
    my_tasks, etag = [], None
    for _ in range(3):  # each user starts with a few tasks it is allowed to edit
        s, _, payload, _ = c.request("POST", f"/api/projects/{pid}/tasks", {"title": "vu task"}, token)
        if s == 201:
            my_tasks.append(json.loads(payload)["id"])
    while time.time() < deadline and not stop.is_set():
        r = random.random()
        if r < 0.50:
            q = f"?page={random.randint(1, 3)}&page_size=20" + random.choice(["", "&status=todo", "&priority=high", "&sort=-updated_at"])
            name, (s, h, _, t) = "list_tasks", c.request("GET", f"/api/projects/{pid}/tasks{q}", token=token)
            ok = s == 200
        elif r < 0.60:
            path = f"/api/projects/{pid}/tasks?page_size=20"
            if etag is None:
                _, h0, _, _ = c.request("GET", path, token=token)
                etag = h0.get("ETag")
            name, (s, h, _, t) = "list_tasks_revalidate", c.request("GET", path, token=token, headers={"If-None-Match": etag} if etag else None)
            ok = s in (200, 304)
            etag = h.get("ETag", etag)
        elif r < 0.80:
            name, (s, h, _, t) = "dashboard", c.request("GET", f"/api/projects/{pid}/dashboard", token=token)
            ok = s == 200
        elif r < 0.92 and my_tasks:
            name, (s, h, _, t) = "update_task", c.request("PATCH", f"/api/tasks/{random.choice(my_tasks)}", {"status": random.choice(["todo", "in_progress", "review", "done"])}, token)
            ok = s == 200
        else:
            name, (s, h, payload, t) = "create_task", c.request("POST", f"/api/projects/{pid}/tasks", {"title": "vu task", "priority": "low"}, token)
            ok = s == 201
            if ok and len(my_tasks) < 20:
                my_tasks.append(json.loads(payload)["id"])
        results.append((name, t, ok))
        if not ok:
            errors.append((name, s))
        if think_ms:
            time.sleep(random.uniform(0.3, 1.7) * think_ms / 1000)


def sse_listener(base, token_provider, counter, stop):
    u = urllib.parse.urlparse(base)
    path = "/api/stream?token=" + urllib.parse.quote(token_provider())
    try:
        conn = (http.client.HTTPSConnection if u.scheme == "https" else http.client.HTTPConnection)(u.hostname, u.port or (443 if u.scheme == "https" else 80), timeout=60)
        conn.request("GET", path)
        resp = conn.getresponse()
        if resp.status != 200:
            counter["failed"] += 1
            return
        counter["connected"] += 1
        while not stop.is_set():
            line = resp.readline()
            if not line:
                break
            if line.startswith(b"data:"):
                counter["events"] += 1
    except Exception:
        counter["failed"] += 1


def percentile(sorted_values, p):
    if not sorted_values:
        return 0.0
    return sorted_values[min(len(sorted_values) - 1, int(len(sorted_values) * p))]


def main():
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--base", default="http://localhost:8000")
    ap.add_argument("--users", type=int, default=100, help="concurrent virtual users")
    ap.add_argument("--duration", type=int, default=30, help="seconds of load (after setup)")
    ap.add_argument("--think-ms", type=int, default=800, help="average pause between a user's actions (0 = none)")
    ap.add_argument("--sse", type=int, default=0, help="extra live-update (SSE) connections to hold open")
    ap.add_argument("--json", help="write the report to this file")
    a = ap.parse_args()

    run_id = uuid.uuid4().hex[:8]
    print(f"setup: {a.users} users (run {run_id}) ...", flush=True)
    t0 = time.time()
    pid, tokens = setup(a.base, a.users, 3, run_id)
    print(f"setup done in {time.time() - t0:.1f}s, project {pid}", flush=True)

    stop, results, errors = threading.Event(), [], []
    sse_counter = {"connected": 0, "failed": 0, "events": 0}
    for i in range(a.sse):
        tok = tokens[i % len(tokens)]
        client = Client(a.base)
        provider = lambda c=client, t=tok: c.json("POST", "/api/auth/stream-token", None, t)[1]["token"]
        threading.Thread(target=sse_listener, args=(a.base, provider, sse_counter, stop), daemon=True).start()
    time.sleep(1 if a.sse else 0)

    deadline = time.time() + a.duration
    started = time.time()
    threads = [threading.Thread(target=virtual_user, args=(a.base, pid, tok, deadline, a.think_ms, results, errors, stop), daemon=True) for tok in tokens]
    for t in threads:
        t.start()
    for t in threads:
        t.join(timeout=a.duration + 60)
    stop.set()
    wall = time.time() - started

    report = {"users": a.users, "duration_s": round(wall, 1), "think_ms": a.think_ms, "total_requests": len(results),
              "requests_per_second": round(len(results) / wall, 1), "errors": len(errors),
              "error_rate": round(len(errors) / max(1, len(results)), 4), "actions": {}}
    by_action = {}
    for name, latency, ok in results:
        by_action.setdefault(name, []).append(latency * 1000)
    for name, values in sorted(by_action.items()):
        values.sort()
        report["actions"][name] = {"count": len(values), "p50_ms": round(percentile(values, .50), 1),
                                   "p95_ms": round(percentile(values, .95), 1), "p99_ms": round(percentile(values, .99), 1),
                                   "mean_ms": round(statistics.mean(values), 1)}
    if a.sse:
        report["sse"] = sse_counter
    if errors:
        report["error_samples"] = [f"{n}: HTTP {s}" for n, s in errors[:5]]
    print(json.dumps(report, indent=2))
    if a.json:
        with open(a.json, "w") as f:
            json.dump(report, f, indent=2)


if __name__ == "__main__":
    main()
