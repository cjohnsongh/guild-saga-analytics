from __future__ import annotations

import unittest
from datetime import datetime, timezone

from collector.constants import (
    MAGIC_EDEN_V2_PROGRAM,
    ME_EXECUTE_SALE_V2,
    ROYALTY_90_ADDRESS,
    SYSTEM_PROGRAM,
    TENSOR_AMM_BUY_NFT,
    TENSOR_AMM_BUY_SELL_EVENT,
    TENSOR_AMM_PROGRAM,
    TENSOR_MARKETPLACE_BUY_LEGACY,
    TENSOR_MARKETPLACE_PROGRAM,
    TENSOR_MARKETPLACE_TAKE_EVENT,
    WORLD_MODE_PROGRAM,
    WORLD_QUEST_RESTART_DISCRIMINATOR,
    WORLD_STAKE_DISCRIMINATOR,
    WORLD_UNSTAKE_DISCRIMINATOR,
)
from collector.hero_state import (
    HeroState,
    RawMovement,
    apply_movement,
    apply_quest_restart,
    classify_world_call,
    quest_bucket,
)
from collector.market import InstructionCall, decode_sales
from collector.solana_normalize import (
    b58decode, normalize_token_movements, normalize_transaction,
    transaction_has_burn_instruction, transaction_signers,
)

NOW = datetime(2026, 8, 28, 12, 0, tzinfo=timezone.utc)


def data_with_u64(discriminator: bytes, one_based_start: int, value: int, size: int = 100) -> bytes:
    b = bytearray(max(size, one_based_start - 1 + 8))
    b[: len(discriminator)] = discriminator
    start = one_based_start - 1
    b[start:start + 8] = value.to_bytes(8, "little")
    return bytes(b)


class HeroStateContracts(unittest.TestCase):
    def test_world_call_discriminators_and_staking_wallet_position(self):
        args = ["a1", "a2", "a3", "staking", "a5"]
        stake = classify_world_call(signature="s", event_time=NOW, executing_account=WORLD_MODE_PROGRAM,
                                    data=WORLD_STAKE_DISCRIMINATOR, signer="user", account_arguments=args)
        self.assertEqual((stake.action, stake.staking_wallet), ("STAKE", "staking"))
        unstake = classify_world_call(signature="u", event_time=NOW, executing_account=WORLD_MODE_PROGRAM,
                                      data=WORLD_UNSTAKE_DISCRIMINATOR, signer="user", account_arguments=args)
        self.assertEqual((unstake.action, unstake.staking_wallet), ("UNSTAKE", "staking"))
        quest = classify_world_call(signature="q", event_time=NOW, executing_account=WORLD_MODE_PROGRAM,
                                    data=WORLD_QUEST_RESTART_DISCRIMINATOR, signer="user", account_arguments=[])
        self.assertEqual((quest.action, quest.staking_wallet), ("QUEST_RESTART", None))

    def test_stake_quest_unstake_state_machine(self):
        state = HeroState(mint="mint", current_raw_owner="user", current_beneficial_owner="user")
        stake_time = datetime(2026, 8, 27, 10, tzinfo=timezone.utc)
        stake = classify_world_call(signature="stake", event_time=stake_time, executing_account=WORLD_MODE_PROGRAM,
                                    data=WORLD_STAKE_DISCRIMINATOR, signer="user",
                                    account_arguments=["1", "2", "3", "staking"])
        state = apply_movement(state, RawMovement("stake", "mint", stake_time, "transfer", "user", "staking"), stake)
        self.assertEqual(state.current_world_staked, 1)
        self.assertEqual(state.current_beneficial_owner, "user")
        self.assertEqual(state.current_raw_owner, "staking")
        self.assertIsNone(state.best_known_last_qualifying_quest_utc)

        quest_time = datetime(2026, 8, 28, 9, tzinfo=timezone.utc)
        quest = classify_world_call(signature="quest", event_time=quest_time, executing_account=WORLD_MODE_PROGRAM,
                                    data=WORLD_QUEST_RESTART_DISCRIMINATOR, signer="user", account_arguments=[])
        state = apply_quest_restart(state, quest)
        self.assertTrue(state.best_known_last_qualifying_quest_utc.startswith("2026-08-28T09:00:00"))
        self.assertEqual(quest_bucket(state, NOW), "Active 0–7d")

        unstake_time = datetime(2026, 8, 28, 11, tzinfo=timezone.utc)
        unstake = classify_world_call(signature="unstake", event_time=unstake_time, executing_account=WORLD_MODE_PROGRAM,
                                      data=WORLD_UNSTAKE_DISCRIMINATOR, signer="user",
                                      account_arguments=["1", "2", "3", "staking"])
        state = apply_movement(state, RawMovement("unstake", "mint", unstake_time, "transfer", "staking", "user"), unstake)
        self.assertEqual(state.current_world_staked, 0)
        self.assertEqual(state.current_beneficial_owner, "user")
        self.assertIsNone(state.quest_user_wallet)

    def test_burn_clears_owner_and_staking(self):
        state = HeroState(mint="m", current_raw_owner="staking", current_world_staked=1,
                          current_beneficial_owner="user", current_world_staking_wallet="staking",
                          quest_user_wallet="user", quest_staking_wallet="staking")
        state = apply_movement(state, RawMovement("burn", "m", NOW, "burn", "staking", None))
        self.assertEqual(state.burned, 1)
        self.assertIsNone(state.current_raw_owner)
        self.assertIsNone(state.current_beneficial_owner)
        self.assertEqual(state.current_world_staked, 0)


