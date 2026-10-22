"""Create and verify a complete scheduled backup; never delete existing backups."""
import argparse
import json
import os
import sys
from datetime import datetime, timedelta, timezone
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from backup import create


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--root", required=True)
    args = parser.parse_args()
    os.umask(0o077)
    root = Path(args.root).resolve()
    database = Path(os.environ.get("PMS_DB", str(root/"data/pms.sqlite3")))
    uploads = Path(os.environ.get("PMS_UPLOAD_DIR", str(database.parent/"attachments")))
    stamp = datetime.now(timezone(timedelta(hours=8))).strftime("%Y%m%d-%H%M%S-%f")
    output = root/"backups"/("verified-"+stamp)
    manifest = create(database, uploads, output)
    print(json.dumps({"ok": True, "backup": str(output), "counts": manifest["counts"]}, ensure_ascii=False))


if __name__ == "__main__":
    main()
