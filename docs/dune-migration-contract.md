# Final Dune logic retained as migration contract

These files were supplied on 2026-08-28 as the exact final production-query backup. They are **not** a future data source.

| Backup query | Independent responsibility |
| --- | --- |
| Guild Saga - Live Hero State | Hero movement state machine, World Mode custody, quest activity, holders, burns |
| Guild Saga - Live Market History | Direct Magic Eden/Tensor sale parsing, royalties, market aggregation |
| Guild Saga - Daily Floor & Listings | Daily floor/listed-count snapshot behavior |
| Guild Saga - Launch KPIs | Frozen launch facts |
| Guild Saga - Mint Phases | Frozen launch facts |
| Guild Saga - Public Mint Distribution | Frozen launch facts |
| Guild Saga - Initial Mint Treasury Split | Frozen economy facts |
| Guild Saga - 3xQ to Fgk8 Asset Handoff | Frozen economy facts |
| Guild Saga - Core Wallet USDC Distribution | Frozen economy facts |
| SOL Converted to USDC Over Time | Frozen economy history |

The Python constants and unit tests in `collector/` are the first executable extraction of the final dynamic-query rules. Before enabling production writes, raw historical transaction fixtures will be added to prove the normalizer produces the same decoded events on real Solana transactions.
