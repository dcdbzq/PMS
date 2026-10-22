"""飞书只读通讯录镜像及 OAuth 连接；密钥和访问令牌仅留在服务端。"""
from __future__ import annotations

import base64
import hashlib
import json
import os
import secrets
import threading
import time
from dataclasses import dataclass, field
from datetime import datetime, timedelta, timezone
from urllib.error import HTTPError, URLError
from urllib.parse import urlencode
from urllib.request import Request, urlopen

API_BASE = "https://open.feishu.cn/open-apis"
AUTHORIZE_URL = "https://accounts.feishu.cn/open-apis/authen/v1/authorize"
TOKEN_URL = "https://accounts.feishu.cn/oauth/v3/token"


class FeishuError(Exception):
    def __init__(self, message, code=None):
        super().__init__(message)
        self.code = code


def now():
    return datetime.now(timezone(timedelta(hours=8))).isoformat(timespec="seconds")


def load_env(path):
    """读取简单 KEY=VALUE 配置；现有进程环境变量优先，不执行插值或脚本。"""
    if not path.is_file():
        return
    for line in path.read_text(encoding="utf-8-sig").splitlines():
        line = line.strip()
        if not line or line.startswith("#"):
            continue
        key, separator, value = line.partition("=")
        key, value = key.strip(), value.strip()
        if not separator or not key.startswith(("PMS_", "FEISHU_")):
            continue
        if len(value) >= 2 and value[0] == value[-1] and value[0] in "\"'":
            value = value[1:-1]
        os.environ.setdefault(key, value)


@dataclass(frozen=True)
class FeishuConfig:
    mode: str = "local"
    app_id: str = ""
    app_secret: str = field(default="", repr=False)
    redirect_uri: str = ""
    admin_open_ids: tuple[str, ...] = ()
    tenant_key: str = ""
    sync_interval: int = 900

    @classmethod
    def from_env(cls):
        from urllib.parse import urlparse
        mode = os.environ.get("PMS_AUTH_MODE", "local").strip()
        if mode not in ("local", "feishu"):
            raise ValueError("PMS_AUTH_MODE 只支持 local 或 feishu")
        if mode == "local":
            return cls()
        config = cls(mode=mode, app_id=os.environ.get("FEISHU_APP_ID", "").strip(),
            app_secret=os.environ.get("FEISHU_APP_SECRET", "").strip(),
            redirect_uri=os.environ.get("FEISHU_REDIRECT_URI", "").strip(),
            admin_open_ids=tuple(v.strip() for v in os.environ.get("FEISHU_ADMIN_OPEN_IDS", "").split(",") if v.strip()),
            tenant_key=os.environ.get("FEISHU_TENANT_KEY", "").strip(),
            sync_interval=int(os.environ.get("FEISHU_SYNC_INTERVAL", "900")))
        if not all((config.app_id, config.app_secret, config.redirect_uri, config.admin_open_ids)):
            raise ValueError("飞书模式需要 FEISHU_APP_ID、FEISHU_APP_SECRET、FEISHU_REDIRECT_URI 和 FEISHU_ADMIN_OPEN_IDS")
        parsed = urlparse(config.redirect_uri)
        if parsed.path != "/api/auth/feishu/callback" or parsed.query or parsed.fragment or parsed.username or parsed.password:
            raise ValueError("FEISHU_REDIRECT_URI 须为完整的 /api/auth/feishu/callback 地址，不带查询参数或片段")
        if parsed.scheme != "https" and not (parsed.scheme == "http" and parsed.hostname in ("localhost", "127.0.0.1")):
            raise ValueError("飞书回调地址须使用 HTTPS；仅 localhost/127.0.0.1 开发环境允许 HTTP")
        if not 60 <= config.sync_interval <= 86400:
            raise ValueError("FEISHU_SYNC_INTERVAL 须为 60–86400 秒")
        return config