class MarketContracts(unittest.TestCase):
    def call(self, program, disc, args, data=None, outer=1):
        return InstructionCall("sig", NOW, program, outer, args, data if data is not None else disc)

    def test_magic_eden_execute_sale_v2(self):
        args = ["buyer", "seller", "x3", "x4", "mint"]
        calls = [self.call(MAGIC_EDEN_V2_PROGRAM, ME_EXECUTE_SALE_V2, args,
                           data_with_u64(ME_EXECUTE_SALE_V2, 11, 70_000_000))]
        sales = decode_sales(calls, {"mint"})
        self.assertEqual(len(sales), 1)
        self.assertEqual((sales[0].buyer, sales[0].seller), ("buyer", "seller"))
        self.assertAlmostEqual(sales[0].gross_price_sol, 0.07)

    def test_tensor_marketplace_pair(self):
        args = ["x1", "buyer", "x3", "x4", "x5", "mint", "seller"]
        buy = self.call(TENSOR_MARKETPLACE_PROGRAM, TENSOR_MARKETPLACE_BUY_LEGACY, args)
        event = self.call(TENSOR_MARKETPLACE_PROGRAM, TENSOR_MARKETPLACE_TAKE_EVENT, [],
                          data_with_u64(TENSOR_MARKETPLACE_TAKE_EVENT, 78, 123_000_000))
        sales = decode_sales([buy, event], {"mint"})
        self.assertEqual(len(sales), 1)
        self.assertEqual(sales[0].marketplace_detail, "Tensor Marketplace")
        self.assertAlmostEqual(sales[0].gross_price_sol, 0.123)

    def test_tensor_amm_pair_and_transaction_royalty(self):
        args = ["x1", "buyer", "x3", "x4", "x5", "x6", "x7", "seller",
                "x9", "x10", "x11", "x12", "x13", "x14", "mint"]
        buy = self.call(TENSOR_AMM_PROGRAM, TENSOR_AMM_BUY_NFT, args)
        event = self.call(TENSOR_AMM_PROGRAM, TENSOR_AMM_BUY_SELL_EVENT, [],
                          data_with_u64(TENSOR_AMM_BUY_SELL_EVENT, 10, 50_000_000))
        system_data = bytearray(12)
        system_data[0:4] = (2).to_bytes(4, "little")
        system_data[4:12] = (2_700_000).to_bytes(8, "little")
        royalty = InstructionCall("sig", NOW, SYSTEM_PROGRAM, 99, ["payer", ROYALTY_90_ADDRESS], bytes(system_data))
        sales = decode_sales([buy, event, royalty], {"mint"})
        self.assertEqual(len(sales), 1)
        self.assertEqual(sales[0].marketplace_detail, "Tensor AMM")
        self.assertAlmostEqual(sales[0].gross_price_sol, 0.05)
        self.assertAlmostEqual(sales[0].royalty_90_sol, 0.0027)


