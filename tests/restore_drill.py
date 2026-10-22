"""Restore a complete backup into a new directory and verify it through HTTP."""
import argparse
import hashlib
import json
import sys
import threading
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from app import Handler, Server
from backup import restore, verify
from feishu import FeishuConfig
from server_acceptance import Client, read_admin


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("backup")
    parser.add_argument("--destination", required=True)
    parser.add_argument("--admin-file", required=True)
    parser.add_argument("--report", required=True)
    args = parser.parse_args()
    destination = Path(args.destination).resolve()
    manifest = restore(args.backup, destination/"pms.sqlite3", destination/"attachments")
    admin = read_admin(args.admin_file)
    checks = []
    for cycle in range(2):
        server = Server(("127.0.0.1", 0), destination/"pms.sqlite3", FeishuConfig())
        thread = threading.Thread(target=server.serve_forever, daemon=True)
        thread.start()
        try:
            client = Client(f"http://127.0.0.1:{server.server_port}")
            state = client.login(admin["username"], admin["password"])
            for table in ("users", "projects", "reports", "submissions", "attachments"):
                assert len(state[table]) == manifest["counts"][table], table+" count mismatch"
            files = {f["storage_key"]: f for f in manifest["files"]}
            from contextlib import closing
            import sqlite3
            with closing(sqlite3.connect(destination/"pms.sqlite3")) as db:
                for aid, key in db.execute("SELECT id,storage_key FROM attachments WHERE attached=1"):
                    raw = client.request("GET", f"/api/attachments/{aid}")
                    assert hashlib.sha256(raw).hexdigest() == files[key]["sha256"], "download hash mismatch"
            client.request("GET", "/api/export")
            client.request("GET", "/api/export/attachments")
            checks.append(f"cycle {cycle+1}: restored login, all counts, attachment bytes and exports passed")
        finally:
            server.shutdown()
            server.server_close()
            thread.join()
    result = {"passed": True, "counts": manifest["counts"], "checks": checks,
              "destination": str(destination), "sqlite_version": sqlite3.sqlite_version}
    Path(args.report).write_text(json.dumps(result, ensure_ascii=False, indent=2), encoding="utf-8")
    print(json.dumps(result, ensure_ascii=False))


if __name__ == "__main__":
    main()
