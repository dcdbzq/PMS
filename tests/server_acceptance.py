"""Non-destructive, real HTTP acceptance/load tests for an authorized local-mode server.

Creates a uniquely named QA cohort; never changes pre-existing users/projects/reports.
Passwords are written only to --credentials-out, which must be outside the repository.
Run --help for usage. Uses only the Python standard library.
"""
from __future__ import annotations

import argparse
import copy
import csv
import gzip
import hashlib
import http.client
import io
import json
import random
import secrets
import os
import sys
import threading
import tempfile
import time
import zipfile
from collections import Counter, defaultdict
from concurrent.futures import ThreadPoolExecutor
from datetime import date, datetime, timedelta, timezone
from pathlib import Path
from urllib.parse import urlencode, urlsplit

ROOT = Path(__file__).resolve().parents[1]
TZ = timezone(timedelta(hours=8))


def read_admin(path):
    fields = {}
    for line in Path(path).read_text(encoding="utf-8-sig").splitlines():
        if ":" in line:
            key, value = line.split(":", 1)
            fields[key.strip().lower()] = value.strip()
    return fields


class Metrics:
    def __init__(self):
        self.rows = []
        self.lock = threading.Lock()

    def add(self, phase, method, path, status, elapsed, size, expected):
        with self.lock:
            self.rows.append({"phase": phase, "method": method, "path": path.split("?")[0],
                              "status": status, "ms": round(elapsed * 1000, 2), "bytes": size,
                              "ok": status in expected})

    def summary(self):
        groups = defaultdict(list)
        for row in self.rows:
            groups[(row["phase"], row["method"], row["path"])].append(row)
        result = []
        for (phase, method, path), rows in groups.items():
            ordered = sorted(r["ms"] for r in rows)
            percentile = lambda p: ordered[max(0, min(len(ordered)-1, int(len(ordered)*p+0.999)-1))]
            result.append({"phase": phase, "method": method, "path": path, "requests": len(rows),
                           "errors": sum(not r["ok"] for r in rows), "p50_ms": percentile(.5),
                           "p95_ms": percentile(.95), "p99_ms": percentile(.99), "max_ms": max(ordered),
                           "statuses": dict(Counter(str(r["status"]) for r in rows)),
                           "max_bytes": max(r["bytes"] for r in rows)})
        return result


class Client:
    def __init__(self, url, metrics=None, phase="functional", timeout=30):
        self.url = url.rstrip("/")
        self.origin = urlsplit(self.url)
        if self.origin.scheme not in ("http", "https") or self.origin.path not in ("", "/"):
            raise ValueError("Use a root http(s) URL")
        self.cookie, self.csrf = "", ""
        self.metrics, self.phase, self.timeout = metrics, phase, timeout

    def request(self, method, path, data=None, expected=(200,), headers=None):
        payload = data if isinstance(data, bytes) else json.dumps(data, ensure_ascii=False).encode() if data is not None else None
        request_headers = {"Content-Type": "application/json", "Cookie": self.cookie, "X-CSRF-Token": self.csrf, "Accept-Encoding": "gzip"}
        request_headers.update(headers or {})
        connection_class = http.client.HTTPSConnection if self.origin.scheme == "https" else http.client.HTTPConnection
        connection = connection_class(self.origin.hostname, self.origin.port, timeout=self.timeout)
        started, status, raw = time.perf_counter(), "network-error", b""
        try:
            connection.request(method, path, payload, request_headers)
            response = connection.getresponse()
            status, raw = response.status, response.read()
            cookie = response.getheader("Set-Cookie")
            if cookie:
                self.cookie = cookie.split(";", 1)[0]
            decoded = gzip.decompress(raw) if response.getheader("Content-Encoding") == "gzip" else raw
            result = json.loads(decoded) if (response.getheader("Content-Type") or "").startswith("application/json") else decoded
            if status == 200 and path == "/api/state":
                self.csrf = result["csrf"]
            if status not in expected:
                detail = result.get("error", "") if isinstance(result, dict) else ""
                raise AssertionError(f"{method} {path.split('?')[0]}: HTTP {status}, expected {expected}; {detail}")
            return result
        finally:
            if self.metrics:
                self.metrics.add(self.phase, method, path, status, time.perf_counter()-started, len(raw), expected)
            connection.close()

    def login(self, username, password):
        self.request("POST", "/api/login", {"username": username, "password": password})
        return self.request("GET", "/api/state")


