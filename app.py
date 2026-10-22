"""轻舟：仅使用 Python 标准库的轻量项目进度系统。"""

from __future__ import annotations

import argparse
import csv
import gzip
import hashlib
import hmac
import io
import json
import math
import mimetypes
import os
import re
import secrets
import sqlite3
import shutil
import tempfile
import threading
import time
import zipfile
from contextlib import contextmanager
from datetime import date, datetime, timedelta, timezone
from http.cookies import SimpleCookie
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.parse import parse_qs, quote, urlparse

from feishu import FeishuConfig, FeishuError, FeishuIntegration, load_env
import domain

ROOT = Path(__file__).resolve().parent
SHANGHAI = timezone(timedelta(hours=8))
SESSION_AGE = 7 * 24 * 60 * 60
USERNAME = re.compile(r"^[a-zA-Z0-9_.-]{3,32}$")
MAX_ATTACHMENT_SIZE = 20 * 1024 * 1024
MAX_ATTACHMENTS = 10


def today():
    return datetime.now(SHANGHAI).date()


def current_week():
    value = today()
    return (value - timedelta(days=value.weekday())).isoformat()


def timestamp():
    return datetime.now(SHANGHAI).isoformat(timespec="seconds")


def hash_password(password, salt=None):
    salt = salt or secrets.token_hex(16)
    digest = hashlib.pbkdf2_hmac("sha256", password.encode(), bytes.fromhex(salt), 260000)
    return f"{salt}${digest.hex()}"


def check_password(password, stored):
    salt, _ = stored.split("$", 1)
    return hmac.compare_digest(hash_password(password, salt), stored)


@contextmanager
def connect(path):
    db = sqlite3.connect(path, timeout=15)
    db.row_factory = sqlite3.Row
    db.execute("PRAGMA foreign_keys = ON")
    try:
        with db:
            yield db
    finally:
        db.close()


def initialize(path):
    Path(path).parent.mkdir(parents=True, exist_ok=True)
    with connect(path) as db:
        db.execute("PRAGMA journal_mode = WAL")
        db.executescript("""
            CREATE TABLE IF NOT EXISTS users (
                id INTEGER PRIMARY KEY, name TEXT NOT NULL,
                username TEXT NOT NULL UNIQUE COLLATE NOCASE,
                password TEXT NOT NULL, role TEXT NOT NULL DEFAULT 'member',
                department TEXT NOT NULL DEFAULT '', active INTEGER NOT NULL DEFAULT 1,
                created_at TEXT NOT NULL
            );
            CREATE TABLE IF NOT EXISTS projects (
                id INTEGER PRIMARY KEY, name TEXT NOT NULL, description TEXT NOT NULL DEFAULT '',
                owner_id INTEGER NOT NULL REFERENCES users(id), start_date TEXT NOT NULL,
                due_date TEXT NOT NULL, priority TEXT NOT NULL DEFAULT 'normal',
                archived INTEGER NOT NULL DEFAULT 0, created_at TEXT NOT NULL, updated_at TEXT NOT NULL
            );
            CREATE TABLE IF NOT EXISTS memberships (
                project_id INTEGER NOT NULL REFERENCES projects(id),
                user_id INTEGER NOT NULL REFERENCES users(id),
                PRIMARY KEY (project_id, user_id)
            );
            CREATE TABLE IF NOT EXISTS reports (
                id INTEGER PRIMARY KEY, project_id INTEGER NOT NULL REFERENCES projects(id),
                user_id INTEGER NOT NULL REFERENCES users(id), week TEXT NOT NULL,
                progress REAL NOT NULL CHECK(progress BETWEEN 0 AND 100),
                effort REAL NOT NULL CHECK(effort BETWEEN 0 AND 100),
                note TEXT NOT NULL, updated_at TEXT NOT NULL,
                UNIQUE(project_id, user_id, week)
            );
            CREATE INDEX IF NOT EXISTS reports_week ON reports(week, user_id);
            CREATE INDEX IF NOT EXISTS reports_user_project ON reports(user_id, project_id);
            CREATE INDEX IF NOT EXISTS memberships_user_project ON memberships(user_id, project_id);
            CREATE TABLE IF NOT EXISTS submissions (
                user_id INTEGER NOT NULL REFERENCES users(id), week TEXT NOT NULL,
                updated_at TEXT NOT NULL, PRIMARY KEY(user_id, week)
            );
            CREATE TABLE IF NOT EXISTS attachments (
                id INTEGER PRIMARY KEY AUTOINCREMENT, project_id INTEGER NOT NULL REFERENCES projects(id),
                user_id INTEGER NOT NULL REFERENCES users(id), week TEXT NOT NULL,
                filename TEXT NOT NULL, storage_key TEXT NOT NULL UNIQUE, size INTEGER NOT NULL,
                attached INTEGER NOT NULL DEFAULT 0, created_at TEXT NOT NULL, staged_at TEXT NOT NULL
            );
            CREATE INDEX IF NOT EXISTS attachments_report ON attachments(user_id, week, project_id);
            CREATE TABLE IF NOT EXISTS sessions (
                token_hash TEXT PRIMARY KEY, user_id INTEGER NOT NULL REFERENCES users(id),
                csrf TEXT NOT NULL, expires REAL NOT NULL
            );
            CREATE TABLE IF NOT EXISTS feishu_departments (
                app_id TEXT NOT NULL, open_department_id TEXT NOT NULL, name TEXT NOT NULL,
                parent_id TEXT NOT NULL, synced_at TEXT NOT NULL,
                PRIMARY KEY(app_id, open_department_id)
            );
            CREATE TABLE IF NOT EXISTS feishu_sync_runs (
                id INTEGER PRIMARY KEY, started_at TEXT NOT NULL, finished_at TEXT,
                status TEXT NOT NULL, users_count INTEGER NOT NULL DEFAULT 0,
                departments_count INTEGER NOT NULL DEFAULT 0, message TEXT NOT NULL DEFAULT ''
            );
            CREATE TABLE IF NOT EXISTS feishu_oauth_states (
                state_hash TEXT PRIMARY KEY, browser_hash TEXT NOT NULL,
                verifier TEXT NOT NULL, expires REAL NOT NULL
            );
        """)
        existing = {row[1] for row in db.execute("PRAGMA table_info(users)")}
        for name, definition in {
            "auth_source": "TEXT NOT NULL DEFAULT 'local'", "feishu_app_id": "TEXT", "feishu_open_id": "TEXT",
            "avatar_url": "TEXT NOT NULL DEFAULT ''", "feishu_department_ids": "TEXT NOT NULL DEFAULT '[]'",
            "employment_status": "TEXT NOT NULL DEFAULT 'local'", "synced_at": "TEXT",
        }.items():
            if name not in existing:
                db.execute(f"ALTER TABLE users ADD COLUMN {name} {definition}")
        db.execute("CREATE UNIQUE INDEX IF NOT EXISTS users_feishu_identity ON users(feishu_app_id, feishu_open_id)")
        existing = {row[1] for row in db.execute("PRAGMA table_info(submissions)")}
        for name, definition in {"leave_percent": "REAL NOT NULL DEFAULT 0", "leave_note": "TEXT NOT NULL DEFAULT ''"}.items():
            if name not in existing:
                db.execute(f"ALTER TABLE submissions ADD COLUMN {name} {definition}")
        domain.initialize_domain(db)


def report_week(value):
    week = parse_date(value, "填报周")
    if week.weekday() != 0 or week.isoformat() > current_week():
        raise APIError("请选择本周或之前的周一日期")
    return week.isoformat()


class APIError(Exception):
    def __init__(self, message, status=400):
        self.message, self.status = message, status


