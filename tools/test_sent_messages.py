from __future__ import annotations

import os
import sys
import tempfile
import unittest
from pathlib import Path
from unittest import mock

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

import feishu_client  # noqa: E402
import sent_messages  # noqa: E402


class FakeResponse:
    def __init__(self, payload, status=200):
        self._payload = payload
        self.status_code = status
        self.text = ""

    def json(self):
        return self._payload

    def raise_for_status(self):
        if self.status_code >= 400:
            raise RuntimeError(f"HTTP {self.status_code}")


class SentMessagesTests(unittest.TestCase):
    def setUp(self) -> None:
        self.tmp = tempfile.TemporaryDirectory()
        self.path = os.path.join(self.tmp.name, sent_messages.FILE_NAME)
        self.counter = 0

    def tearDown(self) -> None:
        self.tmp.cleanup()

    def fake_post(self, url, headers=None, json=None, timeout=None):
        self.counter += 1
        return FakeResponse({"code": 0, "data": {"message_id": f"om_{self.counter}"}})

    def test_round_records_every_message_sent_inside_it(self) -> None:
        with mock.patch.object(feishu_client.requests, "post", self.fake_post):
            feishu_client.send_text("t", "oc_x", "before")  # นอกรอบ ไม่บันทึก
            with sent_messages.recording(self.path, "hourly", "รายงาน 16:01"):
                feishu_client.send_card("t", "oc_x", "{}")
                feishu_client.send_file("t", "oc_x", "file_key")
        rounds = sent_messages.load_rounds(self.path)
        self.assertEqual(len(rounds), 1)
        self.assertEqual([m["message_id"] for m in rounds[0]["messages"]], ["om_2", "om_3"])
        self.assertEqual([m["msg_type"] for m in rounds[0]["messages"]], ["interactive", "file"])
        self.assertEqual(sent_messages.round_status(rounds[0]), "2 ข้อความ")

    def test_partial_round_is_kept_when_sending_fails(self) -> None:
        with mock.patch.object(feishu_client.requests, "post", self.fake_post):
            with self.assertRaises(RuntimeError):
                with sent_messages.recording(self.path, "hourly", "รายงาน 17:01"):
                    feishu_client.send_card("t", "oc_x", "{}")
                    raise RuntimeError("upload failed")
        self.assertEqual(len(sent_messages.load_rounds(self.path)[0]["messages"]), 1)

    def test_recall_marks_success_and_reports_failures(self) -> None:
        sent_messages.record_round(self.path, "hourly", "รายงาน 18:01", [
            {"message_id": "om_ok", "chat_id": "oc_x", "msg_type": "interactive"},
            {"message_id": "om_old", "chat_id": "oc_x", "msg_type": "file"},
        ])
        round_id = sent_messages.load_rounds(self.path)[0]["id"]
        deleted = []

        def fake_delete(url, headers=None, timeout=None):
            deleted.append(url.rsplit("/", 1)[-1])
            if url.endswith("om_old"):
                return FakeResponse({"code": 230110, "msg": "message recall time exceeded"}, status=400)
            return FakeResponse({"code": 0})

        with mock.patch.object(feishu_client.requests, "delete", fake_delete):
            done, errors = sent_messages.recall_round(self.path, round_id, "t")
            self.assertEqual((done, len(errors)), (1, 1))
            self.assertIn("230110", errors[0])
            # กดถอนซ้ำ: ข้ามข้อความที่ถอนแล้ว ลองเฉพาะที่ยังไม่สำเร็จ
            sent_messages.recall_round(self.path, round_id, "t")
        self.assertEqual(deleted.count("om_ok"), 1)
        self.assertEqual(sent_messages.round_status(sent_messages.load_rounds(self.path)[0]), "ถอนแล้ว 1/2")

    def test_journal_keeps_only_recent_rounds(self) -> None:
        with mock.patch.object(sent_messages, "MAX_ROUNDS", 3):
            for i in range(5):
                sent_messages.record_round(self.path, "hourly", f"r{i}", [{"message_id": f"om_{i}"}])
        self.assertEqual([r["label"] for r in sent_messages.load_rounds(self.path)], ["r4", "r3", "r2"])


if __name__ == "__main__":
    unittest.main()
