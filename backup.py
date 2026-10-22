"""SQLite online backup plus immutable attachment files; verified, offline restore.

Examples:
  python backup.py create --db /srv/pms/data/pms.sqlite3 --out /backups/pms-20261015
  python backup.py verify /backups/pms-20261015
  python backup.py restore /backups/pms-20261015 --db /restore/pms.sqlite3

Backups contain passwords/session data. Store them outside the web/repository root.
Restore requires a stopped application and new, nonexistent destination paths.
"""
from __future__ import annotations

import argparse
import hashlib
import json
import os
import re
import shutil
import sqlite3
import tempfile
from contextlib import closing
from datetime import datetime, timedelta, timezone
from pathlib import Path

KEY = re.compile(r"[0-9a-f]{48}")


def digest(path):
    with Path(path).open("rb") as file:
        return hashlib.file_digest(file, "sha256").hexdigest() if hasattr(hashlib, "file_digest") else _digest(file)


def _digest(file):
    value = hashlib.sha256()
    for chunk in iter(lambda: file.read(1024*1024), b""):
        value.update(chunk)
    return value.hexdigest()


def inspect_database(path):
    with closing(sqlite3.connect(Path(path).resolve().as_uri()+"?mode=ro", uri=True)) as db:
        if db.execute("PRAGMA quick_check").fetchall() != [("ok",)]:
            raise ValueError("SQLite integrity check failed")
        if db.execute("PRAGMA foreign_key_check").fetchone():
            raise ValueError("SQLite foreign-key check failed")
        rows = db.execute("SELECT storage_key,size FROM attachments ORDER BY id").fetchall()
        for key, size in rows:
            if not isinstance(key, str) or not KEY.fullmatch(key) or size < 1:
                raise ValueError("Invalid attachment metadata")
        counts = {table: db.execute(f"SELECT COUNT(*) FROM {table}").fetchone()[0]
                  for table in ("users", "projects", "memberships", "reports", "submissions", "attachments")}
        return rows, counts


def create(db_path, uploads, output):
    source, uploads, output = Path(db_path).resolve(), Path(uploads).resolve(), Path(output).resolve()
    if not source.is_file():
        raise ValueError("Source database does not exist")
    if output.exists() or output.is_relative_to(uploads):
        raise ValueError("Backup destination must be new and outside the attachment directory")
    output.parent.mkdir(parents=True, exist_ok=True)
    # Only rename a completed and verified backup into its public final path.
    with tempfile.TemporaryDirectory(prefix=".pms-backup-", dir=output.parent) as temporary:
        staging = Path(temporary)/"snapshot"
        staging.mkdir(mode=0o700)
        (staging/"attachments").mkdir(mode=0o700)
        target = staging/"pms.sqlite3"
        with closing(sqlite3.connect(source.as_uri()+"?mode=ro", uri=True)) as src, closing(sqlite3.connect(target)) as dst:
            src.backup(dst, pages=256, sleep=.05)
            # Make the backed-up database self contained, with no WAL sidecars.
            dst.execute("PRAGMA journal_mode=DELETE")
        rows, counts = inspect_database(target)
        files = []
        # Uploads use random immutable names. The application retains detached
        # blobs for seven days, so writes can continue during this snapshot copy.
        # If cleanup/storage loss races this operation, fail without publishing it.
        for key, size in rows:
            origin, destination = uploads/key, staging/"attachments"/key
            if origin.is_symlink():
                raise ValueError("Attachment symlinks are not supported")
            shutil.copyfile(origin, destination)
            if destination.stat().st_size != size:
                raise ValueError("Attachment size mismatch")
            files.append({"storage_key": key, "size": size, "sha256": digest(destination)})
            os.chmod(destination, 0o600)
        manifest = {"format": 1, "created_at": datetime.now(timezone(timedelta(hours=8))).isoformat(timespec="seconds"),
                    "database_sha256": digest(target), "counts": counts, "files": files}
        (staging/"manifest.json").write_text(json.dumps(manifest, ensure_ascii=False, indent=2), encoding="utf-8")
        os.chmod(target, 0o600)
        os.chmod(staging/"manifest.json", 0o600)
        verify(staging)
        staging.rename(output)
    return manifest