def text_field(data, key, label, limit=100, required=True):
    value = data.get(key, "")
    if not isinstance(value, str):
        raise APIError(f"{label}格式不正确")
    value = value.strip()
    if (required and not value) or len(value) > limit:
        raise APIError(f"请填写{label}，最多 {limit} 个字符")
    return value


def password_field(data, key="password"):
    value = data.get(key)
    if not isinstance(value, str) or not 8 <= len(value) <= 128:
        raise APIError("密码长度须为 8–128 位")
    return value


def integer(value, label="编号"):
    if isinstance(value, bool) or not isinstance(value, int) or value < 1:
        raise APIError(f"{label}不正确")
    return value


def percentage(value, label):
    if isinstance(value, bool) or not isinstance(value, (float, int)):
        raise APIError(f"{label}须为数字")
    if not math.isfinite(value) or not 0 <= value <= 100 or value * 2 != int(value * 2):
        raise APIError(f"{label}须在 0–100 之间，最小单位为 0.5%")
    return value


def parse_date(value, label):
    try:
        if not isinstance(value, str) or len(value) != 10:
            raise ValueError()
        parsed = date.fromisoformat(value)
        if parsed.isoformat() != value:
            raise ValueError()
        return parsed
    except (ValueError, TypeError):
        raise APIError(f"{label}格式不正确") from None


class Server(ThreadingHTTPServer):
    daemon_threads = True
    # A weekly-report deadline can bring the whole team online at once.
    # Small platform defaults reject short bursts before worker threads start.
    request_queue_size = 128

    def __init__(self, address, db_path, config=None, feishu_client=None, uploads_dir=None):
        self.db_path = str(Path(db_path).resolve())
        initialize(self.db_path)
        self.uploads_dir = Path(uploads_dir or Path(self.db_path).parent / "attachments").resolve()
        self.uploads_dir.mkdir(parents=True, exist_ok=True)
        self.cleanup_uploads()
        self.login_attempts = {}
        # Hundreds of simultaneous SQLite row-to-dict loops contend on the GIL.
        # Keep this short snapshot read bounded; response transmission and writes
        # can still proceed concurrently, and every refresh reads current data.
        self.state_read_lock = threading.Lock()
        self.config = config or FeishuConfig.from_env()
        self.feishu = FeishuIntegration(self.config, self.db_path, connect, feishu_client) if self.config.mode == "feishu" else None
        # Keep WAL/SHM files open across short-lived requests. Otherwise the last
        # closing connection repeatedly checkpoints/removes them while new HTTP
        # workers are opening the same database (especially costly on Windows).
        self.db_anchor = sqlite3.connect(self.db_path, timeout=15, check_same_thread=False)
        self.db_anchor.execute("SELECT 1 FROM users LIMIT 1").fetchone()
        try:
            super().__init__(address, Handler)
        except Exception:
            self.db_anchor.close()
            raise

    def cleanup_uploads(self):
        # Keep unsubmitted files for seven days, including files removed by a report update.
        cutoff = (datetime.now(SHANGHAI) - timedelta(days=7)).isoformat(timespec="seconds")
        with connect(self.db_path) as db:
            stale = list(db.execute("SELECT id, storage_key FROM attachments WHERE attached=0 AND staged_at < ?", (cutoff,)))
            for row in stale:
                (self.uploads_dir / row["storage_key"]).unlink(missing_ok=True)
                db.execute("DELETE FROM attachments WHERE id=?", (row["id"],))
            known = {row[0] for row in db.execute("SELECT storage_key FROM attachments")}
        for file in self.uploads_dir.iterdir():
            if re.fullmatch(r"[0-9a-f]{48}", file.name) and file.name not in known and file.stat().st_mtime < time.time() - 7 * 86400:
                file.unlink(missing_ok=True)

    def server_close(self):
        if self.feishu:
            self.feishu.stop()
        super().server_close()
        self.db_anchor.close()


