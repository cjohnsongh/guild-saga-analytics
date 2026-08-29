import importlib.util
import pathlib
import unittest
from datetime import datetime, timezone
from unittest import mock

ROOT = pathlib.Path(__file__).resolve().parents[1]
PATH = ROOT / "scripts" / "prepare_webhook_batch.py"
spec = importlib.util.spec_from_file_location("prepare_webhook_batch", PATH)
mod = importlib.util.module_from_spec(spec)
spec.loader.exec_module(mod)

class LiveBatchHelperTests(unittest.TestCase):
    def test_forced_snapshot_is_sent_on_first_page(self):
        seen = []

        def fake_http(url, _token):
            seen.append(url)
            return {
                "ok": True,
                "snapshot_received_at": "2026-08-29T12:00:00.000Z",
                "events": [],
                "next_cursor": None,
            }

        with (
            mock.patch.dict(
                mod.os.environ,
                {"GUILD_SAGA_SNAPSHOT_RECEIVED_AT": "2026-08-29T12:00:00.000Z"},
            ),
            mock.patch.object(mod, "http_json", side_effect=fake_http),
        ):
            snapshot, events = mod.fetch_pending_snapshot("not-logged")
        self.assertEqual(snapshot, "2026-08-29T12:00:00.000Z")
        self.assertEqual(events, [])
        self.assertIn("snapshot_received_at=2026-08-29T12%3A00%3A00.000Z", seen[0])

    def test_first_post_activation_movement_applies(self):
        activation = datetime(2026, 8, 29, 1, 58, 29, tzinfo=timezone.utc)
        event = datetime(2026, 8, 29, 2, 0, 0, tzinfo=timezone.utc)
        self.assertEqual(
            mod.movement_is_new(
                mint="m", slot=100, signature="s1", block_time=event,
                cursor=None, activation_boundary=activation
            ),
            "APPLY",
        )

    def test_pre_activation_late_delivery_is_covered(self):
        activation = datetime(2026, 8, 29, 1, 58, 29, tzinfo=timezone.utc)
        event = datetime(2026, 8, 29, 1, 0, 0, tzinfo=timezone.utc)
        self.assertEqual(
            mod.movement_is_new(
                mint="m", slot=100, signature="s1", block_time=event,
                cursor=None, activation_boundary=activation
            ),
            "COVERED_PRE_ACTIVATION",
        )

    def test_stale_slot_cannot_regress_state(self):
        activation = datetime(2026, 8, 29, 1, 58, 29, tzinfo=timezone.utc)
        event = datetime(2026, 8, 29, 3, 0, 0, tzinfo=timezone.utc)
        cursor = {
            "mint": "m", "last_slot": "200", "last_signature": "new",
            "last_block_time_utc": "2026-08-29T04:00:00.000Z",
        }
        self.assertEqual(
            mod.movement_is_new(
                mint="m", slot=199, signature="old", block_time=event,
                cursor=cursor, activation_boundary=activation
            ),
            "STALE",
        )

    def test_same_slot_conflict_refuses_guess(self):
        activation = datetime(2026, 8, 29, 1, 58, 29, tzinfo=timezone.utc)
        event = datetime(2026, 8, 29, 3, 0, 0, tzinfo=timezone.utc)
        cursor = {
            "mint": "m", "last_slot": "200", "last_signature": "one",
            "last_block_time_utc": "2026-08-29T03:00:00.000Z",
        }
        with self.assertRaises(RuntimeError):
            mod.movement_is_new(
                mint="m", slot=200, signature="two", block_time=event,
                cursor=cursor, activation_boundary=activation
            )

if __name__ == "__main__":
    unittest.main()
