# 测试指南

## 集成测试

在项目根目录运行，使用 Python 标准库即可：

```sh
python -m unittest discover -s tests -v
```

覆盖本地账号、HTTP 接口、输入校验、会话、CSRF、多级项目、周报、角色与跨部门权限、协调事项、附件和备份恢复。飞书测试使用模拟 API，不需要真实凭证。测试使用临时数据库，不修改默认 `data/` 目录。

## 浏览器测试

安装 Google Chrome，并安装测试依赖：

```sh
python -m pip install -r requirements-dev.txt
python tests/browser_smoke.py
python tests/browser_roles.py
python tests/browser_coordination.py
```

脚本启动本机临时服务，检查桌面及手机页面、项目树、填报、权限、导出和协调事项。`browser_smoke.py` 是 `browser_management.py` 的入口。其他专项测试包括 `browser_daily.py` 和使用模拟飞书服务的 `browser_feishu.py`。

截图和报告位于 `test-results/`，已从 Git 中忽略。测试用账号及密码仅对脚本创建的临时实例有效。

## 可选容量验收

本机隔离容量测试：

```sh
python tests/local_acceptance.py --weeks 4 --soak-seconds 30 --out test-results/local-capacity
```

该测试创建临时数据库及约 100 个虚构人员，测试结束后清理临时数据。生成的性能结果仅代表运行机器和该测试场景，不能当作任意部署环境的容量保证。

`tests/server_*.py`、`tests/prepare_manual_trial.py` 和 `tests/prepare_role_trial.py` 是面向指定测试实例的高级验收工具。先查看各脚本的 `--help`，仅对自己控制且允许写入测试数据的实例运行。部分工具会创建持久测试账号、项目和周报，应使用独立测试实例。

凭证输入和生成的账号文件应放在仓库外。不要提交账号文件、包含真实内容的截图、数据库、测试导出或日志。

## 手工检查建议

1. 从空数据库启动并创建管理员，添加虚构部门、负责人及普通成员。
2. 创建父子项目，分别配置参与人和查看人，检查不同身份的数据范围。
3. 填写项目与非项目工作，确认 100% 校验、草稿、历史周补填及附件行为。
4. 检查跨部门统计、协调事项、CSV 和附件导出。
5. 在手机尺寸下检查导航、表单与项目层级。
6. 对虚构数据创建完整备份，核验后在全新路径恢复并检查附件。

实际数据升级及备份恢复步骤见 [部署与数据管理](deployment.md)。