class Handler(BaseHTTPRequestHandler):
    server_version = "Qingzhou/1.0"

    def log_message(self, fmt, *args):
        # Never log bodies, passwords, cookies, or query strings.
        print(f"[{timestamp()}] {self.client_address[0]} {self.command} {urlparse(self.path).path} {args[1] if len(args) > 1 else ''}")

    def send_data(self, body, status=200, content_type="application/json; charset=utf-8", headers=None):
        if not isinstance(body, bytes):
            body = json.dumps(body, ensure_ascii=False, allow_nan=False, separators=(",", ":")).encode("utf-8")
        if content_type.startswith("application/json") and len(body) >= 1024:
            headers = {**(headers or {}), "Vary": "Accept-Encoding"}
            accepted = {}
            for item in self.headers.get("Accept-Encoding", "").lower().split(","):
                parts = [part.strip() for part in item.split(";")]
                quality = 1
                for option in parts[1:]:
                    if option.startswith("q="):
                        try:
                            quality = float(option[2:])
                        except ValueError:
                            quality = 0
                accepted[parts[0]] = quality
            if 0 < accepted.get("gzip", accepted.get("*", 0)) <= 1:
                body = gzip.compress(body, compresslevel=1, mtime=0)
                headers["Content-Encoding"] = "gzip"
        self.response_headers(len(body), status, content_type, headers)
        self.wfile.write(body)

    def response_headers(self, size, status=200, content_type="application/octet-stream", headers=None):
        self.send_response(status)
        self.send_header("Content-Type", content_type)
        self.send_header("Content-Length", str(size))
        self.send_header("Cache-Control", "no-store")
        self.send_header("X-Content-Type-Options", "nosniff")
        self.send_header("Referrer-Policy", "same-origin")
        self.send_header("X-Frame-Options", "DENY")
        self.send_header("Content-Security-Policy", "default-src 'self'; style-src 'self' 'unsafe-inline'; img-src 'self' data:; script-src 'self'; base-uri 'none'; frame-ancestors 'none'; form-action 'self'")
        for key, value in (headers or {}).items():
            self.send_header(key, value)
        self.end_headers()

    def send_file(self, file, size, filename, content_type="application/octet-stream"):
        self.response_headers(size, content_type=content_type, headers={
            "Content-Disposition": f"attachment; filename=\"download\"; filename*=UTF-8''{quote(filename, safe='')}"})
        shutil.copyfileobj(file, self.wfile, 64 * 1024)

    def body(self):
        try:
            size = int(self.headers.get("Content-Length", "0"))
            if size < 0 or size > 256000:
                raise APIError("提交内容过大", 413)
            data = json.loads(self.rfile.read(size))
            if not isinstance(data, dict):
                raise ValueError()
            return data
        except (ValueError, UnicodeDecodeError):
            raise APIError("请求内容须为 JSON 对象") from None

    def session(self, db):
        cookie = SimpleCookie()
        try:
            cookie.load(self.headers.get("Cookie", ""))
            token = cookie.get("pms_session")
            if not token:
                return None
            token_hash = hashlib.sha256(token.value.encode()).hexdigest()
            user = db.execute("""SELECT u.*, s.csrf, s.token_hash FROM sessions s JOIN users u
                ON u.id = s.user_id WHERE s.token_hash = ? AND s.expires > ? AND u.active = 1""",
                (token_hash, time.time())).fetchone()
            if user and user["auth_source"] == self.server.config.mode:
                if user["auth_source"] == "local" or user["feishu_app_id"] == self.server.config.app_id:
                    return user
            return None
        except Exception:
            return None

    def require_admin(self, user):
        if user["role"] != "admin":
            raise APIError("此操作需要管理员权限", 403)

    def auth_response(self, db, user_id, redirect=False):
        token = secrets.token_urlsafe(32)
        csrf = secrets.token_urlsafe(32)
        db.execute("DELETE FROM sessions WHERE expires < ?", (time.time(),))
        db.execute("INSERT INTO sessions VALUES (?, ?, ?, ?)",
                   (hashlib.sha256(token.encode()).hexdigest(), user_id, csrf, time.time() + SESSION_AGE))
        db.commit()
        secure = "; Secure" if os.environ.get("PMS_SECURE_COOKIE") == "1" else ""
        headers = {"Set-Cookie": f"pms_session={token}; Path=/; HttpOnly; SameSite=Strict; Max-Age={SESSION_AGE}{secure}"}
        if redirect:
            headers["Location"] = "/"
        self.send_data({"ok": True}, status=303 if redirect else 200, headers=headers)

    def do_GET(self):
        self.dispatch()

    def do_POST(self):
        self.dispatch()

    def do_PUT(self):
        self.dispatch()

    def dispatch(self):
        try:
            with connect(self.server.db_path) as db:
                self.route(db)
        except APIError as exc:
            self.send_data({"error": exc.message}, exc.status)
        except FeishuError as exc:
            self.send_data({"error": str(exc)}, 502)
        except sqlite3.IntegrityError:
            self.send_data({"error": "数据冲突，请刷新后重试；登录名不能重复"}, 409)
        except Exception as exc:
            import traceback
            if isinstance(exc, sqlite3.Error):
                print(f"SQLite failure: {getattr(exc, 'sqlite_errorname', 'unknown')} ({getattr(exc, 'sqlite_errorcode', 'unknown')})", flush=True)
            traceback.print_exc()
            self.send_data({"error": "服务器处理失败，请稍后重试"}, 500)

    def route(self, db):
        path = urlparse(self.path).path
        method = self.command
        if not path.startswith("/api/"):
            if method != "GET":
                raise APIError("不支持此请求", 405)
            return self.static(path)

        user = self.session(db)
        if method != "GET":
            origin = self.headers.get("Origin")
            if origin and (urlparse(origin).netloc != self.headers.get("Host") or urlparse(origin).scheme not in ("http", "https")):
                raise APIError("请求来源不正确", 403)

        if path == "/api/bootstrap" and method == "GET":
            return self.send_data({"needs_setup": self.server.config.mode == "local" and not db.execute("SELECT 1 FROM users LIMIT 1").fetchone(),
                "auth_mode": self.server.config.mode,
                "authenticated": bool(user), "week": current_week(), "today": today().isoformat()})

        if path == "/api/auth/feishu/start" and method == "GET":
            if not self.server.feishu:
                raise APIError("尚未启用飞书登录", 404)
            url, browser = self.server.feishu.create_oauth()
            secure = "; Secure" if os.environ.get("PMS_SECURE_COOKIE") == "1" else ""
            return self.send_data({}, 303, headers={"Location": url, "Set-Cookie":
                f"pms_oauth={browser}; Path=/api/auth/feishu; HttpOnly; SameSite=Lax; Max-Age=600{secure}"})

        if path == "/api/auth/feishu/callback" and method == "GET":
            if not self.server.feishu:
                raise APIError("尚未启用飞书登录", 404)
            params = parse_qs(urlparse(self.path).query)
            try:
                cookie = SimpleCookie()
                cookie.load(self.headers.get("Cookie", ""))
                browser = cookie.get("pms_oauth")
                verifier = self.server.feishu.consume_oauth(params.get("state", [""])[0], browser.value if browser else "")
                code = params.get("code", [""])[0]
                if params.get("error") or not code or len(code) > 2048:
                    raise FeishuError("飞书授权未完成")
                identity = self.server.feishu.client.login_identity(code, verifier)
                mirror = db.execute("SELECT * FROM users WHERE feishu_app_id=? AND feishu_open_id=?",
                    (self.server.config.app_id, identity)).fetchone()
                if not mirror or not mirror["active"]:
                    self.server.feishu.sync()
                    mirror = db.execute("SELECT * FROM users WHERE feishu_app_id=? AND feishu_open_id=?",
                        (self.server.config.app_id, identity)).fetchone()
                if not mirror or not mirror["active"]:
                    raise FeishuError("人员尚未同步或账号已停用")
                return self.auth_response(db, mirror["id"], redirect=True)
            except FeishuError:
                # Never place authorization codes or upstream descriptions in a URL or log.
                return self.send_data({}, 303, headers={"Location": "/?auth_error=feishu_login"})

        if path == "/api/setup" and method == "POST":
            if self.server.feishu:
                raise APIError("飞书模式无需注册，请使用飞书账号登录", 403)
            data = self.body()
            name = text_field(data, "name", "姓名", 30)
            username = text_field(data, "username", "登录名", 32)
            if not USERNAME.fullmatch(username):
                raise APIError("登录名须为 3–32 位字母、数字、点、横线或下划线")
            password = hash_password(password_field(data))
            db.execute("BEGIN IMMEDIATE")
            if db.execute("SELECT 1 FROM users LIMIT 1").fetchone():
                raise APIError("系统已完成初始化，请登录", 409)
            row = db.execute("INSERT INTO users(name, username, password, role, created_at) VALUES (?, ?, ?, 'admin', ?)",
                (name, username, password, timestamp()))
            return self.auth_response(db, row.lastrowid)

        if path == "/api/login" and method == "POST":
            if self.server.feishu:
                raise APIError("请使用飞书登录，无需本地账号密码", 403)
            data = self.body()
            username = text_field(data, "username", "登录名", 32)
            password = data.get("password", "")
            if not isinstance(password, str) or len(password) > 128:
                raise APIError("登录名或密码不正确", 401)
            now = time.time()
            key = (self.client_address[0], username.lower())
            attempts = self.server.login_attempts
            for old_key in list(attempts):
                if attempts[old_key][0] < now - 900:
                    del attempts[old_key]
            count = attempts.get(key, (now, 0))
            if count[1] >= 10:
                raise APIError("尝试次数过多，请 15 分钟后重试", 429)
            row = db.execute("SELECT * FROM users WHERE username = ? AND active = 1 AND auth_source='local'", (username,)).fetchone()
            # Perform a hash even for unknown accounts to reduce timing differences.
            valid = check_password(password, row["password"]) if row else check_password(password, hash_password("invalid-account"))
            if not row or not valid:
                attempts[key] = (count[0], count[1] + 1)
                raise APIError("登录名或密码不正确", 401)
            attempts.pop(key, None)
            return self.auth_response(db, row["id"])

        if not user:
            raise APIError("请先登录", 401)
        if method != "GET" and not hmac.compare_digest(self.headers.get("X-CSRF-Token", ""), user["csrf"]):
            raise APIError("登录状态已更新，请刷新页面后重试", 403)

        if path == "/api/logout" and method == "POST":
            db.execute("DELETE FROM sessions WHERE token_hash = ?", (user["token_hash"],))
            db.commit()
            return self.send_data({"ok": True}, headers={"Set-Cookie": "pms_session=; Path=/; HttpOnly; SameSite=Strict; Max-Age=0"})
        if path == "/api/state" and method == "GET":
            return self.send_data(self.state(db, user))
        if path == "/api/analytics" and method == "GET":
            params = parse_qs(urlparse(self.path).query)
            scope = params.get("scope", ["personal"])[0]
            if scope not in ("personal", "department", "company", "project"):
                raise APIError("没有此统计维度的权限", 403)
            week = report_week(params.get("week", [current_week()])[0])
            db.execute("BEGIN")
            ctx = domain.context(db, user)
            if not (bool(ctx['owned']) if scope == 'project' else ctx['grants']['stats_' + scope]):
                raise APIError("没有此统计维度的权限", 403)
            return self.send_data(domain.statistics(db, user, ctx, scope, week))
        if path == "/api/reminders" and method == "POST":
            return self.save_reminders(db, user, self.body())
        if path == "/api/reminders/read" and method == "PUT":
            db.execute("UPDATE reminders SET read_at=? WHERE user_id=? AND read_at IS NULL", (timestamp(), user["id"]))
            db.commit()
            return self.send_data({"ok": True})
        if path == "/api/feishu/status" and method == "GET":
            self.require_admin(user)
            return self.send_data(self.server.feishu.status() if self.server.feishu else {"auth_mode": "local"})
        if path == "/api/feishu/sync" and method == "POST":
            self.require_admin(user)
            if not self.server.feishu:
                raise APIError("请先配置飞书模式", 400)
            return self.send_data(self.server.feishu.sync())
        match = re.fullmatch(r"/api/users/(\d+)/role", path)
        if match and method == "PUT":
            self.require_admin(user)
            return self.save_role(db, int(match[1]), self.body())
        if path == "/api/password" and method == "PUT":
            if self.server.feishu:
                raise APIError("身份认证由飞书管理，请在飞书中维护账号", 403)
            data = self.body()
            old = data.get("old_password", "")
            if not isinstance(old, str) or not check_password(old, user["password"]):
                raise APIError("原密码不正确")
            db.execute("UPDATE users SET password = ? WHERE id = ?", (hash_password(password_field(data)), user["id"]))
            db.execute("DELETE FROM sessions WHERE user_id = ? AND token_hash != ?", (user["id"], user["token_hash"]))
            db.commit()
            return self.send_data({"ok": True})
        if path == "/api/users" and method == "POST":
            self.require_admin(user)
            return self.save_user(db, self.body())
        match = re.fullmatch(r"/api/users/(\d+)", path)
        if match and method == "PUT":
            self.require_admin(user)
            return self.save_user(db, self.body(), int(match[1]))
        if path == "/api/projects" and method == "POST":
            return self.save_project(db, self.body(), user=user)
        match = re.fullmatch(r"/api/projects/(\d+)", path)
        if match and method == "PUT":
            project = db.execute("SELECT * FROM projects WHERE id = ?", (int(match[1]),)).fetchone()
            if not project:
                raise APIError("项目不存在", 404)
            if int(match[1]) not in domain.context(db, user)["managed"]:
                raise APIError("仅管理员和项目负责人可以修改项目", 403)
            return self.save_project(db, self.body(), int(match[1]), user=user)
        if path == "/api/reports" and method == "PUT":
            return self.save_reports(db, user, self.body())
        match = re.fullmatch(r"/api/reports/(\d+)/coordination", path)
        if match and method == "PUT":
            return self.save_coordination(db, user, int(match[1]), self.body())
        if path == "/api/attachments" and method == "POST":
            return self.upload_attachment(db, user)
        match = re.fullmatch(r"/api/attachments/(\d+)", path)
        if match and method == "GET":
            return self.download_attachment(db, user, int(match[1]))
        if path == "/api/export/attachments" and method == "GET":
            self.require_admin(user)
            if not domain.permissions(user)["report_view"]:
                raise APIError("没有周报查看权限", 403)
            return self.export_attachments(db)
        if path == "/api/export" and method == "GET":
            self.require_admin(user)
            if not domain.permissions(user)["report_view"]:
                raise APIError("没有周报查看权限", 403)
            return self.export(db)
        raise APIError("接口不存在", 404)

    def state(self, db, user):
        with self.server.state_read_lock:
            return self.read_state(db, user)

    def read_state(self, db, user):
        # Keep projects, memberships, reports and submissions in one read snapshot.
        db.execute("BEGIN")
        ctx = domain.context(db, user)
        fields = ("id", "name", "username", "role", "department", "active", "created_at", "auth_source", "feishu_open_id", "feishu_app_id", "employment_status", "synced_at")
        people = [{k: u[k] for k in fields} for u in ctx["people"].values()]
        for item in people:
            if user["role"] == "admin" or item["id"] == user["id"]:
                item["permissions"] = domain.permissions(ctx["people"][item["id"]])
                item["managed_departments"] = json.loads(ctx["people"][item["id"]]["managed_departments"])
        if self.server.feishu:
            for item in people:
                if item["auth_source"] != "feishu" or item["feishu_app_id"] != self.server.config.app_id:
                    item["active"] = 0
        projects = []
        for original in ctx["projects"].values():
            pid = original["id"]
            if pid not in ctx["visible"] | ctx["context_only"]:
                continue
            p = dict(original)
            # Retire the old optional reference from all user-facing project data.
            p.pop("approval_ref", None)
            p["context_only"] = pid in ctx["context_only"]
            p["can_manage"] = pid in ctx["managed"]
            p["is_owned"] = pid in ctx["owned"]
            p["is_department"] = pid in ctx["department_visible"] and (ctx['grants']['stats_department'] or domain.department_manager(user) or user['role'] == 'admin')
            p["is_participant"] = any(m['project_id'] == pid and m['user_id'] == user['id'] for m in ctx['assignments'])
            p["can_read_all_reports"] = bool(ctx['grants']['report_view'] and (pid in ctx['read_projects'] or ctx['grants']['stats_company']))
            p["limited_view"] = pid not in ctx['direct'] and not ctx['grants']['stats_company']
            p["effective_archived"] = domain.archived_in_tree(ctx["projects"], pid)
            p["assignments"] = [m for m in ctx["assignments"] if m["project_id"] == pid]
            p["member_ids"] = [m["user_id"] for m in p["assignments"]]
            p["viewer_ids"] = [v["user_id"] for v in ctx["viewers"] if v["project_id"] == pid]
            if p["context_only"]:
                p.update(description="", assignments=[], member_ids=[], viewer_ids=[])
            elif p['limited_view']:
                # Cross-department participation grants a roster of our people,
                # not the external project's description or other departments.
                p['description'] = ''
                p['assignments'] = [m for m in p['assignments'] if m['user_id'] in ctx['scope_people']]
                p['member_ids'] = [m['user_id'] for m in p['assignments']]
                p['viewer_ids'] = []
            projects.append(p)
        reports = [domain.report_data(ctx,user,r) for r in ctx["reports"] if domain.can_read_report(ctx, user, r)]
        visible_people = ctx["scope_people"] | {m['user_id'] for m in ctx['assignments'] if m['project_id'] in ctx['read_projects']}
        # A project-only viewer sees submission status, but no other-project activity or leave notes.
        submissions = []
        non_project = []
        for r in db.execute("SELECT * FROM submissions ORDER BY week DESC"):
            if r["user_id"] in visible_people:
                item = dict(r)
                if r["user_id"] != user["id"] and (r["user_id"] not in ctx["scope_people"] or not ctx["grants"]["report_view"]):
                    item.update(leave_percent=0, leave_note="")
                submissions.append(item)
        if ctx["grants"]["report_view"]:
            non_project = [dict(r) for r in db.execute("SELECT * FROM non_project_entries ORDER BY week DESC") if r["user_id"] in ctx["scope_people"]]
        else:
            non_project = [dict(r) for r in db.execute("SELECT * FROM non_project_entries WHERE user_id=? ORDER BY week DESC", (user["id"],))]
        readable = {(r["project_id"], r["user_id"], r["week"]) for r in reports}
        attachments = [dict(row) for row in db.execute("SELECT id, project_id, user_id, week, filename, size, attached, created_at FROM attachments ORDER BY id")
            if row["user_id"] == user["id"] or (row["attached"] and (row["project_id"],row["user_id"],row["week"]) in readable)]
        return {"user": {key: user[key] for key in ("id", "name", "username", "role", "department", "auth_source")},
            "csrf": user["csrf"], "users": people, "projects": projects, "reports": reports,
            "submissions": submissions, "attachments": attachments, "non_project_entries": non_project,
            "permissions": ctx["grants"], "managed_departments": sorted(domain.departments(user)),
            "capabilities": {"project_management": bool(ctx['owned']),
                "department_management": domain.department_manager(user),
                "create_project": user['role'] == 'admin' or domain.department_manager(user)},
            "reminders": [dict(r) for r in db.execute("SELECT * FROM reminders WHERE user_id=? AND read_at IS NULL ORDER BY created_at DESC", (user["id"],))] if ctx['grants']['report_submit'] else [],
            "attachment_max_size": MAX_ATTACHMENT_SIZE, "attachment_max_count": MAX_ATTACHMENTS,
            "week": current_week(), "today": today().isoformat(),
            "auth_mode": self.server.config.mode,
            "directory": self.server.feishu.status() if self.server.feishu and user["role"] == "admin" else None}

    def save_role(self, db, user_id, data):
        role = data.get("role")
        if role not in ("admin", "member", "department_manager"):
            raise APIError("角色不正确")
        db.execute("BEGIN IMMEDIATE")
        target = db.execute("SELECT * FROM users WHERE id=?", (user_id,)).fetchone()
        if not target or target["auth_source"] != self.server.config.mode:
            raise APIError("成员不存在", 404)
        if self.server.feishu and target["feishu_open_id"] in self.server.config.admin_open_ids and role != "admin":
            raise APIError("该成员是部署配置中指定的管理员，请先调整 FEISHU_ADMIN_OPEN_IDS")
        if target["role"] == "admin" and target["active"] and role != "admin":
            if db.execute("SELECT COUNT(*) FROM users WHERE role='admin' AND active=1 AND auth_source=?", (self.server.config.mode,)).fetchone()[0] <= 1:
                raise APIError("至少保留一名启用的管理员")
        grants, managed = self.validate_grants(data, target)
        db.execute("UPDATE users SET role=?,permissions=?,managed_departments=? WHERE id=?", (role, grants, managed, user_id))
        db.commit()
        self.send_data({"ok": True})

    def save_user(self, db, data, user_id=None):
        if self.server.feishu:
            raise APIError("人员资料由飞书通讯录同步维护，本系统仅可调整业务角色", 403)
        name = text_field(data, "name", "姓名", 30)
        username = text_field(data, "username", "登录名", 32)
        department = text_field(data, "department", "部门", 50, False)
        if not USERNAME.fullmatch(username):
            raise APIError("登录名须为 3–32 位字母、数字、点、横线或下划线")
        role = data.get("role", "member")
        active = data.get("active", True)
        if role not in ("admin", "member", "department_manager") or not isinstance(active, bool):
            raise APIError("成员状态或角色不正确")
        db.execute("BEGIN IMMEDIATE")
        prior = db.execute("SELECT * FROM users WHERE id=?", (user_id,)).fetchone() if user_id else None
        grants, managed = self.validate_grants(data, prior)
        if user_id:
            old = db.execute("SELECT * FROM users WHERE id = ?", (user_id,)).fetchone()
            if not old:
                raise APIError("成员不存在", 404)
            if old["auth_source"] != "local":
                raise APIError("飞书镜像资料只能通过通讯录同步更新", 403)
            if old["role"] == "admin" and old["active"] and (role != "admin" or not active):
                if db.execute("SELECT COUNT(*) FROM users WHERE role = 'admin' AND active = 1").fetchone()[0] <= 1:
                    raise APIError("至少保留一名启用的管理员")
            if not active and db.execute("SELECT 1 FROM projects WHERE owner_id = ? AND archived = 0", (user_id,)).fetchone():
                raise APIError("该成员仍负责进行中的项目，请先更换项目负责人")
            db.execute("UPDATE users SET name = ?, username = ?, department = ?, role = ?, active = ? WHERE id = ?",
                (name, username, department, role, int(active), user_id))
            if data.get("password"):
                db.execute("UPDATE users SET password = ? WHERE id = ?", (hash_password(password_field(data)), user_id))
                db.execute("DELETE FROM sessions WHERE user_id = ?", (user_id,))
            if not active:
                db.execute("DELETE FROM sessions WHERE user_id = ?", (user_id,))
        else:
            row = db.execute("INSERT INTO users(name, username, department, password, role, active, created_at) VALUES (?, ?, ?, ?, ?, ?, ?)",
                (name, username, department, hash_password(password_field(data)), role, int(active), timestamp()))
            user_id = row.lastrowid
        db.execute("UPDATE users SET permissions=?,managed_departments=? WHERE id=?", (grants, managed, user_id))
        db.commit()
        self.send_data({"ok": True, "id": user_id})

    def validate_grants(self, data, prior=None):
        grants = data.get("permissions", json.loads(prior["permissions"]) if prior else {})
        managed = data.get("managed_departments", json.loads(prior["managed_departments"]) if prior else [])
        if not isinstance(grants, dict) or any(k not in domain.PERMISSIONS or not isinstance(v, bool) for k,v in grants.items()):
            raise APIError("权限配置不正确")
        if not isinstance(managed, list) or len(managed) > 100 or any(not isinstance(d,str) or not d.strip() or len(d)>50 for d in managed):
            raise APIError("请选择或填写有效的授权部门")
        return json.dumps(grants), json.dumps(sorted({d.strip() for d in managed}), ensure_ascii=False)

    def save_reminders(self, db, user, data):
        scope = data.get("scope", "department")
        if scope not in ("department", "company", "project"):
            raise APIError("没有该范围的提醒权限", 403)
        week = report_week(data.get("week"))
        ids = data.get("user_ids")
        if not isinstance(ids, list) or len(ids) > 1000:
            raise APIError("请选择待填报人员")
        ids = {integer(uid, "人员") for uid in ids}
        db.execute("BEGIN IMMEDIATE")
        ctx = domain.context(db, user)
        if not (bool(ctx['owned']) if scope == 'project' else ctx['grants']['stats_' + scope]):
            raise APIError("没有该范围的提醒权限", 403)
        stats = domain.statistics(db, user, ctx, scope, week)
        allowed = {m["id"] for m in stats["members"] if m["expected"]}
        if not ids <= allowed:
            raise APIError("只能提醒授权范围内需要填报的人员", 403)
        ids &= set(stats["pending_ids"])
        now = timestamp()
        db.executemany("""INSERT INTO reminders(user_id,sender_id,week,created_at) VALUES(?,?,?,?)
            ON CONFLICT(user_id,sender_id,week) DO UPDATE SET created_at=excluded.created_at,read_at=NULL""",
            [(uid,user["id"],week,now) for uid in ids])
        db.commit()
        return self.send_data({"ok": True, "count": len(ids)})

    def save_project(self, db, data, project_id=None, user=None):
        db.execute("BEGIN IMMEDIATE")
        ctx = domain.context(db, user)
        old = ctx["projects"].get(project_id)
        if project_id and project_id not in ctx["managed"]:
            raise APIError("没有项目管理权限", 403)
        parent_id = data.get("parent_id", old["parent_id"] if old else None)
        if parent_id is not None:
            parent_id = integer(parent_id, "上级项目")
            if parent_id not in ctx["projects"]:
                raise APIError("上级项目不存在")
            if project_id and parent_id in domain.descendants(ctx["projects"], {project_id}):
                raise APIError("WBS 上级不能是自己或下级项目")
        if user["role"] != "admin":
            if not old and ((parent_id is None and not domain.department_manager(user)) or (parent_id is not None and parent_id not in ctx['managed'])):
                raise APIError("部门负责人可新建部门项目；项目负责人可创建负责项目的子项目", 403)
            if old and parent_id != old["parent_id"] and ((parent_id is None and not domain.department_manager(user)) or (parent_id is not None and parent_id not in ctx["managed"])):
                raise APIError("只能移入自己管理的上级项目", 403)
        if parent_id and domain.archived_in_tree(ctx["projects"], parent_id):
            raise APIError("归档项目下不能创建或移入子项目")
        name = text_field(data, "name", "项目名称", 80)
        description = text_field(data, "description", "项目说明", 1000, False)
        owner = integer(data.get("owner_id"), "负责人")
        # A stable department is needed for responsibility after delegation.
        department = text_field(data, 'department', '归属部门', 50, False) if 'department' in data else (
            old['department'] if old else ctx['projects'][parent_id]['department'] if parent_id else
            user['department'] if domain.department_manager(user) else ctx['people'].get(owner, {}).get('department', ''))
        if parent_id:
            department = ctx['projects'][parent_id]['department']
        if user['role'] != 'admin':
            if not old and not parent_id and (not department or department not in domain.departments(user)):
                raise APIError('只能在授权管理的部门中新建项目', 403)
            if old and department != old['department'] and department not in domain.departments(user):
                raise APIError('不能将项目转移到未授权的部门', 403)
            if old and department != old['department'] and not domain.department_manager(user):
                raise APIError('归属部门由部门负责人或公司管理员调整', 403)
        start = parse_date(data.get("start_date"), "开始日期")
        due = parse_date(data.get("due_date"), "计划完成日期")
        if due < start:
            raise APIError("计划完成日期不能早于开始日期")
        priority = data.get("priority", "normal")
        archived = data.get("archived", False)
        members = data.get("member_ids")
        if priority not in ("high", "normal", "low") or not isinstance(archived, bool):
            raise APIError("优先级或项目状态不正确")
        if not isinstance(members, list) or len(members) > 1000:
            raise APIError("请选择项目成员")
        members = set(integer(item, "成员") for item in members)
        viewers = data.get("viewer_ids", [v["user_id"] for v in ctx["viewers"] if v["project_id"] == project_id])
        if not isinstance(viewers,list) or len(viewers)>1000:
            raise APIError("请选择项目查看人")
        viewers = {integer(v, "查看人") for v in viewers}
        if not project_id:
            viewers.add(user["id"])
        periods = data.get("assignments")
        old_periods = {m["user_id"]:m for m in ctx["assignments"] if m["project_id"] == project_id}
        if periods is not None:
            if not isinstance(periods,list) or len(periods)>1000 or any(not isinstance(m,dict) for m in periods):
                raise APIError("参与时间配置不正确")
            period_map = {}
            for m in periods:
                uid = integer(m.get("user_id"), "参与人")
                if uid not in members or uid in period_map:
                    raise APIError("参与时间对应人员不正确或重复")
                period_map[uid] = m
        else:
            period_map = old_periods
        assignments = []
        for uid in sorted(members):
            m = period_map.get(uid, {})
            first, last = m.get("start_date", ""), m.get("end_date", "")
            if first:
                parse_date(first, "参与开始日期")
            if last:
                parse_date(last, "参与结束日期")
            if not isinstance(first,str) or not isinstance(last,str) or (first or start.isoformat()) > (last or due.isoformat()):
                raise APIError("参与结束日期不能早于开始日期")
            if (first and first < start.isoformat()) or (last and last > due.isoformat()):
                raise APIError("参与时间须在本节点的项目日期范围内")
            assignments.append((uid,first,last))
        # Inactive historical members can remain, but cannot be newly assigned.
        old_members = set(row[0] for row in db.execute("SELECT user_id FROM memberships WHERE project_id = ?", (project_id,))) if project_id else set()
        old_viewers = {v["user_id"] for v in ctx["viewers"] if v["project_id"] == project_id}
        for member in members | viewers | {owner}:
            record = db.execute("SELECT active, auth_source FROM users WHERE id = ?", (member,)).fetchone()
            if not record or (not record[0] and (member not in old_members | old_viewers or member == owner)):
                raise APIError("负责人及新增成员必须是启用的账号")
            if self.server.feishu and record[1] != "feishu" and (member not in old_members or member == owner):
                raise APIError("飞书模式只能分配飞书通讯录中的启用成员")
        now = timestamp()
        if project_id:
            db.execute("UPDATE projects SET name = ?, description = ?, owner_id = ?, start_date = ?, due_date = ?, priority = ?, archived = ?, updated_at = ? WHERE id = ?",
                (name, description, owner, start.isoformat(), due.isoformat(), priority, int(archived), now, project_id))
            db.execute("DELETE FROM memberships WHERE project_id = ?", (project_id,))
        else:
            row = db.execute("INSERT INTO projects(name, description, owner_id, start_date, due_date, priority, archived, created_at, updated_at) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)",
                (name, description, owner, start.isoformat(), due.isoformat(), priority, int(archived), now, now))
            project_id = row.lastrowid
        db.execute("UPDATE projects SET parent_id=?,department=? WHERE id=?", (parent_id,department,project_id))
        if old and department != old['department']:
            # Moving a WBS branch moves its responsibility with it.
            children = domain.descendants(ctx['projects'], {project_id}) - {project_id}
            db.executemany('UPDATE projects SET department=? WHERE id=?', [(department,pid) for pid in children])
        db.executemany("INSERT INTO memberships(project_id,user_id,start_date,end_date) VALUES (?,?,?,?)", [(project_id,*m) for m in assignments])
        db.execute("DELETE FROM project_viewers WHERE project_id=?", (project_id,))
        db.executemany("INSERT INTO project_viewers VALUES (?,?)", [(project_id,uid) for uid in viewers])
        db.commit()
        self.send_data({"ok": True, "id": project_id})

    def require_report_access(self, db, user, project_id, week):
        if not domain.permissions(user)["report_submit"]:
            raise APIError("没有周报填报权限", 403)
        project = db.execute("SELECT * FROM projects WHERE id=?", (project_id,)).fetchone()
        existing = db.execute("SELECT 1 FROM reports WHERE project_id=? AND user_id=? AND week=?", (project_id, user["id"], week)).fetchone()
        membership = db.execute("SELECT * FROM memberships WHERE project_id=? AND user_id=?", (project_id, user["id"])).fetchone()
        projects = {r['id']:r for r in db.execute('SELECT * FROM projects')}
        if not project or (not existing and (domain.archived_in_tree(projects, project_id) or not membership or not domain.active_assignment(membership,project,week))):
            raise APIError("仅能填报参与时间内的进行中项目；历史条目仍可修改", 403)

    def upload_attachment(self, db, user):
        params = parse_qs(urlparse(self.path).query)
        week = report_week(params.get("week", [None])[0])
        try:
            project_id = int(params.get("project_id", [""])[0])
        except ValueError:
            raise APIError("项目不正确") from None
        filename = params.get("filename", [""])[0].strip()
        if not filename or len(filename) > 180 or filename in (".", "..") or re.search(r'[\\/:*?"<>|\x00-\x1f\x7f]', filename):
            raise APIError("文件名不合法，最多 180 个字符且不能包含路径或特殊控制字符")
        try:
            size = int(self.headers.get("Content-Length", "0"))
        except ValueError:
            raise APIError("文件大小不正确") from None
        if self.headers.get("Transfer-Encoding") or not 0 < size <= MAX_ATTACHMENT_SIZE:
            raise APIError("请选择非空文件，每个文件最大 20 MB", 413)
        self.require_report_access(db, user, project_id, week)
        storage_key = secrets.token_hex(24)
        destination = self.server.uploads_dir / storage_key
        try:
            self.connection.settimeout(30)
            with destination.open("xb") as file:
                remaining = size
                while remaining:
                    chunk = self.rfile.read(min(remaining, 64 * 1024))
                    if not chunk:
                        raise APIError("文件上传不完整，请重新上传")
                    file.write(chunk)
                    remaining -= len(chunk)
            db.execute("BEGIN IMMEDIATE")
            self.require_report_access(db, user, project_id, week)
            count = db.execute("SELECT COUNT(*) FROM attachments WHERE user_id=? AND project_id=? AND week=?", (user["id"], project_id, week)).fetchone()[0]
            if count >= MAX_ATTACHMENTS * 2:
                raise APIError("本条周报暂存附件过多，请先提交整理附件后再上传")
            stamp = timestamp()
            row = db.execute("""INSERT INTO attachments(project_id,user_id,week,filename,storage_key,size,created_at,staged_at)
                VALUES (?,?,?,?,?,?,?,?)""", (project_id, user["id"], week, filename, storage_key, size, stamp, stamp))
            attachment_id = row.lastrowid
            db.commit()
        except TimeoutError:
            destination.unlink(missing_ok=True)
            raise APIError("文件上传超时，请重新上传", 408) from None
        except Exception:
            destination.unlink(missing_ok=True)
            raise
        self.send_data({"id": attachment_id, "project_id": project_id, "user_id": user["id"], "week": week,
            "filename": filename, "size": size, "attached": 0, "created_at": stamp}, 201)

    def download_attachment(self, db, user, attachment_id):
        item = db.execute("SELECT * FROM attachments WHERE id=?", (attachment_id,)).fetchone()
        if not item:
            raise APIError("附件不存在", 404)
        if item["user_id"] != user["id"]:
            allowed = item["attached"] and domain.can_read_report(domain.context(db,user), user, item)
            if not allowed:
                raise APIError("无权访问此附件", 403)
        path = self.server.uploads_dir / item["storage_key"]
        if not path.is_file():
            raise APIError("附件文件缺失，请联系管理员检查备份", 404)
        with path.open("rb") as file:
            self.send_file(file, path.stat().st_size, item["filename"])

    def save_coordination(self, db, user, report_id, data):
        completed = data.get('completed')
        expected = data.get('expected_coordination')
        if not isinstance(completed, bool) or not isinstance(expected, str):
            raise APIError('协调状态或事项内容不正确')
        db.execute('BEGIN IMMEDIATE')
        report = db.execute('SELECT * FROM reports WHERE id=?', (report_id,)).fetchone()
        if not report:
            raise APIError('周报记录不存在',404)
        ctx = domain.context(db,user)
        if not domain.can_manage_coordination(ctx,user,report):
            raise APIError('仅发起人、负责范围内的负责人和管理员可处理协调事项',403)
        if expected != report['coordination']:
            raise APIError('协调内容已更新，请刷新后再处理',409)
        if bool(report['coordination_completed']) != completed:
            db.execute('''UPDATE reports SET coordination_completed=?,
                coordination_completed_by=?,coordination_completed_at=? WHERE id=?''',
                (int(completed),user['id'] if completed else None,timestamp() if completed else '',report_id))
        db.commit()
        return self.send_data({'ok':True,'completed':completed})

    def save_reports(self, db, user, data):
        if not domain.permissions(user)["report_submit"]:
            raise APIError("没有周报填报权限", 403)
        week = report_week(data.get("week"))
        leave_percent = percentage(data.get("leave_percent", 0), "休假比例")
        leave_note = text_field(data, "leave_note", "休假或其他缺勤说明", 500, leave_percent > 0)
        other_entries = data.get("non_project_entries")
        if other_entries is None:
            other_entries = [{"category":"leave", "effort":leave_percent, "note":leave_note}] if leave_percent else []
        if not isinstance(other_entries,list) or len(other_entries)>3:
            raise APIError("非项目工作最多为休假、学习/研究/培训、其他三个分类")
        allocations, categories = [], set()
        for item in other_entries:
            if not isinstance(item,dict) or item.get("category") not in domain.CATEGORIES or item["category"] in categories:
                raise APIError("非项目分类不正确或重复")
            categories.add(item["category"])
            effort = percentage(item.get("effort"), "非项目比例")
            if effort <= 0:
                raise APIError("非项目比例必须大于 0%，不用的分类请移除")
            note = text_field(item,"note","非项目工作说明",500)
            allocations.append((user["id"],week,item["category"],effort,note))
        leave = next((r for r in allocations if r[2] == "leave"), None)
        leave_percent, leave_note = (leave[3],leave[4]) if leave else (0,"")
        entries = data.get("entries")
        if not isinstance(entries, list) or len(entries) > 200 or (not entries and not allocations):
            raise APIError("请添加项目或非项目工作条目")
        validated, seen, selected_attachments = [], set(), set()
        db.execute("BEGIN IMMEDIATE")
        for entry in entries:
            if not isinstance(entry, dict):
                raise APIError("填报内容格式不正确")
            project_id = integer(entry.get("project_id"), "项目")
            if project_id in seen:
                raise APIError("同一项目不能重复填报")
            seen.add(project_id)
            self.require_report_access(db, user, project_id, week)
            progress = percentage(entry.get("progress"), "开发进度")
            effort = percentage(entry.get("effort"), "投入比例")
            if leave_percent == 100:
                raise APIError("整周休假时请移除项目条目，历史开发进度会保留")
            note = text_field(entry, "note", "本周进展说明", 1000)
            # Old clients may omit these fields; new submissions use the complete weekly template.
            next_plan = text_field(entry,"next_plan","下周计划",1000,"next_plan" in entry)
            coordination = text_field(entry,"coordination","需要协调的工作",1000,False)
            validated.append((project_id, user["id"], week, progress, effort, note, timestamp(),next_plan,coordination))
            ids = entry.get("attachment_ids")
            if ids is None:
                ids = [row[0] for row in db.execute("SELECT id FROM attachments WHERE user_id=? AND project_id=? AND week=? AND attached=1", (user["id"], project_id, week))]
            if not isinstance(ids, list) or len(ids) > MAX_ATTACHMENTS:
                raise APIError("每条项目周报最多添加 10 个附件")
            for attachment_id in ids:
                attachment_id = integer(attachment_id, "附件")
                item = db.execute("SELECT * FROM attachments WHERE id=?", (attachment_id,)).fetchone()
                if not item or item["user_id"] != user["id"] or item["project_id"] != project_id or item["week"] != week:
                    raise APIError("附件不属于当前项目周报，或暂存已过期，请重新上传", 403)
                if not (self.server.uploads_dir / item["storage_key"]).is_file():
                    raise APIError("附件文件缺失，请重新上传")
                if attachment_id in selected_attachments:
                    raise APIError("不能重复添加同一附件")
                selected_attachments.add(attachment_id)
        if sum(row[4] for row in validated) + sum(row[3] for row in allocations) != 100:
            raise APIError("每周项目及非项目工作量合计必须为 100%")
        for row in list(db.execute("SELECT id, project_id FROM reports WHERE user_id=? AND week=?", (user["id"], week))):
            if row["project_id"] not in seen:
                db.execute("DELETE FROM reports WHERE id=?", (row["id"],))
        db.executemany("""INSERT INTO reports(project_id, user_id, week, progress, effort, note, updated_at,next_plan,coordination) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
            ON CONFLICT(project_id,user_id,week) DO UPDATE SET progress=excluded.progress,effort=excluded.effort,note=excluded.note,updated_at=excluded.updated_at,next_plan=excluded.next_plan,
            coordination_completed=CASE WHEN reports.coordination=excluded.coordination THEN reports.coordination_completed ELSE 0 END,
            coordination_completed_by=CASE WHEN reports.coordination=excluded.coordination THEN reports.coordination_completed_by ELSE NULL END,
            coordination_completed_at=CASE WHEN reports.coordination=excluded.coordination THEN reports.coordination_completed_at ELSE '' END,
            coordination=excluded.coordination""", validated)
        db.execute("DELETE FROM non_project_entries WHERE user_id=? AND week=?", (user["id"],week))
        db.executemany("INSERT INTO non_project_entries VALUES(?,?,?,?,?)", allocations)
        stamp = timestamp()
        db.execute("UPDATE attachments SET attached=0, staged_at=? WHERE user_id=? AND week=? AND attached=1", (stamp, user["id"], week))
        db.executemany("UPDATE attachments SET attached=1 WHERE id=?", [(aid,) for aid in selected_attachments])
        # Detached blobs remain briefly on disk so in-flight downloads/exports can finish.
        # Startup cleanup removes unreferenced blobs after seven days.
        db.execute("DELETE FROM attachments WHERE user_id=? AND week=? AND attached=0", (user["id"], week))
        db.execute("""INSERT INTO submissions(user_id,week,updated_at,leave_percent,leave_note) VALUES (?,?,?,?,?)
            ON CONFLICT(user_id,week) DO UPDATE SET updated_at=excluded.updated_at,leave_percent=excluded.leave_percent,leave_note=excluded.leave_note""",
            (user["id"], week, stamp, leave_percent, leave_note if leave_percent else ""))
        db.commit()
        self.send_data({"ok": True})

    def export(self, db):
        buffer = io.StringIO(newline="")
        writer = csv.writer(buffer)
        writer.writerow(["填报周（周一）", "项目", "成员", "部门", "开发进度(%)", "投入比例(%)", "本周工作成果", "更新时间", "记录类型", "休假比例(%)", "休假说明", "下周计划", "需要协调的工作", "项目路径", "附件"])
        projects = {r['id']:r for r in db.execute('SELECT * FROM projects')}
        def project_path(pid):
            names, seen = [], set()
            while pid in projects and pid not in seen:
                seen.add(pid)
                names.insert(0, projects[pid]['name'])
                pid = projects[pid]['parent_id']
            return ' / '.join(names)
        for row in db.execute("""SELECT r.week, p.name, u.name, u.department, r.progress, r.effort, r.note, r.updated_at,r.project_id,r.user_id,r.next_plan,r.coordination
            FROM reports r JOIN projects p ON p.id = r.project_id JOIN users u ON u.id = r.user_id
            ORDER BY r.week DESC, p.id, u.id"""):
            filenames = "；".join(a[0] for a in db.execute("SELECT filename FROM attachments WHERE attached=1 AND week=? AND project_id=? AND user_id=? ORDER BY id", (row[0], row[8], row[9])))
            writer.writerow(self.csv_safe([*row[:8], "项目填报", "", "", row[10],row[11],project_path(row[8]),filenames]))
        for row in db.execute("""SELECT n.*,u.name,u.department,s.updated_at FROM non_project_entries n
            JOIN users u ON u.id=n.user_id JOIN submissions s ON s.user_id=n.user_id AND s.week=n.week ORDER BY n.week DESC,u.id"""):
            writer.writerow(self.csv_safe([row['week'],'',row['name'],row['department'],'',row['effort'],row['note'],row['updated_at'],domain.CATEGORIES[row['category']],
                row['effort'] if row['category']=='leave' else '',row['note'] if row['category']=='leave' else '','','','','']))
        self.send_data(b"\xef\xbb\xbf" + buffer.getvalue().encode("utf-8"), content_type="text/csv; charset=utf-8",
            headers={"Content-Disposition": 'attachment; filename="qingzhou-reports.csv"'})

    @staticmethod
    def csv_safe(row):
        return [("'" + value if value.lstrip().startswith(("=", "+", "-", "@")) else value) if isinstance(value, str) else value for value in row]

    def export_attachments(self, db):
        rows = list(db.execute("""SELECT a.*,p.name AS project_name,u.name AS user_name FROM attachments a
            JOIN projects p ON p.id=a.project_id JOIN users u ON u.id=a.user_id WHERE a.attached=1 ORDER BY a.week,a.project_id,a.user_id,a.id"""))
        manifest = io.StringIO(newline="")
        writer = csv.writer(manifest)
        writer.writerow(["附件ID", "填报周", "项目ID", "项目", "成员ID", "成员", "原文件名", "文件大小（字节）", "上传时间", "压缩包内路径"])
        with tempfile.TemporaryFile() as bundle:
            with zipfile.ZipFile(bundle, "w", compression=zipfile.ZIP_DEFLATED, allowZip64=True) as archive:
                for item in rows:
                    path = self.server.uploads_dir / item["storage_key"]
                    if not path.is_file():
                        raise APIError("存在缺失附件，导出已中止，请联系管理员检查备份", 409)
                    archive_path = f"{item['week']}/project-{item['project_id']}/user-{item['user_id']}/{item['id']}-{item['filename']}"
                    archive.write(path, archive_path)
                    writer.writerow(self.csv_safe([item["id"], item["week"], item["project_id"], item["project_name"], item["user_id"], item["user_name"], item["filename"], item["size"], item["created_at"], archive_path]))
                archive.writestr("附件清单.csv", b"\xef\xbb\xbf" + manifest.getvalue().encode("utf-8"))
            size = bundle.tell()
            bundle.seek(0)
            self.send_file(bundle, size, "qingzhou-attachments.zip", "application/zip")

    def static(self, path):
        if path == "/":
            path = "/index.html"
        files = {"/index.html", "/app.js", "/management.js", "/overview.js", "/projects.js", "/styles.css", "/refinement.css", "/favicon.svg"}
        if path not in files:
            raise APIError("页面不存在", 404)
        file = ROOT / "static" / path.lstrip("/")
        if not file.is_file():
            raise APIError("页面不存在", 404)
        content_type = mimetypes.guess_type(str(file))[0] or "application/octet-stream"
        if file.suffix == ".js":
            content_type = "text/javascript"
        self.send_data(file.read_bytes(), content_type=content_type + "; charset=utf-8")