class FeishuClient:
    def __init__(self, config):
        self.config = config
        self._token = ""
        self._expires = 0
        self._token_lock = threading.Lock()
        self._request_lock = threading.Lock()
        self._last_request = 0

    def _request(self, url, data=None, token=None, form=False):
        headers = {"Content-Type": "application/x-www-form-urlencoded" if form else "application/json; charset=utf-8"}
        if token:
            headers["Authorization"] = f"Bearer {token}"
        body = (urlencode(data).encode() if form else json.dumps(data).encode()) if data is not None else None
        for attempt in range(3):
            # Bound request rate independently from the number of departments/pages.
            with self._request_lock:
                delay = .12 - (time.monotonic() - self._last_request)
                if delay > 0:
                    time.sleep(delay)
                self._last_request = time.monotonic()
            try:
                with urlopen(Request(url, body, headers), timeout=15) as response:
                    raw = response.read(4_000_001)
                if len(raw) > 4_000_000:
                    raise FeishuError("飞书响应过大，同步已中止")
                result = json.loads(raw)
                if not isinstance(result, dict):
                    raise ValueError()
                if result.get("code", 0) != 0 or result.get("error"):
                    # Do not persist upstream text: it can include tokens and request details.
                    code = result.get("code") if isinstance(result.get("code"), int) else None
                    raise FeishuError(f"飞书接口调用失败（错误码 {code if code is not None else 'unknown'}），请检查应用权限、数据范围与发布状态", code)
                return result
            except HTTPError as exc:
                if (exc.code == 429 or exc.code >= 500) and attempt < 2:
                    exc.close()
                    time.sleep(attempt + 1)
                    continue
                status = exc.code
                code = None
                try:
                    result = json.loads(exc.read(16384))
                    code = result.get("code") if isinstance(result, dict) and isinstance(result.get("code"), int) else None
                except (ValueError, OSError):
                    pass
                exc.close()
                raise FeishuError(f"飞书请求失败（HTTP {status}，错误码 {code if code is not None else 'unknown'}），请检查应用权限、数据范围与配置", code) from None
            except (URLError, TimeoutError, OSError):
                if attempt < 2:
                    time.sleep(attempt + 1)
                    continue
                raise FeishuError("暂时无法连接飞书，现有通讯录数据未变更") from None
            except (ValueError, UnicodeDecodeError):
                raise FeishuError("飞书响应格式异常，现有通讯录数据未变更") from None

    def tenant_token(self):
        with self._token_lock:
            if self._token and time.monotonic() < self._expires:
                return self._token
            result = self._request(API_BASE + "/auth/v3/tenant_access_token/internal", {
                "app_id": self.config.app_id, "app_secret": self.config.app_secret})
            token = result.get("tenant_access_token")
            expires = result.get("expire")
            if not isinstance(token, str) or not isinstance(expires, (int, float)) or expires <= 0:
                raise FeishuError("飞书未返回有效的应用凭证，请检查应用配置")
            self._token, self._expires = token, time.monotonic() + max(1, expires - 60)
            return token

    def get(self, path, params=None):
        url = API_BASE + path + ("?" + urlencode(params) if params else "")
        token = self.tenant_token()
        try:
            result = self._request(url, token=token)
        except FeishuError as exc:
            if exc.code != 99991663:
                raise
            with self._token_lock:
                if self._token == token:
                    self._expires = 0
            result = self._request(url, token=self.tenant_token())
        if not isinstance(result.get("data"), dict):
            raise FeishuError("飞书未返回完整的数据，现有通讯录数据未变更")
        return result["data"]

    def pages(self, path, params):
        params = {**params, "page_size": 50}
        seen = set()
        for _ in range(1000):
            data = self.get(path, params)
            if not isinstance(data.get("items"), list) or not isinstance(data.get("has_more"), bool):
                raise FeishuError("飞书分页信息不完整，同步已中止")
            yield from data["items"]
            if not data["has_more"]:
                return
            token = data.get("page_token")
            if not isinstance(token, str) or not token or token in seen:
                raise FeishuError("飞书分页标记异常，同步已中止")
            seen.add(token)
            params["page_token"] = token
        raise FeishuError("通讯录页数超出限制，同步已中止")

    @staticmethod
    def normalize_user(raw):
        status = raw.get("status")
        if not isinstance(raw.get("open_id"), str) or not raw["open_id"] or not isinstance(raw.get("name"), str) or not raw["name"].strip():
            raise FeishuError("飞书人员缺少 Open ID 或姓名，请检查字段读取权限")
        if not isinstance(status, dict) or not isinstance(status.get("is_activated"), bool):
            raise FeishuError("飞书未返回完整的人员状态，请检查用户基本信息及状态字段权限")
        for key in ("is_frozen", "is_resigned", "is_exited"):
            if not isinstance(status.get(key), bool):
                raise FeishuError("飞书未返回完整的人员状态，同步已中止")
        active = status["is_activated"] and not any(status[k] for k in ("is_frozen", "is_resigned", "is_exited")) and not status.get("is_unjoin", False)
        departments = raw.get("department_ids")
        if not isinstance(departments, list) or not all(isinstance(item, str) for item in departments):
            raise FeishuError("飞书未返回人员所属部门，请检查用户组织架构信息读取权限")
        return {"open_id": raw["open_id"], "name": raw["name"].strip(), "active": bool(active),
            "department_ids": departments, "avatar_url": (raw.get("avatar") or {}).get("avatar_72", ""),
            "employment_status": "active" if active else "inactive"}

    def directory(self):
        # Root user listing verifies FULL company directory access, separately from app availability.
        common = {"user_id_type": "open_id", "department_id_type": "open_department_id"}
        root_users = list(self.pages("/contact/v3/users/find_by_department", {**common, "department_id": "0"}))
        raw_departments = list(self.pages("/contact/v3/departments/0/children", {**common, "fetch_child": "true"}))
        departments = {"0": {"id": "0", "name": "公司", "parent_id": ""}}
        for item in raw_departments:
            identity = item.get("open_department_id")
            if not isinstance(identity, str) or not identity or not item.get("name"):
                raise FeishuError("飞书部门信息不完整，同步已中止")
            departments[identity] = {"id": identity, "name": item["name"], "parent_id": item.get("parent_department_id", "0")}
        users = {}
        def consume(rows):
            for raw in rows:
                user = self.normalize_user(raw)
                if user["open_id"] in users:
                    user["department_ids"] = sorted(set(user["department_ids"]) | set(users[user["open_id"]]["department_ids"]))
                users[user["open_id"]] = user
        consume(root_users)
        for identity in departments:
            if identity != "0":
                consume(self.pages("/contact/v3/users/find_by_department", {**common, "department_id": identity}))
        for user in users.values():
            if any(identity not in departments for identity in user["department_ids"]):
                raise FeishuError("人员所属部门不在本次完整部门列表中，请稍后重试")
        return list(departments.values()), list(users.values())

    def login_identity(self, code, verifier):
        result = self._request(TOKEN_URL, {"grant_type": "authorization_code", "client_id": self.config.app_id,
            "client_secret": self.config.app_secret, "code": code, "redirect_uri": self.config.redirect_uri,
            "code_verifier": verifier}, form=True)
        token = result.get("access_token")
        if not isinstance(token, str) or not token:
            raise FeishuError("飞书登录凭证无效，请重新登录")
        info = self._request(API_BASE + "/authen/v1/user_info", token=token).get("data", {})
        # The user token is deliberately not stored; directory polling uses only the application token.
        if not isinstance(info.get("open_id"), str) or not info["open_id"]:
            raise FeishuError("飞书未返回有效的登录身份")
        if self.config.tenant_key and info.get("tenant_key") != self.config.tenant_key:
            raise FeishuError("该账号不属于配置的飞书企业")
        raw = self.get("/contact/v3/users/" + info["open_id"],
            {"user_id_type": "open_id", "department_id_type": "open_department_id"}).get("user")
        if not isinstance(raw, dict):
            raise FeishuError("无法核实飞书人员状态，请联系管理员")
        current = self.normalize_user(raw)
        if current["open_id"] != info["open_id"] or not current["active"]:
            raise FeishuError("账号已离职、冻结或尚未激活，不能登录")
        return current["open_id"]


