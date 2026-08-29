# Guild Saga Analytics

Independent on-chain analytics for Guild Saga Heroes, a 10,000-piece Solana NFT collection connected to Guild Saga: Labyrinths on Epic Games Store.

The site tracks collection supply and burns, holders and ownership concentration, World Mode staking and quest activity, secondary-market history, mint history, rarity, royalties, and project-connected wallet activity.

## Production rule

The public site does **not** depend on Dune. The final Dune queries are retained only as a frozen migration specification for the Aug. 26, 2026 cutover. Future production collection is designed to use direct Solana/provider data and persisted incremental state.

## Repository structure

- `site/` — React website source and generated frontend data
- `data/baseline/` — immutable historical/cutover datasets
- `data/state/` — compact current deltas, append-only live sales, checkpoints
- `data/history/` — persisted daily history such as floor/listings
- `collector/` — independent Solana normalization and deterministic state/sale rules
- `scripts/` — data generation, validation, and migration/backtest tools
- `tests/` — frozen Aug. 26 fixture plus parser/state regression tests
- `docs/` — independent pipeline design and archived final Dune migration logic

## Phase 1 validation

From the repository root:

```bash
python -m unittest tests.test_collector_contracts -v
python scripts/validate_cutover.py
python scripts/validate.py
```

`validate_cutover.py` permanently proves the Aug. 26 cutover fixture has not changed. `validate.py` validates current/live invariants and intentionally does **not** require future KPIs to stay equal to Aug. 26.

## Market migration backtest

For local testing only, provide a Helius API key as `HELIUS_API_KEY` or a repository-root `key.txt`. `alchemy_key.txt` / `ALCHEMY_API_KEY` is an optional fallback. Both key files are ignored by Git.

Then run:

```bash
python scripts/capture_market_backtest.py
```

The backtest fetches public raw Solana transactions for known canonical sales and must reproduce their buyer, seller, Hero mint, marketplace, price, and royalty amounts before live market collection is enabled.
