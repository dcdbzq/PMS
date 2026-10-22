"""Run the same company-scale acceptance suite against an isolated local database."""
import argparse
import sys
import tempfile
import threading
from pathlib import Path
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from app import Handler, Server
from feishu import FeishuConfig
from server_acceptance import Acceptance, Client


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--weeks", type=int, default=26)
    parser.add_argument("--soak-seconds", type=int, default=30)
    parser.add_argument("--out", default="test-results/local-capacity")
    parser.add_argument("--check-reuse", action="store_true")
    cli = parser.parse_args()
    with tempfile.TemporaryDirectory() as temp, patch.object(Handler, "log_message", lambda *_: None):
        root = Path(temp)
        server = Server(("127.0.0.1", 0), root/"pms.sqlite3", FeishuConfig())
        thread = threading.Thread(target=server.serve_forever, daemon=True)
        thread.start()
        url = f"http://127.0.0.1:{server.server_port}"
        admin_file = root/"admin.txt"
        admin_file.write_text(f"URL: {url}\nUsername: admin\nPassword: local-acceptance-123\n")
        Client(url).request("POST", "/api/setup", {"name": "验收管理员", "username": "admin", "password": "local-acceptance-123"})
        args = argparse.Namespace(url=url, admin_file=str(admin_file), credentials_out=str(root/"qa.json"),
            out=cli.out, users=100, projects=20, weeks=cli.weeks, concurrency=[10, 25, 50, 100],
            soak_seconds=cli.soak_seconds, read_p95_ms=2000, write_p95_ms=3000)
        try:
            passed = Acceptance(args).run()
            if passed and cli.check_reuse:
                args.reuse_cohort = True
                args.out += "-reuse"
                args.soak_seconds = 0
                args.concurrency = [10, 100]
                passed = Acceptance(args).run()
        finally:
            server.shutdown()
            server.server_close()
            thread.join()
        return 0 if passed else 1


if __name__ == "__main__":
    sys.exit(main())
