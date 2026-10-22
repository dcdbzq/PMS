"""Concurrent real-file uploads/downloads and ZIP checks on the existing QA cohort.

Uses 20 QA members outside the long-soak group; preserves report values and leave.
One file is exactly 20 MiB, the other nineteen are 1 MiB. Replaces only previous
files created by this script, identified by the reserved filename prefix.
"""
import argparse
import copy
import hashlib
import io
import json
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
from urllib.parse import urlencode
import zipfile

from server_acceptance import Client, Metrics, read_admin


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--admin-file", required=True)
    parser.add_argument("--accounts-file", required=True)
    parser.add_argument("--out", default="test-results/server-attachments")
    args = parser.parse_args()
    out = Path(args.out)
    out.mkdir(parents=True, exist_ok=True)
    cohort = json.loads(Path(args.accounts_file).read_text(encoding="utf-8"))
    fields = read_admin(args.admin_file)
    metrics = Metrics()
    admin = Client(cohort["url"], metrics, "attachment-export")
    before = admin.login(fields["username"], fields["password"])
    week = before["week"]
    accounts = []
    for account in cohort["accounts"][20:]:
        submitted = next(s for s in before["submissions"] if s["user_id"] == account["id"] and s["week"] == week)
        if submitted["leave_percent"] < 100:
            accounts.append(account)
        if len(accounts) == 20:
            break
    assert len(accounts) == 20

    def worker(index):
        account = accounts[index]
        client = Client(cohort["url"], metrics, "concurrent-attachments", timeout=60)
        client.login(account["username"], account["password"])
        submission = next(s for s in before["submissions"] if s["user_id"] == account["id"] and s["week"] == week)
        rows = [r for r in before["reports"] if r["user_id"] == account["id"] and r["week"] == week]
        entries = [{k: r[k] for k in ("project_id", "progress", "effort", "note")} for r in rows]
        pid = entries[0]["project_id"]
        size = (20 if index == 0 else 1)*1024*1024
        block = hashlib.sha256(f"{cohort['prefix']}:{account['id']}".encode()).digest()
        content = block*(size//len(block))
        name = f"并发验收-{index+1:02}-{size//1048576}MB.bin"
        query = urlencode({"project_id": pid, "week": week, "filename": name})
        if index == 0:
            client.request("POST", "/api/attachments?"+query, b"", expected=(413,),
                           headers={"Content-Type": "application/octet-stream", "Content-Length": str(20*1024*1024+1)})
        uploaded = client.request("POST", "/api/attachments?"+query, content, expected=(201,),
                                  headers={"Content-Type": "application/octet-stream"})
        assert uploaded["size"] == size
        for entry in entries:
            entry["attachment_ids"] = [a["id"] for a in before["attachments"] if a["user_id"] == account["id"]
                and a["week"] == week and a["project_id"] == entry["project_id"] and a["attached"] and not a["filename"].startswith("并发验收-")]
        entries[0]["attachment_ids"].append(uploaded["id"])
        payload = {"week": week, "entries": entries, "leave_percent": submission["leave_percent"], "leave_note": submission["leave_note"]}
        client.request("PUT", "/api/reports", payload)
        downloaded = client.request("GET", f"/api/attachments/{uploaded['id']}")
        expected = hashlib.sha256(content).hexdigest()
        assert hashlib.sha256(downloaded).hexdigest() == expected
        return {"id": uploaded["id"], "size": size, "sha256": expected, "filename": name}

    with ThreadPoolExecutor(max_workers=5) as pool:
        files = list(pool.map(worker, range(20)))
    after = admin.request("GET", "/api/state")
    selected = {a["id"] for a in accounts}
    before_rows = {(r["user_id"], r["week"], r["project_id"]): (r["progress"], r["effort"], r["note"])
                   for r in before["reports"] if r["user_id"] in selected}
    after_rows = {(r["user_id"], r["week"], r["project_id"]): (r["progress"], r["effort"], r["note"])
                  for r in after["reports"] if r["user_id"] in selected}
    assert before_rows == after_rows, "Concurrent attachment submissions changed report values"
    archive_bytes = admin.request("GET", "/api/export/attachments")
    with zipfile.ZipFile(io.BytesIO(archive_bytes)) as archive:
        assert archive.testzip() is None
        for file in files:
            names = [n for n in archive.namelist() if n.endswith(f"{file['id']}-{file['filename']}")]
            assert len(names) == 1
            assert hashlib.sha256(archive.read(names[0])).hexdigest() == file["sha256"]
    csv_content = admin.request("GET", "/api/export").decode("utf-8-sig")
    assert all(file["filename"] in csv_content for file in files)
    result = {"passed": True, "members": 20, "parallel_uploads": 5, "files": files,
              "total_bytes": sum(f["size"] for f in files), "boundary_20mb_passed": True,
              "oversize_rejected": True, "all_download_and_zip_hashes_match": True,
              "report_values_unchanged": True, "metrics": metrics.summary()}
    (out/"report.json").write_text(json.dumps(result, ensure_ascii=False, indent=2), encoding="utf-8")
    print(json.dumps({k: v for k, v in result.items() if k not in ("files", "metrics")}, ensure_ascii=False))


if __name__ == "__main__":
    main()