class SolanaNormalizerContracts(unittest.TestCase):
    def test_base58_leading_zeroes(self):
        self.assertEqual(b58decode("1112"), b"\x00\x00\x00\x01")

    def test_compiled_outer_instruction_normalizes_accounts(self):
        # A tiny compiled transaction with program index 2 and account indices 0/1.
        # Base58 "2" decodes to one byte 0x01.
        tx = {
            "blockTime": 1787920000,
            "transaction": {
                "signatures": ["sig"],
                "message": {
                    "accountKeys": ["buyer", "seller", "program"],
                    "instructions": [{"programIdIndex": 2, "accounts": [0, 1], "data": "2"}],
                },
            },
            "meta": {"err": None, "innerInstructions": []},
        }
        calls = normalize_transaction(tx)
        self.assertEqual(len(calls), 1)
        self.assertEqual(calls[0].executing_account, "program")
        self.assertEqual(list(calls[0].account_arguments), ["buyer", "seller"])
        self.assertEqual(calls[0].data, b"\x01")

    def test_token_movement_and_signers_normalize(self):
        tx = {
            "blockTime": 1787920000,
            "transaction": {
                "signatures": ["sig"],
                "message": {
                    "header": {"numRequiredSignatures": 1},
                    "accountKeys": ["user", "from_token", "to_token", "program"],
                    "instructions": [],
                },
            },
            "meta": {
                "err": None,
                "innerInstructions": [],
                "preTokenBalances": [
                    {"accountIndex": 1, "mint": "mint", "owner": "user",
                     "uiTokenAmount": {"amount": "1"}},
                    {"accountIndex": 2, "mint": "mint", "owner": "other",
                     "uiTokenAmount": {"amount": "0"}},
                ],
                "postTokenBalances": [
                    {"accountIndex": 1, "mint": "mint", "owner": "user",
                     "uiTokenAmount": {"amount": "0"}},
                    {"accountIndex": 2, "mint": "mint", "owner": "other",
                     "uiTokenAmount": {"amount": "1"}},
                ],
            },
        }
        self.assertEqual(transaction_signers(tx), ["user"])
        moves = normalize_token_movements(tx, {"mint"})
        self.assertEqual(len(moves), 1)
        self.assertEqual(moves[0].classification, "TRANSFER")
        self.assertEqual((moves[0].from_owner, moves[0].to_owner), ("user", "other"))

    def test_explicit_spl_burn_detection(self):
        # TokenInstruction::Burn = 8; accounts are source, mint, authority.
        # Base58 "9" is the one-byte value 8.
        tx = {
            "blockTime": 1787920000,
            "transaction": {
                "signatures": ["sig"],
                "message": {
                    "header": {"numRequiredSignatures": 1},
                    "accountKeys": [
                        "user", "source", "mint",
                        "TokenkegQfeZyiNwAJbNbGKPFXCWuBvf9Ss623VQ5DA",
                    ],
                    "instructions": [
                        {"programIdIndex": 3, "accounts": [1, 2, 0], "data": "9"}
                    ],
                },
            },
            "meta": {"err": None, "innerInstructions": []},
        }
        self.assertTrue(transaction_has_burn_instruction(tx, "mint"))
        self.assertFalse(transaction_has_burn_instruction(tx, "other"))


if __name__ == "__main__":
    unittest.main()
