# 轻舟 PMS · 项目与周报管理系统

一个使用 **Python 标准库、SQLite 和原生 HTML/CSS/JavaScript** 实现的轻量项目管理系统，提供多级项目、周报填报、人员权限和投入统计。运行时无需安装第三方 Python 包，也无需前端构建工具，适合学习 HTTP 服务、业务权限、SQLite 数据管理和前后端交互。

仓库只包含源码、通用文档和自动化测试。数据库、上传附件、真实账号、环境密钥及内部部署记录均不随项目发布。首次启动为空白系统，由使用者自行创建管理员。

## 功能

- 多级项目与子项目，配置负责人、参与人、查看人及参与起止日期。
- 按周填写成果、下周计划、协调事项、累计进度、投入比例和附件。
- 支持休假、学习/研究/培训及其他非项目工作；每周工作量合计必须为 100%。
- 个人、部门及公司统计，按角色展示项目、周报、未填报人员和待协调事项。
- 项目负责人和部门负责人按授权管理项目；跨部门数据按权限过滤。
- 协调事项完成与重新打开，保留处理人和时间。
- 本地账号、密码管理和细分权限；可选飞书 OAuth 登录及通讯录同步。
- 历史周报、草稿、附件下载、CSV/附件 ZIP 导出、完整备份与恢复。
- 响应式页面，支持桌面及手机浏览器。

详细规则见 [使用与业务规则](docs/weekly-wbs-upgrade.md)。

## 快速开始

需要 **Python 3.10 或更高版本**。Windows、Linux 和 macOS 均可使用；部分 Linux 发行版可能需要另行安装 Python 的 SQLite 支持。

```sh
git clone https://github.com/dcdbzq/PMS.git
cd PMS
python app.py
```

部分系统需将 `python` 替换为 `python3`。也可以在 GitHub 点击 **Code → Download ZIP**，解压后在项目目录运行 `python app.py`。

打开 <http://127.0.0.1:8000>，按页面提示创建管理员，随后添加团队成员、创建项目并配置参与人。成员登录后在「我的填报」中填写周报。**没有预置账号或通用默认密码。**

默认采用本地账号模式，不需要 `.env` 或飞书凭证。测试脚本中的账号和密码只用于临时数据库，不是应用的默认登录凭证。

## 配置

可将 `.env.example` 复制为 `.env` 后编辑；现有进程环境变量优先于 `.env`，命令行参数优先于相应环境变量。复制命令：

```powershell
# Windows PowerShell
Copy-Item .env.example .env
```

```sh
# Linux / macOS
cp .env.example .env
```

| 配置 | 默认值 / 说明 |
| --- | --- |
| `PMS_AUTH_MODE` | `local`；可选 `feishu` |
| `PMS_HOST` | `127.0.0.1`，对应 `--host` |
| `PMS_PORT` | `8000`，对应 `--port` |
| `PMS_DB` | 项目目录下的 `data/pms.sqlite3`，对应 `--db` |
| `PMS_UPLOAD_DIR` | 数据库旁的 `attachments/`，对应 `--uploads` |
| `PMS_SECURE_COOKIE` | 本机 HTTP 使用 `0`，HTTPS 部署设为 `1` |

例如，使用独立数据目录启动：

```sh
python app.py --host 127.0.0.1 --port 8080 --db ../pms-data/pms.sqlite3
```

局域网访问可使用 `--host 0.0.0.0`，通过运行机器的实际地址访问。请在开放网络访问前先在本机完成管理员初始化，并配置合适的访问范围。

本项目使用轻量 HTTP 服务。服务器部署方式、HTTPS、数据迁移及备份恢复见 [部署与数据管理](docs/deployment.md)。飞书默认关闭；可选配置见 [飞书接入](docs/feishu-integration.md)，真实企业接入需要自行配置应用及权限。

## 测试

标准库集成测试使用临时数据库，不访问已有业务数据：

```sh
python -m unittest discover -s tests -v
```

可选浏览器测试需要 Playwright 和 Google Chrome。这些依赖仅用于测试：

```sh
python -m pip install -r requirements-dev.txt
python tests/browser_smoke.py
python tests/browser_roles.py
python tests/browser_coordination.py
```

测试说明及容量验收入口见 [测试指南](docs/pre-release-testing.md)。测试生成的截图、日志及报告保存在已忽略的 `test-results/`。

## 代码结构

```text
app.py                  HTTP 服务、登录、业务接口、校验及文件处理
domain.py               多级项目、权限、统计及兼容迁移
feishu.py               可选飞书身份认证及通讯录同步
backup.py               SQLite 在线备份、附件快照、校验和恢复
static/                 原生前端页面、脚本、样式及图标
scripts/daily_backup.py 完整备份的定时任务入口
tests/                  HTTP 集成、权限、备份及浏览器测试
docs/                   使用、架构、部署和测试文档
.env.example            不含真实凭证的配置模板
```

建议从 [架构说明](docs/architecture.md) 入手，再结合 `tests/test_app.py`、`tests/test_role_scopes.py` 和 `tests/test_backup.py` 学习请求处理、权限边界与数据恢复。

## 参与改进

欢迎通过 Issue 反馈问题或提交 Pull Request。提交前运行集成测试；涉及前端交互时运行相应浏览器测试。示例和复现材料请使用虚构数据，不要提交真实成员信息、周报、数据库、附件、Cookie 或密钥。