class FeishuIntegration:
    def __init__(self, config, db_path, connect, client=None):
        self.config, self.db_path, self.connect = config, db_path, connect
        self.client = client or FeishuClient(config)
        self._lock = threading.Lock()
        self._stop = threading.Event()
        self._thread = None

    def status(self):
        with self.connect(self.db_path) as db:
            latest = db.execute("SELECT * FROM feishu_sync_runs ORDER BY id DESC LIMIT 1").fetchone()
            success = db.execute("SELECT finished_at FROM feishu_sync_runs WHERE status = 'success' ORDER BY id DESC LIMIT 1").fetchone()
            count = db.execute("SELECT COUNT(*) FROM users WHERE auth_source = 'feishu' AND feishu_app_id = ?", (self.config.app_id,)).fetchone()[0]
        return {"auth_mode": "feishu", "sync_interval": self.config.sync_interval,
            "last_success": success[0] if success else None, "latest": dict(latest) if latest else None,
            "member_count": count, "running": self._lock.locked()}

    def sync(self):
        if not self._lock.acquire(blocking=False):
            raise FeishuError("通讯录正在同步，请稍后刷新查看结果")
        run_id = None
        try:
            with self.connect(self.db_path) as db:
                run_id = db.execute("INSERT INTO feishu_sync_runs(started_at, status) VALUES (?, 'running')", (now(),)).lastrowid
            # Complete and validate the remote snapshot BEFORE beginning any mirror write.
            departments, users = self.client.directory()
            if not users:
                raise FeishuError("本次通讯录为空，未覆盖现有数据，请检查全员通讯录读取范围")
            dept_names = {item["id"]: item["name"] for item in departments}
            seen, created, updated = set(), 0, 0
            stamp = now()
            with self.connect(self.db_path) as db:
                db.execute("BEGIN IMMEDIATE")
                other = db.execute("SELECT 1 FROM users WHERE auth_source = 'feishu' AND feishu_app_id != ? LIMIT 1", (self.config.app_id,)).fetchone()
                if other:
                    raise FeishuError("检测到不同 App ID 的人员镜像；更换飞书应用需要明确迁移身份，不能自动合并")
                has_mirror = db.execute("SELECT 1 FROM users WHERE auth_source='feishu' LIMIT 1").fetchone()
                if not has_mirror and not any(u["active"] and u["open_id"] in self.config.admin_open_ids for u in users):
                    raise FeishuError("首次同步未找到配置的启用管理员，请检查 FEISHU_ADMIN_OPEN_IDS 是否来自当前应用")
                db.execute("DELETE FROM feishu_departments WHERE app_id = ?", (self.config.app_id,))
                db.executemany("INSERT INTO feishu_departments VALUES (?, ?, ?, ?, ?)",
                    [(self.config.app_id, d["id"], d["name"], d["parent_id"], stamp) for d in departments])
                for item in users:
                    identity = item["open_id"]
                    seen.add(identity)
                    old = db.execute("SELECT id, role FROM users WHERE feishu_app_id = ? AND feishu_open_id = ?", (self.config.app_id, identity)).fetchone()
                    role = "admin" if identity in self.config.admin_open_ids else old["role"] if old else "member"
                    department = " / ".join(dept_names[d] for d in item["department_ids"])
                    fields = (item["name"], department, int(item["active"]), role, item["avatar_url"],
                        json.dumps(item["department_ids"]), item["employment_status"], stamp)
                    if old:
                        db.execute("UPDATE users SET name=?, department=?, active=?, role=?, avatar_url=?, feishu_department_ids=?, employment_status=?, synced_at=? WHERE id=?", (*fields, old["id"]))
                        updated += 1
                    else:
                        username = "fs_" + hashlib.sha256(f"{self.config.app_id}:{identity}".encode()).hexdigest()[:24]
                        db.execute("""INSERT INTO users(name, department, active, role, avatar_url, feishu_department_ids,
                            employment_status, synced_at, username, password, auth_source, feishu_app_id, feishu_open_id, created_at)
                            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, '!feishu-only', 'feishu', ?, ?, ?)""",
                            (*fields, username, self.config.app_id, identity, stamp))
                        created += 1
                missing = []
                for row in db.execute("SELECT id, feishu_open_id FROM users WHERE auth_source='feishu' AND feishu_app_id=?", (self.config.app_id,)):
                    if row["feishu_open_id"] not in seen:
                        missing.append(row["id"])
                db.executemany("UPDATE users SET active=0, employment_status='not_in_directory', synced_at=? WHERE id=?", [(stamp, identity) for identity in missing])
                db.execute("DELETE FROM sessions WHERE user_id IN (SELECT id FROM users WHERE auth_source='feishu' AND active=0)")
                db.execute("UPDATE feishu_sync_runs SET status='success', finished_at=?, users_count=?, departments_count=?, message=? WHERE id=?",
                    (stamp, len(users), len(departments)-1, f"新增 {created} 人，更新 {updated} 人，不在本次完整范围 {len(missing)} 人", run_id))
            return self.status()
        except Exception as exc:
            message = str(exc) if isinstance(exc, FeishuError) else "同步处理失败，现有通讯录数据未变更"
            if run_id:
                with self.connect(self.db_path) as db:
                    db.execute("UPDATE feishu_sync_runs SET status='failed', finished_at=?, message=? WHERE id=?", (now(), message, run_id))
            raise FeishuError(message) from None
        finally:
            self._lock.release()

    def start(self):
        if self._thread:
            return
        def work():
            while not self._stop.is_set():
                try:
                    self.sync()
                except FeishuError as exc:
                    print(f"飞书通讯录同步：{exc}", flush=True)
                self._stop.wait(self.config.sync_interval)
        self._thread = threading.Thread(target=work, name="feishu-directory-sync", daemon=True)
        self._thread.start()

    def stop(self):
        self._stop.set()

    def create_oauth(self):
        state, browser, verifier = (secrets.token_urlsafe(48) for _ in range(3))
        challenge = base64.urlsafe_b64encode(hashlib.sha256(verifier.encode()).digest()).decode().rstrip("=")
        with self.connect(self.db_path) as db:
            db.execute("DELETE FROM feishu_oauth_states WHERE expires < ?", (time.time(),))
            db.execute("INSERT INTO feishu_oauth_states VALUES (?, ?, ?, ?)",
                (hashlib.sha256(state.encode()).hexdigest(), hashlib.sha256(browser.encode()).hexdigest(), verifier, time.time()+600))
        query = {"client_id": self.config.app_id, "redirect_uri": self.config.redirect_uri, "response_type": "code",
            "state": state, "code_challenge": challenge, "code_challenge_method": "S256"}
        return AUTHORIZE_URL + "?" + urlencode(query), browser

    def consume_oauth(self, state, browser):
        if not state or not browser or len(state) > 256 or len(browser) > 256:
            raise FeishuError("登录校验失败，请重新发起飞书登录")
        with self.connect(self.db_path) as db:
            db.execute("BEGIN IMMEDIATE")
            digest = hashlib.sha256(state.encode()).hexdigest()
            row = db.execute("SELECT * FROM feishu_oauth_states WHERE state_hash=? AND browser_hash=? AND expires>?",
                (digest, hashlib.sha256(browser.encode()).hexdigest(), time.time())).fetchone()
            if not row:
                raise FeishuError("登录校验已失效，请重新发起飞书登录")
            db.execute("DELETE FROM feishu_oauth_states WHERE state_hash=?", (digest,))
        return row["verifier"]
