# 部署与数据管理

## 本地与服务器运行

学习使用直接运行 `python app.py`，默认只监听本机。启动后先在本机创建管理员，再配置团队成员和项目。应用不会预置管理员或演示业务数据。

服务端可以把数据放在源码目录之外：

```sh
python app.py --host 127.0.0.1 --port 8000 --db ../pms-data/pms.sqlite3 --uploads ../pms-data/attachments
```

相对路径相对于启动时的工作目录；长期运行请在服务配置中固定工作目录或使用绝对路径。环境变量和 `.env` 的选项见 [README](../README.md)。

局域网运行时使用 `--host 0.0.0.0`，通过运行机器的实际地址访问。服务器部署应配置单个应用实例、进程守护、访问控制和定期备份。HTTPS 由反向代理处理，并设置 `PMS_SECURE_COOKIE=1`；代理需允许 20 MB 的单文件上传并设置合适的超时。

内置 HTTP 服务适合学习和轻量部署。更大规模或直接面向公网的服务需要结合实际场景完善部署架构与测试。

## 数据位置

- 默认数据库：项目目录下的 `data/pms.sqlite3`。
- 默认附件：数据库旁的 `attachments/`。
- SQLite 采用 WAL，运行时可能同时存在 `-wal`、`-shm` 文件。
- `.env` 保存本机配置，可含飞书凭证，不能公开提交。

数据库中包含成员信息、密码摘要、周报和会话信息；附件、导出及备份同样可能包含私有数据。这些内容应限制文件访问权限并独立保存。

## 完整备份

`backup.py` 使用 SQLite 在线备份并复制附件，同时生成校验清单。备份输出目录必须尚不存在，且不能位于附件目录中。示例均使用虚构的相对路径：

```sh
python backup.py create --db ../pms-data/pms.sqlite3 --uploads ../pms-data/attachments --out ../pms-backups/snapshot-001
python backup.py verify ../pms-backups/snapshot-001
```

使用默认附件目录时可省略 `--uploads`。在线备份时应用可以继续运行；直接复制正在使用的主数据库文件不能代替在线备份。CSV 与附件 ZIP 属于业务导出，不能完整恢复系统。

需要定时备份时可用操作系统的任务调度器调用 `scripts/daily_backup.py --root <运行目录>`。该脚本从进程环境读取 `PMS_DB` 和 `PMS_UPLOAD_DIR`，**不会自行加载 `.env`**；默认读取运行目录中的 `data/pms.sqlite3`。备份写入该目录的 `backups/verified-*`，不会自动删除旧备份，应自行安排保留策略和磁盘监控。

`.env`、反向代理与服务配置需要另外安全备份，数据快照不包含这些部署配置。

## 恢复

先停止应用。使用全新的数据库路径及附件目录；恢复工具拒绝覆盖已有目标：

```sh
python backup.py restore ../pms-backups/snapshot-001 --db ../pms-restored/pms.sqlite3 --uploads ../pms-restored/attachments
python app.py --db ../pms-restored/pms.sqlite3 --uploads ../pms-restored/attachments
```

验证账号、项目、历史周报和附件后，再调整正式服务配置。恢复环境也包含原有账号与会话数据，应限制访问。

## 升级

1. 停止服务，完整备份数据库、附件、原代码与本机 `.env`，核验快照。
2. 更新源码和整个 `static/` 目录，保留原数据目录及 `.env`。
3. 使用原有数据路径启动，程序自动补充兼容字段和表。
4. 检查登录、权限、历史周报、附件和统计后再恢复访问。

如需回退，停止服务后恢复成套的旧代码、数据库和附件。不要仅回退代码而继续写入已迁移的数据。