def main():
    load_env(ROOT / ".env")
    parser = argparse.ArgumentParser(description="轻舟 · 轻量项目进度系统")
    parser.add_argument("--host", default=os.environ.get("PMS_HOST", "127.0.0.1"))
    parser.add_argument("--port", type=int, default=int(os.environ.get("PMS_PORT", "8000")))
    parser.add_argument("--db", default=os.environ.get("PMS_DB", str(ROOT / "data" / "pms.sqlite3")))
    parser.add_argument("--uploads", default=os.environ.get("PMS_UPLOAD_DIR"), help="附件集中存储目录，默认在数据库旁的 attachments 目录")
    parser.add_argument("--sync-feishu", action="store_true", help="立即全量同步飞书通讯录，然后退出")
    args = parser.parse_args()
    try:
        config = FeishuConfig.from_env()
    except ValueError as exc:
        parser.error(str(exc))
    if args.sync_feishu:
        if config.mode != "feishu":
            parser.error("请先配置 PMS_AUTH_MODE=feishu")
        initialize(args.db)
        try:
            result = FeishuIntegration(config, str(Path(args.db).resolve()), connect).sync()
        except FeishuError as exc:
            parser.exit(1, f"同步失败：{exc}\n")
        print(json.dumps(result, ensure_ascii=False))
        return
    server = Server((args.host, args.port), args.db, config=config, uploads_dir=args.uploads)
    if server.feishu:
        server.feishu.start()
    print(f"轻舟已启动：http://{args.host}:{server.server_port}", flush=True)
    print(f"数据库：{server.db_path}", flush=True)
    print(f"附件目录：{server.uploads_dir}", flush=True)
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        pass
    finally:
        server.server_close()


if __name__ == "__main__":
    main()
