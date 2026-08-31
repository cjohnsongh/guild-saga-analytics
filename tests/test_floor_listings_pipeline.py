import importlib.util
import json
import pathlib
import tempfile
import unittest
from unittest import mock

ROOT = pathlib.Path(__file__).resolve().parents[1]
SPEC = importlib.util.spec_from_file_location(
    "floor_listings_pipeline", ROOT / "scripts" / "floor_listings_pipeline.py"
)
mod = importlib.util.module_from_spec(SPEC)
assert SPEC.loader is not None
SPEC.loader.exec_module(mod)


class FloorListingsPipelineTests(unittest.TestCase):
    def seed(self, root: pathlib.Path) -> None:
        (root / "data/history").mkdir(parents=True)
        (root / "data/state").mkdir(parents=True)
        (root / "data/history/floor_listings.csv").write_text(
            "snapshot_date,floor_sol,listed_count,source\n"
            "2026-08-25,0.08,440,magic_eden_snapshot\n"
            "2026-08-26,0.07,433,magic_eden_snapshot\n",
            encoding="utf-8",
        )
        (root / "data/state/checkpoints.json").write_text(
            json.dumps({
                "cutover_date": "2026-08-26",
                "hero_state_checkpoint": "2026-08-30T00:00:00Z",
                "market_checkpoint_date": "2026-08-30",
                "floor_checkpoint_date": "2026-08-26",
            }, indent=2) + "\n",
            encoding="utf-8",
        )

    def test_parse_snapshot_preserves_historical_units_and_listing_semantics(self):
        floor, listed = mod.parse_snapshot(
            {"floorPrice": 78_840_000},
            {"listedCount": 435},
        )
        self.assertAlmostEqual(floor, 0.07884)
        self.assertEqual(listed, 435)

    def test_parse_snapshot_fails_closed_on_missing_source_fields(self):
        with self.assertRaises(RuntimeError):
            mod.parse_snapshot({}, {"listedCount": 435})
        with self.assertRaises(RuntimeError):
            mod.parse_snapshot({"floorPrice": 70_000_000}, {})

    def test_prepare_appends_today_and_advances_only_floor_checkpoint(self):
        with tempfile.TemporaryDirectory() as raw:
            root = pathlib.Path(raw)
            self.seed(root)
            before = mod.load_checkpoints(root)
            changed = mod.prepare_snapshot(root, "2026-08-30", 0.065, 421)
            self.assertTrue(changed)
            after = mod.load_checkpoints(root)
            self.assertEqual(after["floor_checkpoint_date"], "2026-08-30")
            self.assertEqual(after["hero_state_checkpoint"], before["hero_state_checkpoint"])
            self.assertEqual(after["market_checkpoint_date"], before["market_checkpoint_date"])
            rows = mod.read_history(root)
            self.assertEqual(rows[-1], {
                "snapshot_date": "2026-08-30",
                "floor_sol": "0.065",
                "listed_count": "421",
                "source": "magic_eden_snapshot",
            })

    def test_prepare_never_fabricates_missed_days(self):
        with tempfile.TemporaryDirectory() as raw:
            root = pathlib.Path(raw)
            self.seed(root)
            mod.prepare_snapshot(root, "2026-08-30", 0.065, 421)
            dates = [row["snapshot_date"] for row in mod.read_history(root)]
            self.assertNotIn("2026-08-27", dates)
            self.assertNotIn("2026-08-28", dates)
            self.assertNotIn("2026-08-29", dates)
            self.assertEqual(dates[-1], "2026-08-30")

    def test_same_utc_day_is_idempotent_noop(self):
        with tempfile.TemporaryDirectory() as raw:
            root = pathlib.Path(raw)
            self.seed(root)
            mod.prepare_snapshot(root, "2026-08-30", 0.065, 421)
            history_before = (root / "data/history/floor_listings.csv").read_bytes()
            checkpoints_before = (root / "data/state/checkpoints.json").read_bytes()
            changed = mod.prepare_snapshot(root, "2026-08-30", 0.06, 400)
            self.assertFalse(changed)
            self.assertEqual((root / "data/history/floor_listings.csv").read_bytes(), history_before)
            self.assertEqual((root / "data/state/checkpoints.json").read_bytes(), checkpoints_before)

    def test_same_day_production_recovery_can_skip_provider_fetch(self):
        with tempfile.TemporaryDirectory() as raw:
            root = pathlib.Path(raw)
            self.seed(root)
            mod.prepare_snapshot(root, "2026-08-30", 0.065, 421)
            with (
                mock.patch.object(mod, "changed_paths", return_value=[]),
                mock.patch.object(mod, "git") as fake_git,
                mock.patch.object(mod, "run_python") as fake_run_python,
                mock.patch.object(mod, "wait_for_deployment", return_value="https://release.pages.dev"),
            ):
                fake_git.side_effect = lambda *args, **kwargs: mock.Mock(
                    stdout=("main\n" if args[:2] == ("branch", "--show-current") else "a" * 40 + "\n")
                )
                self.assertTrue(mod.prove_current_date_if_present("2026-08-30", root))
                fake_run_python.assert_called_once_with("scripts/validate_live.py", cwd=root)

    def test_fetch_retries_transient_network_failure(self):
        responses = [
            mod.urllib.error.URLError("dns"),
            (200, {"floorPrice": 70_000_000}),
        ]

        def fake_request(_url):
            item = responses.pop(0)
            if isinstance(item, Exception):
                raise item
            return item

        with (
            mock.patch.object(mod, "request_json", side_effect=fake_request),
            mock.patch.object(mod.time, "sleep"),
        ):
            result = mod.fetch_magic_eden_stats(False)
        self.assertEqual(result["floorPrice"], 70_000_000)

    def test_workflow_routes_only_exact_cloudflare_event_to_production(self):
        text = (ROOT / ".github/workflows/floor-listings.yml").read_text(encoding="utf-8")
        self.assertIn("types: [floor_listings_daily]", text)
        self.assertIn("github.event.action == 'floor_listings_daily'", text)
        self.assertIn("--mode dry-run", text)
        self.assertIn("--mode production", text)
        self.assertIn("group: guild-saga-production-pipeline", text)
        self.assertNotIn("schedule:", text)

    def test_worker_config_has_floor_retry_cron(self):
        config = json.loads((ROOT / "cloudflare/webhook-inbox/wrangler.jsonc").read_text(encoding="utf-8"))
        self.assertIn("0,30 * * * *", config["triggers"]["crons"])
        self.assertIn("30,50 23 * * *", config["triggers"]["crons"])


if __name__ == "__main__":
    unittest.main()
