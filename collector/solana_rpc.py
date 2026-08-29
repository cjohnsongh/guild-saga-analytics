"""Small stdlib Solana RPC client used by collector/backtest jobs."""
from __future__ import annotations

import json
import os
import time
import urllib.error
import urllib.parse
import urllib.request
from pathlib import Path
from typing import Any

HELIUS_TEMPLATE = "https://mainnet.helius-rpc.com/?api-key={key}"
ALCHEMY_TEMPLATE = "https://solana-mainnet.g.alchemy.com/v2/{key}"


def load_secret(env_name: str, fallback_path: Path | None = None) -> str | None:
    value = os.environ.get(env_name, "").strip()
    if value:
        return value
    if fallback_path and fallback_path.exists():
        value = fallback_path.read_text(encoding="utf-8-sig").strip()
        return value or None
    return None


class RpcClient:
    def __init__(self, url: str, label: str, max_retries: int = 6, timeout: int = 75):
        self.url = url
        self.label = label
        self.max_retries = max_retries
        self.timeout = timeout
        self.calls = 0

    def call(self, method: str, params: Any) -> Any:
        body = json.dumps({
            "jsonrpc": "2.0",
            "id": f"guild-saga-{self.label.lower()}",
            "method": method,
            "params": params,
        }).encode("utf-8")

        last: str | None = None
        for attempt in range(self.max_retries):
            req = urllib.request.Request(
                self.url,
                data=body,
                headers={
                    "Content-Type": "application/json",
                    "Accept": "application/json",
                    "User-Agent": "GuildSagaIndependentCollector/1.0",
                },
                method="POST",
            )
            try:
                with urllib.request.urlopen(req, timeout=self.timeout) as resp:
                    obj = json.loads(resp.read().decode("utf-8"))
                if obj.get("error") is not None:
                    err = obj["error"]
                    code = err.get("code") if isinstance(err, dict) else None
                    if code in (429, -32005, -32029) and attempt + 1 < self.max_retries:
                        time.sleep(min(30, 2 ** attempt) + 0.25)
                        continue
                    raise RuntimeError(f"{self.label} {method}: {err}")
                self.calls += 1
                return obj.get("result")
            except urllib.error.HTTPError as exc:
                detail = exc.read().decode("utf-8", errors="replace")
                last = f"HTTP {exc.code}: {detail[:800]}"
                if exc.code in (429, 500, 502, 503, 504) and attempt + 1 < self.max_retries:
                    time.sleep(min(30, 2 ** attempt) + 0.25)
                    continue
                raise RuntimeError(f"{self.label} {method}: {last}") from exc
            except (urllib.error.URLError, TimeoutError, json.JSONDecodeError) as exc:
                last = repr(exc)
                if attempt + 1 < self.max_retries:
                    time.sleep(min(30, 2 ** attempt))
                    continue
                raise RuntimeError(f"{self.label} {method}: {last}") from exc
        raise RuntimeError(f"{self.label} {method} failed: {last}")

    def get_transaction(self, signature: str) -> dict[str, Any] | None:
        return self.call("getTransaction", [
            signature,
            {
                "encoding": "json",
                "commitment": "finalized",
                "maxSupportedTransactionVersion": 0,
            },
        ])


def load_combined_keys(path: Path) -> tuple[str | None, str | None]:
    """Load a local two-key text file without ever logging either secret.

    Expected format: one non-empty key per line. Alchemy keys begin with
    ``alch``; the other non-empty line is treated as the Helius key.
    """
    if not path.exists():
        return None, None

    values = [
        line.strip()
        for line in path.read_text(encoding="utf-8-sig").splitlines()
        if line.strip()
    ]
    alchemy = next((value for value in values if value.lower().startswith("alch")), None)
    helius_candidates = [value for value in values if value != alchemy]

    if len(helius_candidates) > 1:
        raise RuntimeError(
            "keys.txt contains more than one non-Alchemy value. "
            "Keep exactly one Helius key and one Alchemy key, one per line."
        )

    helius = helius_candidates[0] if helius_candidates else None
    return helius, alchemy


def clients_from_repo(root: Path) -> list[RpcClient]:
    clients: list[RpcClient] = []

    # Environment variables remain highest priority for eventual CI.
    hk = load_secret("HELIUS_API_KEY")
    ak = load_secret("ALCHEMY_API_KEY")

    # Existing two-file local format remains supported.
    if not hk:
        hk = load_secret("HELIUS_API_KEY", root / "key.txt")
    if not ak:
        ak = load_secret("ALCHEMY_API_KEY", root / "alchemy_key.txt")

    # Convenience local format: one ignored keys.txt with both secrets.
    if not hk or not ak:
        combined_hk, combined_ak = load_combined_keys(root / "keys.txt")
        hk = hk or combined_hk
        ak = ak or combined_ak

    if hk:
        clients.append(
            RpcClient(
                HELIUS_TEMPLATE.format(key=urllib.parse.quote(hk, safe="")),
                "Helius",
            )
        )
    if ak:
        clients.append(
            RpcClient(
                ALCHEMY_TEMPLATE.format(key=urllib.parse.quote(ak, safe="")),
                "Alchemy",
            )
        )

    if not clients:
        raise RuntimeError(
            "No RPC API key found. Set HELIUS_API_KEY / ALCHEMY_API_KEY, "
            "or use key.txt + alchemy_key.txt, or place both keys one-per-line "
            "in ignored keys.txt in the repository root."
        )
    return clients


def get_transaction_with_fallback(clients: list[RpcClient], signature: str) -> tuple[dict[str, Any], str]:
    errors = []
    for client in clients:
        try:
            tx = client.get_transaction(signature)
            if tx is not None:
                return tx, client.label
            errors.append(f"{client.label}: null transaction")
        except Exception as exc:
            errors.append(f"{client.label}: {exc}")
    raise RuntimeError(f"Could not fetch {signature}: {' | '.join(errors)}")
