import importlib.util
import json
import pathlib
import sys
import tempfile
import unittest
from unittest import mock

ROOT = pathlib.Path(__file__).resolve().parents[1]
PATH = ROOT / "scripts" / "production_pipeline.py"
SPEC = importlib.util.spec_from_file_location("production_pipeline", PATH)
mod = importlib.util.module_from_spec(SPEC)
sys.modules[SPEC.name] = mod
SPEC.loader.exec_module(mod)


class FakeWorker:
    def __init__(self, pending=()):
        self.pending = tuple(pending)
        self.acks = []

    def ack(self, signatures):
        self.acks.append(list(signatures))
        self.pending = tuple(x for x in self.pending if x not in signatures)
        return {"ok": True, "requested": len(signatures), "processed": len(signatures)}

    def snapshot(self):
        return mod.Snapshot("2026-08-29T12:00:00.000Z", tuple(
            {"signature": sig} for sig in self.pending
        ))


class ProductionPipelineTests(unittest.TestCase):
    def test_workflow_routes_only_schedule_to_production(self):
        text = (ROOT / ".github" / "workflows" / "production-pipeline.yml").read_text(
            encoding="utf-8"
        )
        self.assertIn("workflow_dispatch:", text)
        self.assertNotIn("inputs:", text)
        self.assertIn("schedule:", text)
        self.assertIn('cron: "7,37 * * * *"', text)
        self.assertIn("if: github.event_name == 'schedule'", text)
        self.assertIn("if: github.event_name == 'workflow_dispatch'", text)
        self.assertEqual(text.count("--mode production"), 1)
        self.assertEqual(text.count("--mode dry-run"), 1)
        self.assertIn("manual-dry-run:", text)
        self.assertIn("scheduled-production:", text)
        self.assertIn("contents: write", text)
        self.assertIn("contents: read", text)
        self.assertIn("cancel-in-progress: false", text)
        self.assertEqual(text.count("fetch-depth: 0"), 2)
        self.assertEqual(text.count("ref: main"), 2)
        manual = text.split("manual-dry-run:", 1)[1].split("scheduled-production:", 1)[0]
        scheduled = text.split("scheduled-production:", 1)[1]
        self.assertIn("contents: read", manual)
        self.assertIn("--mode dry-run", manual)
        self.assertNotIn("--mode production", manual)
        self.assertNotIn("HELIUS_WEBHOOK_AUTH", manual)
        self.assertIn("contents: write", scheduled)
        self.assertIn("--mode production", scheduled)
        self.assertIn("HELIUS_WEBHOOK_AUTH", scheduled)
        for secret in (
            "HELIUS_API_KEY",
            "ALCHEMY_API_KEY",
            "PIPELINE_TOKEN",
            "HELIUS_WEBHOOK_AUTH",
        ):
            self.assertIn(f"secrets.{secret}", text)

    def test_tracked_webhook_config_is_raw_and_immutable_boundary(self):
        config = json.loads(
            (ROOT / "data" / "state" / "webhook_production.json").read_text(encoding="utf-8")
        )
        self.assertTrue(config["active"])
        self.assertEqual(config["webhook_type"], "raw")
        self.assertEqual(config["activation_boundary_utc"], "2026-08-29T01:58:29.082Z")

    def test_no_manifest_is_clean_recovery_noop(self):
        worker = FakeWorker()
        with mock.patch.object(mod, "MANIFEST", pathlib.Path("definitely-missing-manifest")):
            mod.recover_committed_batch(worker)
        self.assertEqual(worker.acks, [])

    def test_manifest_is_deterministic_and_exact(self):
        with tempfile.TemporaryDirectory() as raw:
            root = pathlib.Path(raw)
            for rel in mod.VERIFY_FILES:
                path = root / rel
                path.parent.mkdir(parents=True, exist_ok=True)
                path.write_text('{"ok":true}\n', encoding="utf-8")
            prepared = {
                "snapshot_received_at": "2026-08-29T12:00:00.000Z",
                "activation_boundary_utc": "2026-08-29T01:58:29.082Z",
                "signatures": ["s1", "s2"],
                "canonical_hashes": {"data/state/checkpoints.json": "a" * 64},
                "counts": {"TRANSFER": 1},
            }
            one = mod.build_manifest(prepared, root)
            two = mod.build_manifest(prepared, root)
            self.assertEqual(one, two)
            self.assertEqual(mod.validate_manifest(one), ["s1", "s2"])

    def test_duplicate_delivery_is_rejected_from_manifest(self):
        manifest = {
            "schema_version": 1,
            "signatures": ["same", "same"],
            "signature_count": 2,
        }
        with self.assertRaises(RuntimeError):
            mod.validate_manifest(manifest)

    def test_new_arrival_is_not_part_of_selected_snapshot(self):
        selected = mod.Snapshot("t1", ({"signature": "old"},))
        later = mod.Snapshot("t2", ({"signature": "old"}, {"signature": "new"}))
        self.assertEqual(selected.signatures, ("old",))
        self.assertNotIn("new", selected.signatures)
        self.assertIn("new", later.signatures)

    def test_ack_missing_signature_fails_closed(self):
        response = {
            "ok": True, "requested": 2, "processed": 1,
            "missing": ["s2"], "not_processed": [],
        }
        with self.assertRaises(RuntimeError):
            mod.validate_ack_response(response, ["s1", "s2"])

    def test_ack_not_processed_fails_closed(self):
        response = {
            "ok": True, "requested": 1, "processed": 0,
            "missing": [], "not_processed": ["s1"],
        }
        with self.assertRaises(RuntimeError):
            mod.validate_ack_response(response, ["s1"])

    def _recover(self, worker, verifier):
        manifest = {
            "schema_version": 1,
            "signatures": ["s1", "s2"],
            "signature_count": 2,
            "public_json_hashes": {},
        }
        with tempfile.TemporaryDirectory() as raw:
            path = pathlib.Path(raw) / "manifest.json"
            path.write_text(json.dumps(manifest), encoding="utf-8")
            completed = mock.Mock(returncode=0)
            with (
                mock.patch.object(mod, "MANIFEST", path),
                mock.patch.object(mod, "manifest_release_commit", return_value="a" * 40),
                mock.patch.object(mod, "git", return_value=completed),
                mock.patch.object(mod, "wait_for_deployment", side_effect=verifier),
            ):
                mod.recover_committed_batch(worker)

    def test_crash_after_push_resumes_deploy_then_ack(self):
        worker = FakeWorker(("s1", "s2", "newer"))
        self._recover(worker, lambda *_args, **_kwargs: "https://release.pages.dev")
        self.assertEqual(worker.acks, [["s1", "s2"]])
        self.assertEqual(worker.pending, ("newer",))

    def test_deployment_failure_never_acks(self):
        worker = FakeWorker(("s1", "s2"))
        with self.assertRaises(RuntimeError):
            self._recover(worker, RuntimeError("deployment timeout"))
        self.assertEqual(worker.acks, [])

    def test_crash_after_deploy_before_ack_retries_exact_batch(self):
        worker = FakeWorker(("s1", "s2", "newer"))
        self._recover(worker, lambda *_args, **_kwargs: "https://release.pages.dev")
        self.assertEqual(worker.acks[0], ["s1", "s2"])
        self.assertEqual(worker.pending, ("newer",))

    def test_already_processed_retry_is_idempotently_reproven(self):
        worker = FakeWorker(())
        self._recover(worker, lambda *_args, **_kwargs: "https://release.pages.dev")
        self.assertEqual(worker.acks, [["s1", "s2"]])

    def test_push_race_fails_closed(self):
        with self.assertRaises(RuntimeError):
            mod.assert_push_base("parent", "new-origin")

    def test_matching_push_base_passes(self):
        mod.assert_push_base("same", "same")

    def test_deployment_candidates_survive_transient_github_dns_failure(self):
        with mock.patch.object(
            mod, "request_json", side_effect=mod.urllib.error.URLError("temporary dns failure")
        ):
            candidates = mod.deployment_candidates("a" * 40, "token")
        self.assertIn("https://guildsaga.pages.dev", candidates)
        self.assertIn("https://guild-saga-analytics.pages.dev", candidates)

    def test_candidate_matches_treats_transient_dns_failure_as_not_ready(self):
        with mock.patch.object(
            mod, "request_json", side_effect=mod.urllib.error.URLError("temporary dns failure")
        ):
            matched = mod.candidate_matches(
                "https://release.pages.dev", {"data/example.json": {"ok": True}}, "a" * 40
            )
        self.assertFalse(matched)

    def test_wait_for_deployment_retries_transient_discovery_failure(self):
        source = mock.Mock(
            side_effect=[
                mod.urllib.error.URLError("temporary dns failure"),
                {"https://release.pages.dev"},
            ]
        )
        with (
            mock.patch.object(mod, "expected_release_json", return_value={"data/example.json": {"ok": True}}),
            mock.patch.object(mod, "candidate_matches", return_value=True) as matches,
            mock.patch.object(mod.time, "sleep"),
        ):
            origin = mod.wait_for_deployment(
                "a" * 40, {}, timeout_seconds=10, poll_seconds=1, candidate_source=source
            )
        self.assertIn(origin, {"https://guildsaga.pages.dev", "https://guild-saga-analytics.pages.dev"})
        self.assertEqual(source.call_count, 1)
        matches.assert_called_once()

    def test_missing_secret_fails_before_provider_or_ack(self):
        with mock.patch.object(mod, "assert_clean_production_tree"):
            with self.assertRaises(RuntimeError) as raised:
                mod.production_run({})
        self.assertIn("PIPELINE_TOKEN", str(raised.exception))


if __name__ == "__main__":
    unittest.main()
