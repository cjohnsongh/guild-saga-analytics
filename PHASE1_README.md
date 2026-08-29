# Phase 1 — Independent collector foundation

This patch is designed to be copied into the root of the current `guild-saga-analytics` repository. It does not modify the React UI or current production JSON.

## What Phase 1 establishes

- the Aug. 26, 2026 website output is frozen as an immutable cutover fixture;
- final Dune SQL is archived as migration specification only, never as a production dependency;
- World Mode Stake / Unstake / Quest Restart rules are encoded as deterministic Python contracts;
- Magic Eden V2 / Tensor Marketplace / Tensor AMM sale rules are encoded from the final production SQL;
- raw Solana `getTransaction` normalization exists for real-transaction parity tests;
- live validation is separated from frozen cutover validation;
- API keys are explicitly excluded from Git.

## Local no-network checks

```bash
python -m unittest tests.test_collector_contracts -v
python scripts/validate_cutover.py
python scripts/validate.py
```

## First network-backed migration gate

```bash
python scripts/capture_market_backtest.py
```

That backtest covers all 17 existing post-baseline live sales plus four historical Tensor cases. It stores only public raw transaction fixtures; secrets are never written to fixtures.

Do not enable scheduled collection, webhook ingestion, or automatic production commits until this backtest passes.
