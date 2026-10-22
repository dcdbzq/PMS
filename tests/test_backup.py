"""Full SQLite and attachment recovery, including a real restored HTTP download."""
import tempfile
import threading
import unittest
from pathlib import Path

from app import Server, current_week
from backup import create, restore, verify
from feishu import FeishuConfig
from test_app import Client


class BackupTests(unittest.TestCase):
    def test_online_backup_restores_login_reports_sessions_and_attachment(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            server = Server(("127.0.0.1", 0), root/"live"/"pms.sqlite3", FeishuConfig())
            thread = threading.Thread(target=server.serve_forever, daemon=True)
            thread.start()
            try:
                client = Client(server.server_port)
                self.assertEqual(client.request("POST", "/api/setup", {"name": "备份验证", "username": "backup_admin", "password": "restore-password-123"})[0], 200)
                client.state()
                status, person = client.request('POST','/api/users',dict(name='填报成员',username='backup_worker',password='worker-password-123',department='研发部',role='member',active=True))
                self.assertEqual(status,200)
                reporter=Client(server.server_port)
                self.assertEqual(reporter.request('POST','/api/login',dict(username='backup_worker',password='worker-password-123'))[0],200)
                reporter.state()
                status, result = client.request("POST", "/api/projects", {"name": "恢复项目", "description": "", "owner_id": 1,
                    "start_date": "2026-01-01", "due_date": "2027-12-31", "member_ids": [person['id']]})
                self.assertEqual(status, 200)
                pid = result["id"]
                content = "恢复演练附件内容".encode()
                query = f"/api/attachments?project_id={pid}&week={current_week()}&filename=recovery.txt"
                status, attachment = reporter.request("POST", query, content, {"Content-Type": "application/octet-stream"})
                self.assertEqual(status, 201)
                self.assertEqual(reporter.request("PUT", "/api/reports", {"week": current_week(), "entries": [
                    {"project_id": pid, "progress": 65, "effort": 100, "note": "备份时的记录", "coordination":"请协助核对恢复结果", "attachment_ids": [attachment["id"]]}]})[0], 200)
                report=reporter.state()[1]['reports'][0]
                self.assertEqual(reporter.request('PUT',f"/api/reports/{report['id']}/coordination",dict(completed=True,expected_coordination=report['coordination']))[0],200)
                snapshot = root/"backup"
                manifest = create(server.db_path, server.uploads_dir, snapshot)
                self.assertEqual(manifest["counts"]["reports"], 1)
                self.assertEqual(verify(snapshot)["counts"]["attachments"], 1)
                recovered_db = root/"recovered"/"pms.sqlite3"
                recovered_blobs = recovered_db.parent/"attachments"
                restore(snapshot, recovered_db, recovered_blobs)
                replacement = Server(("127.0.0.1", 0), recovered_db, FeishuConfig())
                recovered_thread = threading.Thread(target=replacement.serve_forever, daemon=True)
                recovered_thread.start()
                try:
                    recovered = Client(replacement.server_port)
                    self.assertEqual(recovered.request("POST", "/api/login", {"username": "backup_admin", "password": "restore-password-123"})[0], 200)
                    self.assertEqual(recovered.state()[1]["reports"][0]["progress"], 65)
                    self.assertEqual(recovered.state()[1]['reports'][0]['coordination_completed'],1)
                    self.assertEqual(recovered.state()[1]['reports'][0]['coordination_completed_by'],person['id'])
                    self.assertEqual(recovered.request("GET", f"/api/attachments/{attachment['id']}")[1], content)
                finally:
                    replacement.shutdown()
                    replacement.server_close()
                    recovered_thread.join()
                with self.assertRaises(ValueError):
                    restore(snapshot, recovered_db, recovered_blobs)
                blob = next((snapshot/"attachments").iterdir())
                blob.write_bytes(b"tampered")
                with self.assertRaises(ValueError):
                    verify(snapshot)
            finally:
                server.shutdown()
                server.server_close()
                thread.join()


if __name__ == "__main__":
    unittest.main()
