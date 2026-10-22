"""Prepare four manual trial accounts independent of the automated QA cohort."""
import argparse
import json
import secrets
from datetime import date, timedelta
from pathlib import Path

from server_acceptance import Client, ROOT, read_admin


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--admin-file", required=True)
    parser.add_argument("--credentials-out", required=True)
    parser.add_argument("--guide-out", required=True)
    args = parser.parse_args()
    output, guide = Path(args.credentials_out).resolve(), Path(args.guide_out).resolve()
    if output.is_relative_to(ROOT) or guide.is_relative_to(ROOT):
        parser.error("Keep plaintext credentials outside the repository")
    fields = read_admin(args.admin_file)
    admin = Client(fields["url"])
    state = admin.login(fields["username"], fields["password"])
    if output.exists():
        fixture = json.loads(output.read_text(encoding="utf-8"))
    else:
        fixture = {"url": fields["url"], "accounts": [], "projects": []}
        for suffix, name, role in [("admin", "体验管理员", "admin"), ("owner_a", "体验负责人甲", "member"),
                                   ("owner_b", "体验负责人乙", "member"), ("member", "体验成员", "member")]:
            fixture["accounts"].append({"username": "trial1015_"+suffix, "password": secrets.token_urlsafe(18),
                "name": name, "department": "人工体验", "role": role, "active": True})
        output.parent.mkdir(parents=True, exist_ok=True)
        output.write_text(json.dumps(fixture, ensure_ascii=False, indent=2), encoding="utf-8")
    by_username = {u["username"]: u for u in state["users"]}
    for account in fixture["accounts"]:
        if account["username"] in by_username:
            account["id"] = by_username[account["username"]]["id"]
            Client(fields["url"]).login(account["username"], account["password"])
        else:
            account["id"] = admin.request("POST", "/api/users", account)["id"]
    output.write_text(json.dumps(fixture, ensure_ascii=False, indent=2), encoding="utf-8")
    if not fixture["projects"]:
        for index, name in enumerate(["【人工体验】客户协作平台", "【人工体验】数据看板"]):
            project = {"name": name, "description": "用于人工体验，与自动化长测数据独立，可自由编辑和填报。",
                "owner_id": fixture["accounts"][index+1]["id"], "member_ids": [a["id"] for a in fixture["accounts"][1:]],
                "start_date": "2026-07-01", "due_date": "2026-12-31", "priority": "normal", "archived": False}
            project["id"] = admin.request("POST", "/api/projects", project)["id"]
            fixture["projects"].append(project)
            output.write_text(json.dumps(fixture, ensure_ascii=False, indent=2), encoding="utf-8")
        week = (date.fromisoformat(state["week"])-timedelta(weeks=1)).isoformat()
        for i, account in enumerate(fixture["accounts"][1:]):
            client = Client(fields["url"])
            client.login(account["username"], account["password"])
            chosen = fixture["projects"] if i == 2 else [fixture["projects"][i]]
            entries = [{"project_id": p["id"], "progress": 25+i*5, "effort": 50 if len(chosen)==2 else 100,
                        "note": "人工体验预置的上周记录；本周可自行填写进度、投入和附件。"} for p in chosen]
            client.request("PUT", "/api/reports", {"week": week, "entries": entries})
    lines = ["轻舟 PMS 人工体验账号（密码请保密）", "地址："+fixture["url"], "",
        "以下四个账号与自动化长测账号独立，可以自由创建项目、调整成员和填报。",
        "请在“人工体验”账号和项目之间操作；不要修改“验收”账号、项目或历史周报，以免影响长测核对。", ""]
    descriptions = ["管理员：可新建账号、项目并分配成员", "负责人甲：负责客户协作平台，参与两个项目",
                    "负责人乙：负责数据看板，参与两个项目", "普通成员：参与两个项目，可填报及上传附件"]
    for account, description in zip(fixture["accounts"], descriptions):
        lines += [description, "姓名："+account["name"], "用户名："+account["username"], "密码："+account["password"], ""]
    lines += ["已预置上周记录，本周留空供自己填写。", "建议：四个独立浏览器会话分别登录，体验负责人调整成员、普通成员填报和管理员总览。"]
    guide.write_text("\n".join(lines)+"\n", encoding="utf-8")
    print(json.dumps({"created_accounts": len(fixture["accounts"]), "manual_projects": len(fixture["projects"]),
                      "guide": str(guide)}, ensure_ascii=False))


if __name__ == "__main__":
    main()
