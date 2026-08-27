# Guild Saga Heroes Analytics

Portable static analytics site replacing the Dune dashboard.

## Architecture

- **Python** builds deterministic dashboard JSON from canonical local state.
- **GitHub Actions** will later update the three live collectors (Hero state, market history, floor/listings).
- **Cloudflare Pages** will only host the finished static site.
- No Dune query archive is stored in this repository.

## Production data

- `data/baseline/` — immutable trusted baselines required by the live updater.
- `data/state/hero_deltas.csv` — post-baseline Hero state deltas at cutover.
- `data/state/market_live_sales.csv` — post-baseline sales at cutover.
- `data/history/floor_listings.csv` — full floor/listing series through cutover, with explicit source provenance.
- `data/state/checkpoints.json` — current cutover/checkpoint information.

## First milestone: offline parity

```powershell
python scripts/build_dashboard_data.py
python scripts/validate.py
```

Expected result:

```text
PASS: offline snapshot exactly matches the Aug. 26, 2026 Dune gold master.
PASS: structural invariants are healthy.
```

The generated frontend data is written to `site/public/data/`.

## Cutover gold master — 2026-08-26

- Active Supply: 9,832
- Beneficial Holders: 1,962
- Staked Heroes: 5,851
- Staked Supply: 59.5%
- Quest Active ≤30d: 1,122
- Quest Idle 1+ Year: 3,490
- Burned: 168
- Secondary Sales: 19,797
- Secondary Volume: 35,725.707237474 SOL
- Unique Buyers: 5,472
- Heroes Ever Sold: 8,405
- Guild Saga Royalties: 1,742.09 SOL
- Mint + Guild Saga Royalties: 16,592.09 SOL
- Floor: 0.07 SOL
- Listings: 433

## Frontend milestone: Dune-layout parity

The React/Vite frontend is now scaffolded in `site/`. At the 1408px reference viewport it uses the measured 1164px / 12-column Dune dashboard geometry.

On Windows PowerShell, use `npm.cmd` (not `npm`) if PowerShell blocks the npm.ps1 shim:

```powershell
cd site
npm.cmd install
npm.cmd run dev
```

The website reads only generated JSON from `site/public/data/`.

Next: inspect the first local render against the Dune screenshot and tune geometry before visual redesign.