class Acceptance:
    def __init__(self, args):
        self.args, self.metrics, self.checks = args, Metrics(), []
        self.prefix = datetime.now(TZ).strftime("%m%d%H%M") + secrets.token_hex(2)
        self.output = Path(args.out).resolve()
        self.output.mkdir(parents=True, exist_ok=True)
        self.credentials_path = Path(args.credentials_out).resolve()
        if self.credentials_path.is_relative_to(ROOT):
            raise ValueError("Credentials must be stored outside the repository")
        self.credentials_path.parent.mkdir(parents=True, exist_ok=True)
        self.admin_fields = read_admin(args.admin_file)
        self.url = args.url or self.admin_fields["url"]
        self.admin = Client(self.url, self.metrics)
        self.accounts, self.clients, self.projects = [], [], []
        self.expected, self.attachment = {}, None
        self.started = datetime.now(TZ).isoformat(timespec="seconds")

    def check(self, name, condition, detail=""):
        self.checks.append({"name": name, "passed": bool(condition), "detail": detail})
        if not condition:
            raise AssertionError(name + (": " + detail if detail else ""))

    def stage(self, name, function):
        print(f"[{datetime.now(TZ):%H:%M:%S}] {name}", flush=True)
        try:
            function()
        except Exception as exc:
            self.checks.append({"name": name, "passed": False, "detail": str(exc)[:500]})
            print(f"FAILED: {name}: {exc}", flush=True)
            return False
        return True

    def save_credentials(self):
        value = {"url": self.url, "prefix": self.prefix,
            "created_at": self.started, "accounts": self.accounts,
            "project_ids": [p["id"] for p in self.projects]}
        # Atomic replacement avoids partially written credentials and transient
        # Windows scanner locks during the hundred-account creation loop.
        with tempfile.NamedTemporaryFile(mode="w", encoding="utf-8", dir=self.credentials_path.parent,
                                         prefix=".qa-", suffix=".tmp", delete=False) as file:
            json.dump(value, file, ensure_ascii=False, indent=2)
            file.flush()
            os.fsync(file.fileno())
            pending = Path(file.name)
        for attempt in range(10):
            try:
                pending.replace(self.credentials_path)
                return
            except PermissionError:
                if attempt == 9:
                    raise PermissionError(f"Credential file locked; recover the latest snapshot from {pending}") from None
                time.sleep(.1)

    def preflight(self):
        bootstrap = self.admin.request("GET", "/api/bootstrap")
        self.check("server uses local authentication; Feishu not enabled", bootstrap["auth_mode"] == "local")
        state = self.admin.login(self.admin_fields["username"], self.admin_fields["password"])
        self.check("administrator login", state["user"]["role"] == "admin")
        self.baseline = {k: len(state[k]) for k in ("users", "projects", "reports", "submissions", "attachments")}
        self.baseline_ids = {k: {v["id"] for v in state[k]} for k in ("users", "projects", "reports", "attachments")}
        self.week = date.fromisoformat(state["week"])
        self.weeks = [(self.week-timedelta(weeks=w)).isoformat() for w in reversed(range(self.args.weeks))]
        for path, filename in [("/api/export", "before-reports.csv"), ("/api/export/attachments", "before-attachments.zip")]:
            (self.output/filename).write_bytes(self.admin.request("GET", path))

    def seed(self):
        self.admin.phase = "seed"
        if getattr(self.args, "reuse_cohort", False):
            cohort = json.loads(self.credentials_path.read_text(encoding="utf-8"))
            if cohort["url"].rstrip("/") != self.url.rstrip("/"):
                raise ValueError("Existing cohort belongs to a different URL")
            self.prefix, self.accounts = cohort["prefix"], cohort["accounts"]
            self.args.users = len(self.accounts)
            state = self.admin.request("GET", "/api/state")
            projects = {p["id"]: p for p in state["projects"]}
            self.projects = [projects[pid] for pid in cohort["project_ids"]]
            for project in self.projects:
                project["archived"] = bool(project["archived"])
            self.args.projects = len(self.projects)
            ids = {a["id"]: i for i, a in enumerate(self.accounts)}
            for kind in ("users", "projects", "reports", "attachments"):
                self.baseline_ids[kind] = {r["id"] for r in state[kind]
                    if (r["id"] not in ids if kind == "users" else r["id"] not in cohort["project_ids"] if kind == "projects" else r["user_id"] not in ids)}
            self.weeks = sorted({s["week"] for s in state["submissions"] if s["user_id"] in ids})
            if len(self.weeks) < 2 or len(self.accounts) < 100 or len(self.projects) < 20:
                raise ValueError("Existing cohort is incomplete; inspect it before reusing")
            self.args.weeks = len(self.weeks)
            for i, account in enumerate(self.accounts):
                actual = {p["id"] for p in self.projects if account["id"] in p["member_ids"]}
                wanted = {self.projects[n]["id"] for n in self.project_indices(i)}
                if actual != wanted:
                    raise ValueError("Cohort memberships have been manually changed; use a new cohort")
            for submission in state["submissions"]:
                if submission["user_id"] in ids:
                    key = (ids[submission["user_id"]], submission["week"])
                    entries = [{k: r[k] for k in ("project_id", "progress", "effort", "note")}
                               for r in state["reports"] if r["user_id"] == submission["user_id"] and r["week"] == submission["week"]]
                    order = [self.projects[n]["id"] for n in self.project_indices(ids[submission["user_id"]])]
                    entries.sort(key=lambda e: order.index(e["project_id"]))
                    self.expected[key] = {"week": submission["week"], "entries": entries,
                        "leave_percent": submission["leave_percent"], "leave_note": submission["leave_note"]}
            self.check("reuse existing cohort without creating additional accounts", True)
        # Persist each new credential immediately so an interrupted run remains recoverable.
        for i in range(0 if self.accounts else self.args.users):
            account = {"username": f"qa{self.prefix}_{i+1:03}", "password": secrets.token_urlsafe(18),
                       "name": f"验收{self.prefix[-4:]}成员{i+1:03}", "department": f"验收团队{i%5+1}",
                       "role": "member", "active": True}
            result = self.admin.request("POST", "/api/users", account)
            account["id"] = result["id"]
            self.accounts.append(account)
            self.save_credentials()
        for i in range(0 if self.projects else self.args.projects):
            members = [a["id"] for n, a in enumerate(self.accounts) if i in self.project_indices(n)]
            project = {"name": f"【验收{self.prefix}】协作项目{i+1:02}",
                       "description": "自动验收数据：多成员、多项目、多周填报；可保留用于人工试用。",
                       "owner_id": self.accounts[i]["id"], "start_date": self.weeks[0],
                       "due_date": (self.week+timedelta(weeks=12)).isoformat(),
                       "priority": ("normal", "high", "low")[i%3], "member_ids": members, "archived": False}
            project["id"] = self.admin.request("POST", "/api/projects", project)["id"]
            self.projects.append(project)
            self.save_credentials()
        self.clients = [Client(self.url, self.metrics, "login") for _ in self.accounts]
        def login(i):
            state = self.clients[i].login(self.accounts[i]["username"], self.accounts[i]["password"])
            self.check(f"identity {i+1}", state["user"]["id"] == self.accounts[i]["id"])
        with ThreadPoolExecutor(max_workers=10) as pool:
            list(pool.map(login, range(len(self.accounts))))
        self.check("all test accounts can log in", len(self.clients) == self.args.users)

    def project_indices(self, index):
        return [(index+shift) % self.args.projects for shift in (0, 1, 7)]

    def payload(self, index, week, revision=0):
        w = self.weeks.index(week)
        leave = 100 if (index+w)%23 == 0 else 20 if (index+w)%13 == 0 else 0
        efforts = [40, 24, 16] if leave == 20 else [50, 30, 20]
        entries = [] if leave == 100 else [{"project_id": self.projects[p]["id"],
            "progress": min(100, ((w+1)*3+index%11+revision)/2), "effort": efforts[n],
            "note": f"验收成员{index+1}第{w+1}周修订{revision}：完成开发、联调与测试。"}
            for n, p in enumerate(self.project_indices(index))]
        return {"week": week, "entries": entries, "leave_percent": leave,
                "leave_note": "验收：整周休假" if leave == 100 else "验收：休假一天" if leave else ""}

    def history(self):
        if getattr(self.args, "reuse_cohort", False):
            self.verify_data("reused cohort baseline")
            return
        def submit(i):
            client = self.clients[i]
            client.phase = "history"
            for week in self.weeks:
                body = self.payload(i, week)
                client.request("PUT", "/api/reports", body)
                self.expected[(i, week)] = body
        with ThreadPoolExecutor(max_workers=10) as pool:
            list(pool.map(submit, range(len(self.accounts))))
        self.check("multi-week history created through HTTP", len(self.expected) == self.args.users*self.args.weeks)
        self.verify_data("after history")

    def verify_data(self, label):
        self.admin.phase = "verify"
        state = self.admin.request("GET", "/api/state")
        ids = {a["id"]: i for i, a in enumerate(self.accounts)}
        reports = {(ids[r["user_id"]], r["week"], r["project_id"]): r for r in state["reports"] if r["user_id"] in ids}
        submissions = {(ids[s["user_id"]], s["week"]): s for s in state["submissions"] if s["user_id"] in ids}
        wanted = {}
        for (i, week), payload in self.expected.items():
            actual = submissions.get((i, week), {})
            assert actual.get("leave_percent") == payload["leave_percent"], f"{label}: leave mismatch for {i}/{week}"
            assert actual.get("leave_note") == payload["leave_note"], f"{label}: leave note mismatch for {i}/{week}"
            for entry in payload["entries"]:
                key = (i, week, entry["project_id"])
                wanted[key] = entry
                actual = reports.get(key, {})
                for field in ("progress", "effort", "note"):
                    assert actual.get(field) == entry[field], f"{label}: {field} mismatch for {key}"
        self.check(f"{label}: every report value and leave value matches", reports.keys() == wanted.keys() and submissions.keys() == self.expected.keys())
        for kind in self.baseline_ids:
            self.check(f"{label}: original {kind} still present", self.baseline_ids[kind] <= {r["id"] for r in state[kind]})
        self.counts = {k: len(state[k]) for k in ("users", "projects", "reports", "submissions", "attachments")}
        return state

    def functional(self):
        self.admin.phase = "functional"
        client = self.clients[1]
        for item in self.clients:
            item.phase = "functional"
        guest = Client(self.url, self.metrics)
        guest.request("GET", "/api/state", expected=(401,))
        guest.request("GET", "/api/export", expected=(401,))
        guest.request("POST", "/api/setup", {"name": "非法初始化", "username": "qa_reinit", "password": "password123"}, expected=(409,))
        guest.request("GET", "/.env", expected=(404,))
        guest.request("GET", "/data/pms.sqlite3", expected=(404,))
        guest.request("GET", "/api/auth/feishu/start", expected=(404,))
        client.request("POST", "/api/users", self.accounts[0], expected=(403,))
        client.request("POST", "/api/projects", self.projects[0], expected=(403,))
        client.request("GET", "/api/export", expected=(403,))
        client.request("GET", "/api/export/attachments", expected=(403,))
        client.request("PUT", f"/api/users/{self.accounts[0]['id']}/role", {"role": "admin"}, expected=(403,))
        self.check("guest and ordinary-member administrative restrictions", True)
        # Index 1 participates in projects 1, 2, 8; project 0 is private to other users.
        p = self.projects[0]
        state = client.request("GET", "/api/state")
        visible = {x["id"] for x in state["projects"]}
        allowed = {x["id"] for x in self.projects if self.accounts[1]["id"] in x["member_ids"]}
        self.check("project and report isolation", visible == allowed and all(r["project_id"] in visible for r in state["reports"]))
        client.request("PUT", f"/api/projects/{p['id']}", p, expected=(403,))
        bad = {"week": self.weeks[-1], "entries": [{"project_id": p["id"], "progress": 20, "effort": 100, "note": "越权"}]}
        client.request("PUT", "/api/reports", bad, expected=(403,))
        baseline = copy.deepcopy(self.expected[(1, self.weeks[-1])])
        variants = []
        for field, value in [("progress", 101), ("progress", -1), ("progress", .1), ("progress", True), ("progress", float("nan")),
                             ("effort", 99), ("note", ""), ("note", "a"*1001), ("project_id", True)]:
            body = copy.deepcopy(baseline)
            body["entries"][0][field] = value
            variants.append(body)
        body = copy.deepcopy(baseline)
        body["entries"].append(body["entries"][0])
        variants.append(body)
        for value in [(self.week+timedelta(weeks=1)).isoformat(), (self.week+timedelta(days=1)).isoformat(), "2026-02-30", None]:
            body = copy.deepcopy(baseline)
            body["week"] = value
            variants.append(body)
        for body in variants:
            client.request("PUT", "/api/reports", body, expected=(400,))
        client.request("PUT", "/api/reports", b"[]", expected=(400,))
        client.request("PUT", "/api/reports", b"{invalid", expected=(400,))
        client.request("PUT", "/api/reports", baseline, expected=(403,), headers={"X-CSRF-Token": "incorrect"})
        client.request("PUT", "/api/reports", baseline, expected=(403,), headers={"Origin": "http://other.invalid"})
        # Report body cannot impersonate another member.
        body = copy.deepcopy(baseline)
        body["user_id"] = self.accounts[0]["id"]
        body["entries"][0]["user_id"] = self.accounts[0]["id"]
        client.request("PUT", "/api/reports", body)
        self.check("input boundaries, CSRF, cross-origin and impersonation checks", True)
        self.verify_data("after rejected requests (atomic rollback)")
        owner = self.clients[0]
        target = next(i for i, a in enumerate(self.accounts) if i != 0 and a["id"] in p["member_ids"])
        updated = dict(p, description=p["description"] + " 负责人已完成成员调整验收。")
        updated["member_ids"] = [u for u in p["member_ids"] if u != self.accounts[target]["id"]]
        owner.request("PUT", f"/api/projects/{p['id']}", updated)
        owner.request("PUT", f"/api/projects/{p['id']}", p)
        archived = dict(p, archived=True)
        owner.request("PUT", f"/api/projects/{p['id']}", archived)
        old = next(body for (i, _), body in self.expected.items() if i == 0 and body["entries"])
        owner.request("PUT", "/api/reports", old)
        new_archived = {"week": self.weeks[0], "entries": [{"project_id": p["id"], "progress": 10, "effort": 100, "note": "归档后不接受新条目"}]}
        owner.request("PUT", "/api/reports", new_archived, expected=(403,))
        owner.request("PUT", f"/api/projects/{p['id']}", p)
        self.check("project owner can edit members, archive and reopen", True)
        # Historical reports survive removal, while a new week/project combination is forbidden.
        removed = dict(p, member_ids=[u for u in p["member_ids"] if u != self.accounts[target]["id"]])
        owner.request("PUT", f"/api/projects/{p['id']}", removed)
        history = next((body for (i, _), body in self.expected.items() if i == target and any(e["project_id"] == p["id"] for e in body["entries"])), None)
        if history:
            self.clients[target].request("PUT", "/api/reports", history)
            self.check("removed member retains historical access", p["id"] in {v["id"] for v in self.clients[target].request("GET", "/api/state")["projects"]})
        owner.request("PUT", f"/api/projects/{p['id']}", p)
        self.check("membership removal preserves historical reports", True)
        client.request("PUT", "/api/reports", baseline)
        client.request("PUT", "/api/reports", baseline)
        self.verify_data("after identical report retries (no duplicates)")
        # Never target the real administrator for role/deactivation checks.
        last = len(self.accounts)-1
        account = self.accounts[last]
        self.admin.request("PUT", f"/api/users/{account['id']}", dict(account, active=False, password=""))
        self.clients[last].request("GET", "/api/state", expected=(401,))
        self.admin.request("PUT", f"/api/users/{account['id']}", dict(account, password=""))
        self.clients[last].login(account["username"], account["password"])
        secondary = Client(self.url, self.metrics)
        secondary.login(account["username"], account["password"])
        new_password = secrets.token_urlsafe(18)
        self.clients[last].request("PUT", "/api/password", {"old_password": account["password"], "password": new_password})
        account["password"] = new_password
        self.save_credentials()
        secondary.request("GET", "/api/state", expected=(401,))
        self.clients[last].request("GET", "/api/state")
        self.clients[last].request("POST", "/api/logout", {})
        self.clients[last].request("GET", "/api/state", expected=(401,))
        self.clients[last].login(account["username"], account["password"])
        self.admin.request("PUT", f"/api/users/{account['id']}/role", {"role": "admin"})
        self.clients[last].request("GET", "/api/export")
        self.admin.request("PUT", f"/api/users/{account['id']}/role", {"role": "member"})
        self.clients[last].request("GET", "/api/export", expected=(403,))
        self.check("business role changes take effect in existing sessions", True)
        self.check("deactivation, password change and logout revoke sessions", True)

    def attachments_and_exports(self):
        client = self.clients[1]
        body = copy.deepcopy(self.expected[(1, self.weeks[-1])])
        pid = body["entries"][0]["project_id"]
        content = ("中文验收附件\n"*100).encode()
        query = urlencode({"project_id": pid, "week": body["week"], "filename": "验收证据.txt"})
        item = client.request("POST", "/api/attachments?"+query, content, expected=(201,), headers={"Content-Type": "application/octet-stream"})
        url = f"/api/attachments/{item['id']}"
        self.check("uploader downloads staged attachment byte-for-byte", client.request("GET", url) == content)
        peer_index = next(i for i, a in enumerate(self.accounts) if i != 1 and a["id"] in self.projects[1]["member_ids"])
        peer = self.clients[peer_index]
        peer.request("GET", url, expected=(403,))
        body["entries"][0]["attachment_ids"] = [item["id"]]
        client.request("PUT", "/api/reports", body)
        self.check("project peer downloads published attachment byte-for-byte", peer.request("GET", url) == content)
        outsider_index = next(i for i, a in enumerate(self.accounts) if a["id"] not in self.projects[1]["member_ids"])
        self.clients[outsider_index].request("GET", url, expected=(403,))
        self.expected[(1, body["week"])] = body
        self.attachment = {"id": item["id"], "sha256": hashlib.sha256(content).hexdigest(), "content": content.decode()}
        csv_content = self.admin.request("GET", "/api/export")
        rows = list(csv.reader(io.StringIO(csv_content.decode("utf-8-sig"))))
        reports = sum(len(b["entries"]) for b in self.expected.values())
        leaves = sum(b["leave_percent"] > 0 for b in self.expected.values())
        self.check("CSV contains all QA reports and leave rows", len(rows)-1 >= reports+leaves)
        (self.output/"after-reports.csv").write_bytes(csv_content)
        archive = self.admin.request("GET", "/api/export/attachments")
        with zipfile.ZipFile(io.BytesIO(archive)) as z:
            self.check("ZIP CRC integrity", z.testzip() is None)
            names = [n for n in z.namelist() if n.endswith(f"{item['id']}-验收证据.txt")]
            self.check("ZIP attachment bytes and manifest", len(names) == 1 and z.read(names[0]) == content and "附件清单.csv" in z.namelist())
        (self.output/"after-attachments.zip").write_bytes(archive)

    def load(self):
        self.load_results = []
        for concurrency in self.args.concurrency:
            if concurrency > len(self.clients):
                continue
            phase = f"load-{concurrency}"
            print(f"  ramp: {concurrency} simultaneous clients", flush=True)
            start = threading.Event()
            errors = []
            def worker(i):
                client = self.clients[i]
                client.phase = phase
                start.wait()
                try:
                    for iteration in range(3):
                        state = client.request("GET", "/api/state")
                        assert state["user"]["id"] == self.accounts[i]["id"], "identity leakage"
                        allowed = {p["id"] for p in self.projects if self.accounts[i]["id"] in p["member_ids"]}
                        assert {p["id"] for p in state["projects"]} == allowed, "project leakage"
                        if iteration == 1:
                            body = copy.deepcopy(self.expected[(i, self.weeks[-1])])
                            for entry in body["entries"]:
                                entry["note"] += f" 并发{concurrency}已复核。"
                            client.request("PUT", "/api/reports", body)
                            self.expected[(i, self.weeks[-1])] = body
                except Exception as exc:
                    errors.append(str(exc))
            started = time.perf_counter()
            with ThreadPoolExecutor(max_workers=concurrency) as pool:
                futures = [pool.submit(worker, i) for i in range(concurrency)]
                start.set()
                for f in futures:
                    f.result()
            elapsed = time.perf_counter()-started
            samples = [r for r in self.metrics.rows if r["phase"] == phase]
            self.load_results.append({"clients": concurrency, "requests": len(samples), "elapsed_seconds": round(elapsed, 2),
                                      "requests_per_second": round(len(samples)/elapsed, 2), "failures": errors[:10]})
            print(f"    {len(samples)} requests in {elapsed:.2f}s; {len(errors)} client failures", flush=True)
            self.checks.append({"name": f"{concurrency} concurrent readers/writers: no request or isolation errors", "passed": not errors, "detail": "; ".join(errors[:3])})
            for method, budget in [("GET", self.args.read_p95_ms), ("PUT", self.args.write_p95_ms)]:
                values = sorted(r["ms"] for r in samples if r["method"] == method)
                p95 = values[max(0, int(len(values)*.95+.999)-1)] if values else float("inf")
                self.checks.append({"name": f"{concurrency} concurrent {method}: p95 <= {budget}ms", "passed": p95 <= budget, "detail": f"p95={p95:.2f}ms"})
        self.verify_data("after concurrent writes")

    def soak(self):
        phase = "soak"
        deadline = time.perf_counter()+self.args.soak_seconds
        failures, counts = [], []
        def worker(i):
            client = self.clients[i]
            client.phase = phase
            rng, count = random.Random(i), 0
            while time.perf_counter() < deadline:
                try:
                    if count % 4 == 3:
                        client.request("PUT", "/api/reports", self.expected[(i, self.weeks[-1])])
                    else:
                        state = client.request("GET", "/api/state")
                        assert state["user"]["id"] == self.accounts[i]["id"], "identity leakage"
                    count += 1
                except Exception as exc:
                    failures.append(str(exc))
                time.sleep(rng.uniform(.25, 1))
            counts.append(count)
        with ThreadPoolExecutor(max_workers=min(20, len(self.clients))) as pool:
            list(pool.map(worker, range(min(20, len(self.clients)))))
        self.check("sustained mixed reads/writes have no errors", not failures, "; ".join(failures[:3]))
        self.soak_result = {"seconds": self.args.soak_seconds, "clients": len(counts), "successful_requests": sum(counts)}
        self.verify_data("after soak")
        if self.attachment:
            raw = self.admin.request("GET", f"/api/attachments/{self.attachment['id']}")
            self.check("attachment survives load", hashlib.sha256(raw).hexdigest() == self.attachment["sha256"])

    def report(self):
        result = {"started_at": self.started, "finished_at": datetime.now(TZ).isoformat(timespec="seconds"),
                  "url": self.url, "prefix": self.prefix, "baseline_counts": getattr(self, "baseline", {}),
                  "counts": getattr(self, "counts", {}), "users": self.args.users, "projects": self.args.projects,
                  "weeks": self.args.weeks, "checks": self.checks, "metrics": self.metrics.summary(),
                  "load": getattr(self, "load_results", []), "soak": getattr(self, "soak_result", {}),
                  "limitations": ["External HTTP tests do not verify server CPU/RAM/disk or process supervision.",
                                  "CSV/ZIP are business exports, not a complete SQLite and attachment backup.",
                                  "A short soak is not a full-day reliability run; Feishu is excluded."]}
        failed = [c for c in self.checks if not c["passed"]]
        result["passed"] = not failed
        (self.output/"report.json").write_text(json.dumps(result, ensure_ascii=False, indent=2), encoding="utf-8")
        with (self.output/"requests.csv").open("w", encoding="utf-8-sig", newline="") as f:
            writer = csv.DictWriter(f, fieldnames=["phase", "method", "path", "status", "ms", "bytes", "ok"])
            writer.writeheader()
            writer.writerows(self.metrics.rows)
        lines = ["# 服务器本地模式自动验收", "", f"时间：{self.started} — {result['finished_at']}", f"地址：{self.url}",
                 f"测试标记：{self.prefix}", f"结果：{'通过' if result['passed'] else '存在未通过项'}；{len(self.checks)-len(failed)}/{len(self.checks)} 项通过。", "",
                 f"数据：{json.dumps(result['counts'], ensure_ascii=False)}", "", "## 并发结果", "",
                 "|阶段|请求|错误|P50 ms|P95 ms|P99 ms|最大 ms|", "|---|---:|---:|---:|---:|---:|---:|"]
        for row in result["metrics"]:
            if row["phase"].startswith(("load-", "soak", "login")):
                lines.append(f"|{row['phase']} {row['method']} {row['path']}|{row['requests']}|{row['errors']}|{row['p50_ms']}|{row['p95_ms']}|{row['p99_ms']}|{row['max_ms']}|")
        lines += ["", "## 检查项", ""] + [f"- [{'x' if c['passed'] else ' '}] {c['name']} {c['detail']}" for c in self.checks]
        lines += ["", "## 验证边界", "", "本轮没有连接飞书。测试账号、项目和历史周报保留供人工试用。账号密码仅保存在本机独立凭据文件。",
                  "服务器资源监控、进程重启、SQLite 与附件完整备份恢复、长时间稳定性需要单独验证。业务导出不能代替完整备份。"]
        (self.output/"report.md").write_text("\n".join(lines)+"\n", encoding="utf-8")
        print(f"Report: {self.output/'report.md'}; passed={result['passed']}; checks={len(self.checks)-len(failed)}/{len(self.checks)}", flush=True)
        return result["passed"]

    def run(self):
        try:
            for name, function in [("preflight and pre-test exports", self.preflight), ("create cohort and login", self.seed),
                                   ("create multi-week history", self.history), ("permissions and lifecycle", self.functional),
                                   ("attachments and exports", self.attachments_and_exports), ("concurrency ramp", self.load),
                                   ("sustained mixed workload", self.soak)]:
                if not self.stage(name, function):
                    break
        finally:
            passed = self.report()
        return passed


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--url", help="Defaults to URL in initial-admin.txt")
    parser.add_argument("--admin-file", required=True, help="Deployment initial-admin.txt; never printed")
    parser.add_argument("--credentials-out", required=True, help="Private QA account JSON outside the repo")
    parser.add_argument("--out", default="test-results/server-acceptance")
    parser.add_argument("--reuse-cohort", action="store_true", help="Reuse the existing --credentials-out cohort; preserve its history")
    parser.add_argument("--users", type=int, default=100)
    parser.add_argument("--projects", type=int, default=20)
    parser.add_argument("--weeks", type=int, default=26)
    parser.add_argument("--concurrency", type=int, nargs="+", default=[10, 25, 50, 100])
    parser.add_argument("--soak-seconds", type=int, default=180)
    parser.add_argument("--read-p95-ms", type=int, default=2000)
    parser.add_argument("--write-p95-ms", type=int, default=3000)
    args = parser.parse_args()
    if not (100 <= args.users <= 300 and 20 <= args.projects <= min(args.users, 100) and 2 <= args.weeks <= 104):
        parser.error("Use 100..300 users, 20..min(users,100) projects and 2..104 weeks")
    if not 0 <= args.soak_seconds <= 86400 or any(n < 1 or n > args.users for n in args.concurrency):
        parser.error("Invalid soak duration or concurrency")
    return 0 if Acceptance(args).run() else 1


if __name__ == "__main__":
    sys.exit(main())
