"""真实 HTTP + 临时 SQLite 集成测试。"""
import http.client
import csv
import io
import gzip
import json
import sqlite3
import tempfile
import threading
import unittest
import zipfile
from concurrent.futures import ThreadPoolExecutor
from contextlib import closing
from datetime import date, timedelta
from pathlib import Path
from urllib.parse import urlencode
from app import Server, current_week, initialize, MAX_ATTACHMENT_SIZE
from feishu import FeishuConfig


class Client:
    def __init__(self, port):
        self.port, self.cookie, self.csrf = port, "", ""

    def request(self, method, path, data=None, headers=None):
        connection = http.client.HTTPConnection("127.0.0.1", self.port, timeout=10)
        payload = data if isinstance(data, bytes) else json.dumps(data).encode() if data is not None else None
        request_headers = {"Cookie": self.cookie, "X-CSRF-Token": self.csrf, "Content-Type": "application/json"}
        request_headers.update(headers or {})
        connection.request(method, path, payload, request_headers)
        response = connection.getresponse()
        raw = response.read()
        if response.getheader("Set-Cookie"):
            self.cookie = response.getheader("Set-Cookie").split(";", 1)[0]
        result = json.loads(raw) if response.getheader("Content-Type").startswith("application/json") else raw
        status = response.status
        connection.close()
        return status, result

    def state(self):
        status, state = self.request("GET", "/api/state")
        if status == 200:
            self.csrf = state["csrf"]
        return status, state


class AppTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.path = Path(self.temp.name) / "test.sqlite3"
        self.server = Server(("127.0.0.1", 0), self.path, FeishuConfig())
        self.thread = threading.Thread(target=self.server.serve_forever, daemon=True)
        self.thread.start()
        self.admin = Client(self.server.server_port)
        self.assertEqual(self.admin.request("POST", "/api/setup", {
            "name": "管理员", "username": "admin", "password": "admin-password"})[0], 200)
        self.admin.state()

    def tearDown(self):
        self.server.shutdown()
        self.server.server_close()
        self.thread.join()
        self.temp.cleanup()

    def add_member(self, name="张明", username="zhangming"):
        status, result = self.admin.request("POST", "/api/users", {
            "name": name, "username": username, "password": "member-password",
            "department": "研发部", "role": "member", "active": True})
        self.assertEqual(status, 200, result)
        member = Client(self.server.server_port)
        self.assertEqual(member.request("POST", "/api/login", {
            "username": username, "password": "member-password"})[0], 200)
        member.state()
        return result["id"], member

    def add_project(self, members, name="核心平台", owner=1):
        data = {"name": name, "description": "一个测试项目", "owner_id": owner,
                "start_date": "2026-01-01", "due_date": "2026-12-31",
                "priority": "normal", "member_ids": members, "archived": False}
        status, result = self.admin.request("POST", "/api/projects", data)
        self.assertEqual(status, 200, result)
        return result["id"], data

    def upload(self, client, project_id, filename="测试附件.txt", content=b"weekly evidence", week=None, headers=None):
        query = urlencode({"project_id": project_id, "week": week or current_week(), "filename": filename})
        return client.request("POST", "/api/attachments?" + query, content,
            {"Content-Type": "application/octet-stream", **(headers or {})})

    def test_setup_login_and_access(self):
        guest = Client(self.server.server_port)
        self.assertEqual(guest.request("GET", "/api/state")[0], 401)
        self.assertEqual(guest.request("POST", "/api/setup", {
            "name": "其他管理员", "username": "admin2", "password": "password123"})[0], 409)
        self.assertEqual(guest.request("POST", "/api/login", {
            "username": "admin", "password": "incorrect"})[0], 401)
        self.assertEqual(guest.request("GET", "/")[0], 200)
        self.assertEqual(guest.request("GET", "/../app.py")[0], 404)
        self.assertEqual(self.admin.request("POST", "/api/logout", {})[0], 200)
        self.assertEqual(self.admin.state()[0], 401)

    def test_weekly_report_roundtrip_and_validation(self):
        user_id, member = self.add_member()
        project_id, _ = self.add_project([user_id])
        payload = {"week": current_week(), "entries": [{"project_id": project_id,
            "progress": 65.5, "effort": 100, "note": "完成登录接口，开始联调"}]}
        self.assertEqual(member.request("PUT", "/api/reports", payload)[0], 200)
        records = self.admin.state()[1]["reports"]
        self.assertEqual(len(records), 1)
        self.assertEqual((records[0]["user_id"], records[0]["progress"]), (user_id, 65.5))
        payload["entries"][0]["progress"] = 101
        self.assertEqual(member.request("PUT", "/api/reports", payload)[0], 400)
        payload["entries"][0]["progress"] = 70
        payload["entries"][0]["effort"] = 99
        self.assertEqual(member.request("PUT", "/api/reports", payload)[0], 400)
        self.assertEqual(self.admin.state()[1]["reports"][0]["progress"], 65.5)
        payload["entries"][0]["effort"] = 100
        self.assertEqual(member.request("PUT", "/api/reports", payload)[0], 200)
        self.assertEqual(self.admin.state()[1]["reports"][0]["progress"], 70)

    def test_permissions_membership_and_csrf(self):
        user_id, member = self.add_member()
        _, outsider = self.add_member("李华", "lihua")
        project_id, data = self.add_project([user_id])
        self.assertEqual(member.request("POST", "/api/projects", data)[0], 403)
        self.assertEqual(member.request("PUT", f"/api/projects/{project_id}", data)[0], 403)
        self.assertEqual(member.request("GET", "/api/export")[0], 403)
        self.assertEqual(outsider.state()[1]["projects"], [])
        self.assertEqual(outsider.request("PUT", "/api/reports", {"week": current_week(),
            "entries": [{"project_id": project_id, "progress": 20, "effort": 100, "note": "不应接受"}]})[0], 403)
        self.assertEqual(member.request("PUT", "/api/password", {}, {"X-CSRF-Token": "incorrect"})[0], 403)
        self.assertEqual(self.admin.request("POST", "/api/users", {}, {"Origin": "https://evil.example"})[0], 403)

    def test_project_owner_and_archive(self):
        user_id, member = self.add_member()
        project_id, data = self.add_project([user_id], owner=user_id)
        data["name"] = "修改后的项目"
        self.assertEqual(member.request("PUT", f"/api/projects/{project_id}", data)[0], 200)
        data["archived"] = True
        self.assertEqual(member.request("PUT", f"/api/projects/{project_id}", data)[0], 200)
        self.assertEqual(member.request("PUT", "/api/reports", {"week": current_week(),
            "entries": [{"project_id": project_id, "progress": 50, "effort": 100, "note": "新条目"}]})[0], 403)
        data["archived"] = False
        self.assertEqual(self.admin.request("PUT", f"/api/projects/{project_id}", data)[0], 200)

    def test_password_sessions_and_last_admin(self):
        user_id, member = self.add_member()
        second = Client(self.server.server_port)
        self.assertEqual(second.request("POST", "/api/login", {
            "username": "zhangming", "password": "member-password"})[0], 200)
        second.state()
        self.assertEqual(member.request("PUT", "/api/password", {
            "old_password": "member-password", "password": "updated-password"})[0], 200)
        self.assertEqual(second.state()[0], 401)
        self.assertEqual(member.state()[0], 200)
        self.assertEqual(self.admin.request("PUT", "/api/users/1", {
            "name": "管理员", "username": "admin", "department": "", "role": "member", "active": True})[0], 400)
        self.assertEqual(self.admin.request("PUT", f"/api/users/{user_id}", {
            "name": "张明", "username": "zhangming", "department": "研发部", "role": "member", "active": False})[0], 200)
        self.assertEqual(member.state()[0], 401)

    def test_persistence_and_csv_export(self):
        user_id, member = self.add_member()
        project_id, _ = self.add_project([user_id], name="=1+1")
        self.assertEqual(member.request("PUT", "/api/reports", {"week": current_week(),
            "entries": [{"project_id": project_id, "progress": 30, "effort": 100, "note": "=HYPERLINK(example)"}]})[0], 200)
        status, content = self.admin.request("GET", "/api/export")
        self.assertEqual(status, 200)
        self.assertTrue(content.startswith(b"\xef\xbb\xbf"))
        self.assertIn("'=1+1", content.decode("utf-8-sig"))
        replacement = Server(("127.0.0.1", 0), self.path, FeishuConfig())
        try:
            with sqlite3.connect(replacement.db_path) as db:
                self.assertEqual(db.execute("SELECT progress FROM reports").fetchone()[0], 30)
            db.close()
        finally:
            replacement.server_close()

    def test_attachments_publish_download_export_and_remove(self):
        uid, member = self.add_member()
        _, outsider = self.add_member("局外成员", "outside")
        pid, _ = self.add_project([uid])
        status, first = self.upload(member, pid, "设计说明.txt", "中文文件内容".encode())
        self.assertEqual(status, 201, first)
        status, second = self.upload(member, pid, "设计说明.txt", b"different content")
        self.assertEqual(status, 201)
        self.assertNotEqual(first["id"], second["id"])
        self.assertEqual(len(list(self.server.uploads_dir.iterdir())), 2)
        url = f"/api/attachments/{first['id']}"
        self.assertEqual(member.request("GET", url)[1], "中文文件内容".encode())
        self.assertEqual(self.admin.request("GET", url)[0], 403)  # Unsubmitted files are private.
        self.assertEqual(Client(self.server.server_port).request("GET", url)[0], 401)
        payload = {"week": current_week(), "entries": [{"project_id": pid, "progress": 35,
            "effort": 100, "note": "附上方案", "attachment_ids": [first["id"], second["id"]]}]}
        self.assertEqual(member.request("PUT", "/api/reports", payload)[0], 200)
        self.assertEqual(self.admin.request("GET", url)[0], 200)
        exported = self.admin.request("GET", "/api/export")[1].decode("utf-8-sig")
        csv_rows = list(csv.reader(io.StringIO(exported)))
        self.assertEqual(len(csv_rows[0]), len(csv_rows[1]))
        self.assertEqual(csv_rows[1][-1], "设计说明.txt；设计说明.txt")
        self.assertEqual(outsider.request("GET", url)[0], 403)
        self.assertEqual(outsider.state()[1]["attachments"], [])
        self.assertEqual(member.request("GET", "/api/export/attachments")[0], 403)
        status, bundle = self.admin.request("GET", "/api/export/attachments")
        self.assertEqual(status, 200)
        with zipfile.ZipFile(io.BytesIO(bundle)) as archive:
            names = archive.namelist()
            self.assertEqual(len(names), 3)
            self.assertIn("附件清单.csv", names)
            content = archive.read(next(name for name in names if f"/{first['id']}-" in name))
            self.assertEqual(content, "中文文件内容".encode())
            self.assertIn("设计说明.txt", archive.read("附件清单.csv").decode("utf-8-sig"))
        # Legacy report clients do not accidentally discard already attached files.
        payload["entries"][0].pop("attachment_ids")
        self.assertEqual(member.request("PUT", "/api/reports", payload)[0], 200)
        self.assertEqual(len(self.admin.state()[1]["attachments"]), 2)
        payload["entries"][0]["attachment_ids"] = [second["id"]]
        self.assertEqual(member.request("PUT", "/api/reports", payload)[0], 200)
        self.assertEqual(member.request("GET", url)[0], 404)
        self.assertEqual(len(self.admin.state()[1]["attachments"]), 1)
        payload["entries"][0]["attachment_ids"] = []
        self.assertEqual(member.request("PUT", "/api/reports", payload)[0], 200)
        _, replacement = self.upload(member, pid)
        self.assertGreater(replacement["id"], second["id"])
        self.assertEqual(member.request("GET", f"/api/attachments/{second['id']}")[0], 404)

    def test_attachment_authorization_limits_and_atomic_report_updates(self):
        uid, member = self.add_member()
        other_uid, other = self.add_member("其他成员", "otheruser")
        pid, project_data = self.add_project([uid, other_uid])
        another_pid, _ = self.add_project([uid])
        self.assertEqual(self.upload(other, another_pid)[0], 403)
        self.assertEqual(self.upload(member, pid, "../escape.txt")[0], 400)
        self.assertEqual(self.upload(member, pid, "bad\r\nheader.txt")[0], 400)
        self.assertEqual(self.upload(member, pid, content=b"")[0], 413)
        self.assertEqual(self.upload(member, pid, headers={"Content-Length": str(MAX_ATTACHMENT_SIZE+1)})[0], 413)
        self.assertEqual(self.upload(member, pid, headers={"X-CSRF-Token": "wrong"})[0], 403)
        _, own_file = self.upload(member, pid)
        _, foreign_file = self.upload(other, pid)
        payload = {"week": current_week(), "entries": [{"project_id": pid, "progress": 45,
            "effort": 100, "note": "正常记录", "attachment_ids": [own_file["id"]]}]}
        self.assertEqual(member.request("PUT", "/api/reports", payload)[0], 200)
        payload["entries"][0].update(progress=99, attachment_ids=[foreign_file["id"]])
        self.assertEqual(member.request("PUT", "/api/reports", payload)[0], 403)
        state = self.admin.state()[1]
        self.assertEqual(state["reports"][0]["progress"], 45)
        self.assertTrue(next(a for a in state["attachments"] if a["id"] == own_file["id"])["attached"])
        payload["entries"][0]["attachment_ids"] = [own_file["id"]] * 11
        self.assertEqual(member.request("PUT", "/api/reports", payload)[0], 400)
        # Attaching an upload from another week/project is refused.
        payload["entries"][0].update(project_id=another_pid, attachment_ids=[own_file["id"]])
        self.assertEqual(member.request("PUT", "/api/reports", payload)[0], 403)
        project_data["archived"] = True
        self.assertEqual(self.admin.request("PUT", f"/api/projects/{pid}", project_data)[0], 200)
        self.assertEqual(self.upload(member, pid)[0], 201)  # Existing historical entries remain editable.
        self.assertEqual(self.upload(other, pid)[0], 403)

    def test_partial_and_full_week_leave_preserve_members_and_progress(self):
        uid, member = self.add_member()
        pid, _ = self.add_project([uid], owner=uid)
        previous = (date.fromisoformat(current_week()) - timedelta(days=7)).isoformat()
        entry = {"project_id": pid, "progress": 60, "effort": 100, "note": "之前的开发进展"}
        self.assertEqual(member.request("PUT", "/api/reports", {"week": previous, "entries": [entry]})[0], 200)
        partial = {"week": current_week(), "leave_percent": 40, "leave_note": "周四、周五休假",
            "entries": [{**entry, "progress": 70, "effort": 60}]}
        self.assertEqual(member.request("PUT", "/api/reports", partial)[0], 200)
        partial["entries"][0]["effort"] = 100
        self.assertEqual(member.request("PUT", "/api/reports", partial)[0], 400)
        partial["entries"][0]["effort"] = 60
        partial["leave_note"] = ""
        self.assertEqual(member.request("PUT", "/api/reports", partial)[0], 400)
        full = {"week": current_week(), "leave_percent": 100, "leave_note": "整周休假", "entries": []}
        self.assertEqual(member.request("PUT", "/api/reports", full)[0], 200)
        state = self.admin.state()[1]
        submission = next(s for s in state["submissions"] if s["user_id"] == uid and s["week"] == current_week())
        self.assertEqual(submission["leave_percent"], 100)
        self.assertEqual([r["progress"] for r in state["reports"]], [60])
        self.assertIn(uid, state["projects"][0]["member_ids"])
        _, outsider = self.add_member("无关成员", "leaveoutside")
        self.assertEqual(outsider.state()[1]["submissions"], [])
        export = self.admin.request("GET", "/api/export")[1].decode("utf-8-sig")
        self.assertIn("整周休假", export)
        self.assertIn("休假比例", export)
        self.assertEqual(member.request("PUT", "/api/reports", {**full, "leave_percent": 99})[0], 400)
        self.assertEqual(member.request("PUT", "/api/reports", {**full, "entries": [{**entry, "effort": 0}]})[0], 400)
        self.assertEqual(member.request("PUT", "/api/reports", {**full, "leave_percent": True})[0], 400)
        self.assertEqual(member.request("PUT", "/api/reports", {**full, "week": "2099-01-05"})[0], 400)
        # Returning from leave replaces the leave registration with a normal weekly report.
        self.assertEqual(member.request("PUT", "/api/reports", {"week": current_week(), "entries": [entry]})[0], 200)
        self.assertEqual(next(s for s in member.state()[1]["submissions"] if s["week"] == current_week())["leave_percent"], 0)

    def test_old_database_migration_and_attachment_cleanup(self):
        legacy_path = Path(self.temp.name) / "legacy.sqlite3"
        with closing(sqlite3.connect(legacy_path)) as db, db:
            db.execute("CREATE TABLE submissions(user_id INTEGER NOT NULL,week TEXT NOT NULL,updated_at TEXT NOT NULL,PRIMARY KEY(user_id,week))")
            db.execute("INSERT INTO submissions VALUES(1,'2026-01-05','2026-01-09')")
        initialize(legacy_path)
        initialize(legacy_path)
        with closing(sqlite3.connect(legacy_path)) as db, db:
            self.assertEqual(db.execute("SELECT leave_percent,leave_note FROM submissions").fetchone(), (0,""))
        uid, member = self.add_member()
        pid, _ = self.add_project([uid])
        _, file = self.upload(member, pid)
        with closing(sqlite3.connect(self.server.db_path)) as db, db:
            key = db.execute("SELECT storage_key FROM attachments WHERE id=?", (file["id"],)).fetchone()[0]
            db.execute("UPDATE attachments SET staged_at='2000-01-01' WHERE id=?", (file["id"],))
        self.server.cleanup_uploads()
        self.assertFalse((self.server.uploads_dir / key).exists())
        self.assertEqual(member.request("GET", f"/api/attachments/{file['id']}")[0], 404)

    def test_team_connection_burst(self):
        start = threading.Event()

        def visit(_):
            start.wait()
            return Client(self.server.server_port).request("GET", "/api/bootstrap")[0]

        with ThreadPoolExecutor(max_workers=100) as pool:
            pending = [pool.submit(visit, i) for i in range(100)]
            start.set()
            self.assertEqual([f.result() for f in pending], [200] * 100)

    def test_state_gzip_negotiation_preserves_json_and_content_length(self):
        uid, _ = self.add_member()
        self.add_project([uid], name="压缩响应项目")
        expected = self.admin.state()[1]
        for encoding, compressed in [("gzip", True), ("gzip;q=0", False), ("*;q=1,gzip;q=0", False)]:
            connection = http.client.HTTPConnection("127.0.0.1", self.server.server_port, timeout=10)
            try:
                connection.request("GET", "/api/state", headers={"Cookie": self.admin.cookie, "Accept-Encoding": encoding})
                response = connection.getresponse()
                raw = response.read()
                self.assertEqual(response.status, 200)
                self.assertEqual(int(response.getheader("Content-Length")), len(raw))
                self.assertEqual(response.getheader("Vary"), "Accept-Encoding")
                self.assertEqual(response.getheader("Content-Encoding") == "gzip", compressed)
                actual = json.loads(gzip.decompress(raw) if compressed else raw)
                self.assertEqual(actual, expected)
            finally:
                connection.close()

    def test_state_snapshot_observes_external_commits_without_sharing_csrf(self):
        uid, member = self.add_member()
        pid, _ = self.add_project([uid])
        self.assertEqual(member.state()[1]["projects"][0]["name"], "核心平台")
        second = Client(self.server.server_port)
        self.assertEqual(second.request("POST", "/api/login", {"username": "zhangming", "password": "member-password"})[0], 200)
        self.assertNotEqual(second.state()[1]["csrf"], member.state()[1]["csrf"])
        with closing(sqlite3.connect(self.path)) as db, db:
            db.execute("UPDATE projects SET name='外部提交已生效' WHERE id=?", (pid,))
        self.assertEqual(member.state()[1]["projects"][0]["name"], "外部提交已生效")
        # Cached business data must never include another session's CSRF token.
        self.assertNotEqual(second.state()[1]["csrf"], self.admin.state()[1]["csrf"])

    def test_historical_project_visibility_and_private_staged_files(self):
        uid, former = self.add_member()
        peer_id, peer = self.add_member("同项目成员", "sameproject")
        _, outsider = self.add_member("其他项目成员", "unrelated")
        pid, project = self.add_project([uid, peer_id])
        private_pid, _ = self.add_project([], name="不应可见")
        _, file = self.upload(former, pid)
        payload = {"week": current_week(), "entries": [{"project_id": pid, "progress": 40,
            "effort": 100, "note": "历史记录", "attachment_ids": [file["id"]]}]}
        self.assertEqual(former.request("PUT", "/api/reports", payload)[0], 200)
        _, staged = self.upload(peer, pid)
        # Removed members retain access to the project they previously reported on.
        project["member_ids"] = [peer_id]
        self.assertEqual(self.admin.request("PUT", f"/api/projects/{pid}", project)[0], 200)
        state = former.state()[1]
        self.assertEqual([p["id"] for p in state["projects"]], [pid])
        self.assertNotIn(private_pid, [p["id"] for p in state["projects"]])
        self.assertEqual([r["project_id"] for r in state["reports"]], [pid])
        self.assertEqual([a["id"] for a in state["attachments"]], [file["id"]])
        self.assertEqual(former.request("GET", f"/api/attachments/{staged['id']}")[0], 403)
        self.assertEqual(outsider.state()[1]["reports"], [])
        self.assertEqual(outsider.state()[1]["submissions"], [])
        self.assertEqual(former.request("PUT", "/api/reports", payload)[0], 200)

    def report_payload(self, project_id, note, progress=40, week=None):
        return {"week": week or current_week(), "entries": [{
            "project_id": project_id, "progress": progress, "effort": 100, "note": note}]}

    def test_same_week_last_submit_replaces_the_previous_one(self):
        user_id, first = self.add_member()
        project_id, _ = self.add_project([user_id])
        self.assertEqual(first.request("PUT", "/api/reports", self.report_payload(project_id, "窗口一先保存", 20))[0], 200)
        second = Client(self.server.server_port)
        self.assertEqual(second.request("POST", "/api/login", {
            "username": "zhangming", "password": "member-password"})[0], 200)
        second.state()
        self.assertEqual(second.request("PUT", "/api/reports", self.report_payload(project_id, "窗口二后保存", 80))[0], 200)
        reports = [row for row in self.admin.state()[1]["reports"] if row["user_id"] == user_id]
        self.assertEqual(len(reports), 1)
        self.assertEqual((reports[0]["note"], reports[0]["progress"]), ("窗口二后保存", 80))
        self.assertEqual(first.request("PUT", "/api/reports", self.report_payload(project_id, "窗口一再次保存", 35))[0], 200)
        reports = [row for row in self.admin.state()[1]["reports"] if row["user_id"] == user_id]
        self.assertEqual(len(reports), 1)
        self.assertEqual((reports[0]["note"], reports[0]["progress"]), ("窗口一再次保存", 35))

    def test_concurrent_same_week_submits_keep_one_row(self):
        user_id, first = self.add_member()
        project_id, _ = self.add_project([user_id])
        second = Client(self.server.server_port)
        self.assertEqual(second.request("POST", "/api/login", {
            "username": "zhangming", "password": "member-password"})[0], 200)
        second.state()
        gate = threading.Barrier(2)

        def submit(client, note, progress):
            gate.wait(timeout=5)
            return client.request("PUT", "/api/reports", self.report_payload(project_id, note, progress))

        with ThreadPoolExecutor(max_workers=2) as pool:
            pending = [pool.submit(submit, first, "并发甲", 11), pool.submit(submit, second, "并发乙", 22)]
            results = [item.result() for item in pending]
        self.assertEqual([status for status, _ in results], [200, 200])
        reports = [row for row in self.admin.state()[1]["reports"] if row["user_id"] == user_id]
        self.assertEqual(len(reports), 1)
        self.assertIn(reports[0]["note"], {"并发甲", "并发乙"})
        self.assertEqual(len(self.admin.state()[1]["submissions"]), 1)

    def test_future_week_and_duplicate_username_are_rejected(self):
        user_id, member = self.add_member()
        project_id, _ = self.add_project([user_id])
        future = (date.fromisoformat(current_week()) + timedelta(days=7)).isoformat()
        status, body = member.request("PUT", "/api/reports", self.report_payload(project_id, "未来周", week=future))
        self.assertEqual(status, 400)
        self.assertIn("本周或之前", body["error"])
        self.assertEqual(self.admin.state()[1]["reports"], [])
        status, body = self.admin.request("POST", "/api/users", {
            "name": "重名", "username": "zhangming", "password": "member-password",
            "department": "研发部", "role": "member", "active": True})
        self.assertEqual(status, 409)
        self.assertIn("登录名", body["error"])
        self.assertEqual(len(self.admin.state()[1]["users"]), 2)

    def test_logout_keeps_other_sessions_until_password_reset_or_deactivation(self):
        user_id, first = self.add_member()
        project_id, _ = self.add_project([user_id])
        saved = self.report_payload(project_id, "已保存的进展", 10)
        self.assertEqual(first.request("PUT", "/api/reports", saved)[0], 200)
        second = Client(self.server.server_port)
        self.assertEqual(second.request("POST", "/api/login", {
            "username": "zhangming", "password": "member-password"})[0], 200)
        second.state()
        self.assertEqual(first.request("POST", "/api/logout", {})[0], 200)
        self.assertEqual(first.state()[0], 401)
        self.assertEqual(second.state()[0], 200)
        self.assertEqual(self.admin.request("PUT", f"/api/users/{user_id}", {
            "name": "张明", "username": "zhangming", "department": "研发部",
            "role": "member", "active": True, "password": "reset-password"})[0], 200)
        status, _ = second.request("PUT", "/api/reports", self.report_payload(project_id, "重置后不应写入", 99))
        self.assertEqual(status, 401)
        report = self.admin.state()[1]["reports"][0]
        self.assertEqual((report["note"], report["progress"]), ("已保存的进展", 10))
        fresh = Client(self.server.server_port)
        self.assertEqual(fresh.request("POST", "/api/login", {
            "username": "zhangming", "password": "reset-password"})[0], 200)
        fresh.state()
        self.assertEqual(self.admin.request("PUT", f"/api/users/{user_id}", {
            "name": "张明", "username": "zhangming", "department": "研发部",
            "role": "member", "active": False})[0], 200)
        status, _ = fresh.request("PUT", "/api/reports", self.report_payload(project_id, "停用后不应写入", 99))
        self.assertEqual(status, 401)
        report = self.admin.state()[1]["reports"][0]
        self.assertEqual((report["note"], report["progress"]), ("已保存的进展", 10))


if __name__ == "__main__":
    unittest.main()
