# Baseline collector foundation

This document records the foundational contracts established for the independent Guild Saga collector before automated production was enabled.

## What the foundation establishes

- the Aug. 26, 2026 website output is frozen as an immutable baseline fixture;
- canonical baseline datasets are byte-verified against the preserved source archive;
- World Mode Stake, Unstake, and Quest Restart rules are encoded as deterministic Python contracts;
- Magic Eden V2, Tensor Marketplace, and Tensor AMM sale rules are encoded as deterministic parser contracts;
- raw Solana `getTransaction` normalization exists for real-transaction parity tests;
- live validation is separated from frozen baseline validation;
- API keys and production secrets are excluded from Git.

## Local no-network checks

```bash
python -m unittest tests.test_collector_contracts -v
python scripts/validate_cutover.py
python scripts/validate.py
```

## Network-backed parser gate

```bash
python scripts/capture_market_backtest.py
```

The captured public transaction fixtures make later parser regressions deterministic instead of depending on provider availability during every test run.

Current production scheduling, D1 ingestion, deployment proof, and recovery behavior are documented in `docs/architecture.md`.