def verify(backup):
    root = Path(backup).resolve()
    manifest = json.loads((root/"manifest.json").read_text(encoding="utf-8"))
    if manifest.get("format") != 1:
        raise ValueError("Unsupported backup format")
    database = root/"pms.sqlite3"
    if digest(database) != manifest["database_sha256"]:
        raise ValueError("Database SHA-256 mismatch")
    rows, counts = inspect_database(database)
    wanted = {key: size for key, size in rows}
    entries = manifest["files"]
    if len(entries) != len(wanted) or {r["storage_key"]: r["size"] for r in entries} != wanted or counts != manifest["counts"]:
        raise ValueError("Backup manifest does not match database metadata")
    for entry in entries:
        path = root/"attachments"/entry["storage_key"]
        if path.is_symlink() or path.stat().st_size != entry["size"] or digest(path) != entry["sha256"]:
            raise ValueError("Attachment integrity check failed")
    return manifest


def restore(backup, database, uploads):
    source, database, uploads = Path(backup).resolve(), Path(database).resolve(), Path(uploads).resolve()
    manifest = verify(source)
    if database.exists() or uploads.exists() or database.is_relative_to(uploads):
        raise ValueError("Restore destinations must not exist; stop the app and use new paths")
    database.parent.mkdir(parents=True, exist_ok=True)
    uploads.parent.mkdir(parents=True, exist_ok=True)
    if database.with_name(database.name+"-wal").exists() or database.with_name(database.name+"-shm").exists():
        raise ValueError("SQLite sidecars already exist at restore destination")
    with tempfile.TemporaryDirectory(prefix=".pms-restore-", dir=database.parent) as temporary:
        staged_db = Path(temporary)/"database"
        shutil.copyfile(source/"pms.sqlite3", staged_db)
        if digest(staged_db) != manifest["database_sha256"]:
            raise ValueError("Restored database hash mismatch")
        os.chmod(staged_db, 0o600)
        with tempfile.TemporaryDirectory(prefix=".pms-attachments-", dir=uploads.parent) as blobs_temp:
            staged_blobs = Path(blobs_temp)/"attachments"
            shutil.copytree(source/"attachments", staged_blobs)
            for entry in manifest["files"]:
                if digest(staged_blobs/entry["storage_key"]) != entry["sha256"]:
                    raise ValueError("Restored attachment hash mismatch")
            staged_blobs.rename(uploads)
        staged_db.rename(database)
    return manifest


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    sub = parser.add_subparsers(dest="command", required=True)
    create_parser = sub.add_parser("create")
    create_parser.add_argument("--db", required=True)
    create_parser.add_argument("--uploads")
    create_parser.add_argument("--out", required=True)
    verify_parser = sub.add_parser("verify")
    verify_parser.add_argument("backup")
    restore_parser = sub.add_parser("restore")
    restore_parser.add_argument("backup")
    restore_parser.add_argument("--db", required=True)
    restore_parser.add_argument("--uploads")
    args = parser.parse_args()
    try:
        if args.command == "verify":
            result = verify(args.backup)
        else:
            uploads = args.uploads or str(Path(args.db).resolve().parent/"attachments")
            result = create(args.db, uploads, args.out) if args.command == "create" else restore(args.backup, args.db, uploads)
        print(json.dumps({"ok": True, "command": args.command, "counts": result["counts"], "files": len(result["files"])}, ensure_ascii=False))
    except (ValueError, OSError, sqlite3.Error, KeyError, TypeError) as exc:
        parser.exit(1, f"Backup operation failed: {exc}\n")


if __name__ == "__main__":
    main()
