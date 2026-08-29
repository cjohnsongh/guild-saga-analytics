import importlib.util
import pathlib
import unittest

ROOT = pathlib.Path(__file__).resolve().parents[1]
PATH = ROOT / "scripts" / "refresh_webhook_watch_frontier.py"
spec = importlib.util.spec_from_file_location("frontier_refresh", PATH)
mod = importlib.util.module_from_spec(spec)
spec.loader.exec_module(mod)

class FrontierOverlayTests(unittest.TestCase):
    def test_pending_transfer_adds_destination_and_preserves_old_account(self):
        mint = "M" * 32
        old = "A" * 32
        new = "B" * 32
        sig = "S" * 88
        rows = [{
            "mint": mint, "token_account": old,
            "first_observed_utc": "2026-08-29T01:58:29.082Z",
            "last_observed_utc": "2026-08-29T01:58:29.082Z",
            "is_current": "1", "source": "PHASE1L_ACTIVATION_FRONTIER",
        }]
        tx = {
            "slot": 123,
            "blockTime": 1787970000,
            "transaction": {
                "signatures": [sig],
                "message": {
                    "accountKeys": [old, new],
                    "header": {"numRequiredSignatures": 0},
                    "instructions": [],
                },
            },
            "meta": {
                "err": None,
                "preTokenBalances": [{
                    "accountIndex": 0, "mint": mint, "owner": "OWNER1",
                    "uiTokenAmount": {"amount": "1"},
                }],
                "postTokenBalances": [{
                    "accountIndex": 1, "mint": mint, "owner": "OWNER2",
                    "uiTokenAmount": {"amount": "1"},
                }],
            },
        }
        events = [{
            "signature": sig,
            "slot": 123,
            "payload_json": __import__("json").dumps(tx),
        }]
        final_rows, introduced = mod.overlay_pending_frontier(rows, events, {mint}, {mint})
        by_acct = {r["token_account"]: r for r in final_rows}
        self.assertEqual(set(by_acct), {old, new})
        self.assertEqual(by_acct[old]["is_current"], "0")
        self.assertEqual(by_acct[new]["is_current"], "1")
        self.assertIn(new, introduced)

if __name__ == "__main__":
    unittest.main()
