"""飞书接口模拟测试；不连接真实企业、不需要凭证。"""
import copy
import hashlib
import sqlite3
import tempfile
import threading
import unittest
from pathlib import Path
from urllib.parse import parse_qs, urlparse
from unittest.mock import patch

from app import Handler, Server, connect, initialize
from feishu import API_BASE, TOKEN_URL, FeishuClient, FeishuConfig, FeishuError, FeishuIntegration
from test_app import Client


CONFIG = FeishuConfig(mode="feishu", app_id="cli_test_app", app_secret="test-only-secret",
    redirect_uri="http://127.0.0.1/api/auth/feishu/callback", admin_open_ids=("ou_admin",))


def raw_user(identity, name="研发成员", depts=None, active=True):
    return {"open_id": identity, "name": name, "department_ids": depts or ["od-dev"],
        "status": {"is_activated": True, "is_frozen": False, "is_resigned": not active, "is_exited": False},
        "avatar": {"avatar_72": "https://example.test/avatar"}, "email": "DO-NOT-PERSIST@example.test"}


class FakeClient(FeishuClient):
    def __init__(self):
        super().__init__(CONFIG)
        self.people = [self.normalize_user(raw_user("ou_admin", "管理员")),
                       self.normalize_user(raw_user("ou_member", "项目成员"))]
        self.fail = False

    def directory(self):
        if self.fail:
            raise FeishuError("模拟分页中断")
        return [{"id": "0", "name": "公司", "parent_id": ""},
                {"id": "od-dev", "name": "研发部", "parent_id": "0"}], copy.deepcopy(self.people)

    def login_identity(self, code, verifier):
        if code == "invalid" or not verifier:
            raise FeishuError("模拟授权失败")
        return "ou_member" if code == "member-code" else "ou_admin"


class FeishuTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.path = Path(self.temp.name) / "feishu.sqlite3"
        initialize(self.path)
        self.client = FakeClient()
        self.integration = FeishuIntegration(CONFIG, self.path, connect, self.client)

    def tearDown(self):
        self.temp.cleanup()

    def user(self, identity):
        with connect(self.path) as db:
            row = db.execute("SELECT * FROM users WHERE feishu_open_id=?", (identity,)).fetchone()
            return dict(row) if row else None

    def test_mirror_upsert_department_changes_roles_and_reactivation(self):
        self.integration.sync()
        admin, member = self.user("ou_admin"), self.user("ou_member")
        self.assertEqual(admin["role"], "admin")
        self.assertEqual(member["auth_source"], "feishu")
        self.assertEqual(member["password"], "!feishu-only")
        self.assertEqual(member["department"], "研发部")
        with connect(self.path) as db:
            db.execute("UPDATE users SET role='admin' WHERE id=?", (member["id"],))
        self.client.people[1]["name"] = "成员新姓名"
        self.client.people[1]["active"] = False
        self.integration.sync()
        updated = self.user("ou_member")
        self.assertEqual((updated["id"], updated["role"], updated["active"]), (member["id"], "admin", 0))
        self.assertEqual(updated["name"], "成员新姓名")
        self.client.people[1]["active"] = True
        self.integration.sync()
        self.assertEqual(self.user("ou_member")["active"], 1)
        self.assertNotIn("DO-NOT-PERSIST", self.path.read_bytes().decode("latin1"))

    def test_failure_never_overwrites_mirror_and_missing_revokes_session(self):
        self.integration.sync()
        member = self.user("ou_member")
        with connect(self.path) as db:
            db.execute("INSERT INTO sessions VALUES ('token', ?, 'csrf', 9999999999)", (member["id"],))
        self.client.fail = True
        with self.assertRaises(FeishuError):
            self.integration.sync()
        self.assertEqual(self.user("ou_member")["active"], 1)
        self.assertEqual(self.integration.status()["latest"]["status"], "failed")
        self.client.fail = False
        self.client.people = self.client.people[:1]
        self.integration.sync()
        self.assertEqual(self.user("ou_member")["employment_status"], "not_in_directory")
        with connect(self.path) as db:
            self.assertEqual(db.execute("SELECT COUNT(*) FROM sessions").fetchone()[0], 0)

    def test_empty_snapshot_and_different_app_abort(self):
        self.integration.sync()
        self.client.people = []
        with self.assertRaises(FeishuError):
            self.integration.sync()
        self.assertEqual(self.user("ou_admin")["active"], 1)
        config = FeishuConfig(mode="feishu", app_id="other-app")
        other = FeishuIntegration(config, self.path, connect, FakeClient())
        with self.assertRaises(FeishuError):
            other.sync()
        self.assertEqual(self.user("ou_admin")["feishu_app_id"], CONFIG.app_id)

    def test_oauth_pkce_browser_binding_expiration_and_replay(self):
        url, cookie = self.integration.create_oauth()
        params = parse_qs(urlparse(url).query)
        state = params["state"][0]
        self.assertEqual(params["code_challenge_method"], ["S256"])
        self.assertNotIn(CONFIG.app_secret, url)
        with self.assertRaises(FeishuError):
            self.integration.consume_oauth(state, "other-browser")
        verifier = self.integration.consume_oauth(state, cookie)
        self.assertGreaterEqual(len(verifier), 43)
        with self.assertRaises(FeishuError):
            self.integration.consume_oauth(state, cookie)
        url, cookie = self.integration.create_oauth()
        with connect(self.path) as db:
            db.execute("UPDATE feishu_oauth_states SET expires=0")
        with self.assertRaises(FeishuError):
            self.integration.consume_oauth(parse_qs(urlparse(url).query)["state"][0], cookie)

    def test_directory_pagination_100_people_and_multidepartment_dedup(self):
        client = FeishuClient(CONFIG)
        calls = []
        def get(path, params):
            calls.append((path, dict(params)))
            if path.endswith("children"):
                return {"items": [{"open_department_id": "od-dev", "name": "研发", "parent_department_id": "0"},
                    {"open_department_id": "od-other", "name": "产品", "parent_department_id": "0"}], "has_more": False}
            dept = params["department_id"]
            if dept == "0":
                return {"items": [], "has_more": False}
            if dept == "od-other":
                return {"items": [raw_user("ou_0", depts=["od-dev", "od-other"])], "has_more": False}
            offset = 50 if params.get("page_token") else 0
            return {"items": [raw_user(f"ou_{i}") for i in range(offset,offset+50)],
                    "has_more": offset == 0, "page_token": "page2" if offset == 0 else ""}
        with patch.object(client, "get", side_effect=get):
            departments, users = client.directory()
        self.assertEqual(len(users), 100)
        self.assertEqual(len(departments), 3)
        self.assertEqual(next(u for u in users if u["open_id"] == "ou_0")["department_ids"], ["od-dev", "od-other"])
        self.assertTrue(any(params.get("page_token") == "page2" for _,params in calls))
        self.assertTrue(all(params.get("user_id_type") == "open_id" for _,params in calls))

    def test_repeating_page_and_missing_status_rejected(self):
        client = FeishuClient(CONFIG)
        with patch.object(client, "get", return_value={"items": [], "has_more": True, "page_token": "same"}):
            with self.assertRaises(FeishuError):
                list(client.pages("/test", {}))
        user = raw_user("ou_x")
        user.pop("status")
        with self.assertRaises(FeishuError):
            client.normalize_user(user)

    def test_expired_tenant_token_refreshes_once(self):
        import time
        client = FeishuClient(CONFIG)
        client._token, client._expires = "old-token", time.monotonic() + 1000
        calls = []
        def request(url, data=None, token=None, form=False):
            calls.append((url,token))
            if token == "old-token":
                raise FeishuError("token expired", 99991663)
            if url.endswith("tenant_access_token/internal"):
                return {"tenant_access_token": "new-token", "expire": 7200}
            return {"data": {"items": [], "has_more": False}}
        with patch.object(client, "_request", side_effect=request):
            self.assertEqual(client.get("/test")["items"], [])
        self.assertEqual([token for _,token in calls], ["old-token", None, "new-token"])

    def test_first_sync_requires_configured_admin_and_atomic_updates(self):
        self.client.people = self.client.people[1:]
        with self.assertRaises(FeishuError):
            self.integration.sync()
        self.assertIsNone(self.user("ou_member"))
        self.client = FakeClient()
        integration = FeishuIntegration(CONFIG, self.path, connect, self.client)
        integration.sync()
        self.client.people[0]["name"] = "This change must roll back"
        self.client.people[1]["department_ids"] = ["missing-department"]
        with self.assertRaises(FeishuError):
            integration.sync()
        self.assertEqual(self.user("ou_admin")["name"], "管理员")

    def test_oauth_uses_v3_form_and_does_not_store_user_token(self):
        client = FeishuClient(CONFIG)
        calls = []
        def request(url, data=None, token=None, form=False):
            calls.append((url, data, token, form))
            return {"access_token": "one-time-user-token"} if url == TOKEN_URL else {"data": {"open_id": "ou_member"}}
        with patch.object(client, "_request", side_effect=request), patch.object(client, "get", return_value={"user": raw_user("ou_member")}):
            self.assertEqual(client.login_identity("code", "verifier"), "ou_member")
        self.assertEqual(calls[0][0], "https://accounts.feishu.cn/oauth/v3/token")
        self.assertTrue(calls[0][3])
        self.assertEqual(calls[0][1]["code_verifier"], "verifier")
        self.assertEqual(client._token, "")

    def test_http_feishu_login_manual_sync_and_readonly_personnel(self):
        with patch.object(Handler, "log_message", lambda *_: None):
            server = Server(("127.0.0.1",0), self.path, CONFIG, self.client)
            thread = threading.Thread(target=server.serve_forever, daemon=True)
            thread.start()
            try:
                server.feishu.sync()
                admin = Client(server.server_port)
                bootstrap = admin.request("GET", "/api/bootstrap")[1]
                self.assertFalse(bootstrap["needs_setup"])
                self.assertEqual(bootstrap["auth_mode"], "feishu")
                self.assertEqual(admin.request("POST", "/api/setup", {})[0], 403)
                self.assertEqual(admin.request("POST", "/api/login", {})[0], 403)
                url, cookie = server.feishu.create_oauth()
                state = parse_qs(urlparse(url).query)["state"][0]
                admin.cookie = "pms_oauth=" + cookie
                callback = "/api/auth/feishu/callback?code=admin-code&state=" + state
                self.assertEqual(admin.request("GET", callback)[0], 303)
                self.assertEqual(admin.state()[0], 200)
                self.assertEqual(admin.state()[1]["user"]["role"], "admin")
                self.assertEqual(admin.request("POST", "/api/users", {})[0], 403)
                self.assertEqual(admin.request("PUT", "/api/users/1", {})[0], 403)
                self.assertEqual(admin.request("PUT", "/api/password", {})[0], 403)
                self.assertEqual(admin.request("POST", "/api/feishu/sync", {})[0], 200)
                user_id = self.user("ou_member")["id"]
                self.assertEqual(admin.request("PUT", f"/api/users/{user_id}/role", {"role": "admin"})[0], 200)
                self.assertEqual(admin.request("PUT", "/api/users/1/role", {"role": "member"})[0], 400)
                # Replayed callbacks cannot issue an authenticated session.
                other = Client(server.server_port)
                other.cookie = "pms_oauth=" + cookie
                self.assertEqual(other.request("GET", callback)[0], 303)
                self.assertEqual(other.state()[0], 401)
                # Disabled users lose existing authenticated sessions on the next successful sync.
                self.client.people[0]["active"] = False
                server.feishu.sync()
                self.assertEqual(admin.state()[0], 401)
            finally:
                server.shutdown(); server.server_close(); thread.join()


if __name__ == "__main__":
    unittest.main()
