"""Deterministic Guild Saga Hero-state reducer.

The reducer is the independent equivalent of the final Live Hero State query.
It consumes normalized Solana movements + World Mode calls and updates the
canonical per-Hero state. Quest age buckets are derived from timestamps later;
they are never stored as events.
"""

from __future__ import annotations

from dataclasses import dataclass, replace
from datetime import datetime, timezone
from typing import Iterable, Mapping, MutableMapping, Sequence

from .constants import (
    WORLD_MODE_PROGRAM,
    WORLD_QUEST_RESTART_DISCRIMINATOR,
    WORLD_STAKE_DISCRIMINATOR,
    WORLD_UNSTAKE_DISCRIMINATOR,
)


def _utc(dt: datetime) -> datetime:
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=timezone.utc)
    return dt.astimezone(timezone.utc)


def iso_z(dt: datetime) -> str:
    return _utc(dt).isoformat().replace("+00:00", "Z")


def parse_iso(value: str | None) -> datetime | None:
    if not value:
        return None
    value = value.strip().replace(" UTC", "+00:00").replace("Z", "+00:00")
    dt = datetime.fromisoformat(value)
    return _utc(dt)


@dataclass(frozen=True)
class HeroState:
    mint: str
    burned: int = 0
    burn_utc: str | None = None
    burn_signature: str | None = None
    current_raw_owner: str | None = None
    current_world_staked: int = 0
    current_beneficial_owner: str | None = None
    current_world_staking_wallet: str | None = None
    latest_event_utc: str | None = None
    latest_signature: str | None = None
    quest_user_wallet: str | None = None
    quest_staking_wallet: str | None = None
    current_stake_deposit_utc: str | None = None
    current_stake_deposit_signature: str | None = None
    best_known_last_qualifying_quest_utc: str | None = None
    best_known_last_qualifying_quest_signature: str | None = None
    quest_history_source: str | None = None
    deep_history_status: str | None = None


@dataclass(frozen=True)
class RawMovement:
    signature: str
    mint: str
    event_time: datetime
    action: str  # transfer | burn
    from_owner: str | None
    to_owner: str | None


@dataclass(frozen=True)
class WorldCall:
    signature: str
    event_time: datetime
    action: str  # STAKE | UNSTAKE | QUEST_RESTART
    user_wallet: str
    staking_wallet: str | None = None


def classify_world_call(
    *,
    signature: str,
    event_time: datetime,
    executing_account: str,
    data: bytes,
    signer: str | None,
    account_arguments: Sequence[str],
) -> WorldCall | None:
    """Classify one World Mode instruction using the validated discriminators.

    The validated stake/unstake layout places the staking wallet in the fourth
    account argument, which is Python index 3.
    """
    if executing_account != WORLD_MODE_PROGRAM or not signer:
        return None

    action: str | None = None
    staking_wallet: str | None = None
    if data.startswith(WORLD_STAKE_DISCRIMINATOR):
        action = "STAKE"
    elif data.startswith(WORLD_UNSTAKE_DISCRIMINATOR):
        action = "UNSTAKE"
    elif data.startswith(WORLD_QUEST_RESTART_DISCRIMINATOR):
        action = "QUEST_RESTART"
    else:
        return None

    if action in {"STAKE", "UNSTAKE"}:
        if len(account_arguments) < 4:
            return None
        staking_wallet = account_arguments[3]

    return WorldCall(
        signature=signature,
        event_time=_utc(event_time),
        action=action,
        user_wallet=signer,
        staking_wallet=staking_wallet,
    )


def _clear_quest_and_stake(state: HeroState) -> dict[str, object | None]:
    return {
        "current_world_staked": 0,
        "current_world_staking_wallet": None,
        "quest_user_wallet": None,
        "quest_staking_wallet": None,
        "current_stake_deposit_utc": None,
        "current_stake_deposit_signature": None,
        "best_known_last_qualifying_quest_utc": None,
        "best_known_last_qualifying_quest_signature": None,
        "quest_history_source": None,
        "deep_history_status": None,
    }


def apply_movement(
    state: HeroState,
    movement: RawMovement,
    matching_world_call: WorldCall | None = None,
) -> HeroState:
    """Apply one chronological Hero movement.

    A World Mode call only changes custody semantics when it is in the same
    transaction as the Hero transfer and the transfer direction validates the
    call. Otherwise the movement is an ordinary transfer/burn.
    """
    when = iso_z(movement.event_time)
    common = {
        "current_raw_owner": movement.to_owner,
        "latest_event_utc": when,
        "latest_signature": movement.signature,
    }

    if movement.action == "burn":
        return replace(
            state,
            burned=1,
            burn_utc=when,
            burn_signature=movement.signature,
            current_raw_owner=None,
            current_beneficial_owner=None,
            latest_event_utc=when,
            latest_signature=movement.signature,
            **_clear_quest_and_stake(state),
        )

    if movement.action != "transfer":
        raise ValueError(f"Unsupported movement action: {movement.action}")

    wc = matching_world_call
    if (
        wc
        and wc.signature == movement.signature
        and wc.action == "STAKE"
        and wc.staking_wallet
        and movement.to_owner == wc.staking_wallet
    ):
        return replace(
            state,
            **common,
            current_world_staked=1,
            current_beneficial_owner=wc.user_wallet,
            current_world_staking_wallet=wc.staking_wallet,
            quest_user_wallet=wc.user_wallet,
            quest_staking_wallet=wc.staking_wallet,
            current_stake_deposit_utc=iso_z(wc.event_time),
            current_stake_deposit_signature=wc.signature,
            best_known_last_qualifying_quest_utc=None,
            best_known_last_qualifying_quest_signature=None,
            quest_history_source="INDEPENDENT_LIVE",
            deep_history_status="LIVE_POST_CUTOVER",
        )

    if (
        wc
        and wc.signature == movement.signature
        and wc.action == "UNSTAKE"
        and wc.staking_wallet
        and movement.from_owner == wc.staking_wallet
        and movement.to_owner == wc.user_wallet
    ):
        return replace(
            state,
            **common,
            current_beneficial_owner=movement.to_owner,
            **_clear_quest_and_stake(state),
        )

    # Ordinary non-World transfer: beneficial owner follows raw destination.
    return replace(
        state,
        **common,
        current_beneficial_owner=movement.to_owner,
        **_clear_quest_and_stake(state),
    )


def apply_quest_restart(state: HeroState, call: WorldCall) -> HeroState:
    """Advance quest time iff the call qualifies for the Hero's current stay."""
    if call.action != "QUEST_RESTART" or state.current_world_staked != 1:
        return state
    if state.quest_user_wallet != call.user_wallet:
        return state

    stake_time = parse_iso(state.current_stake_deposit_utc)
    prior_time = parse_iso(state.best_known_last_qualifying_quest_utc)
    event_time = _utc(call.event_time)

    if stake_time is None or event_time < stake_time:
        return state
    if prior_time is not None and event_time <= prior_time:
        return state

    return replace(
        state,
        best_known_last_qualifying_quest_utc=iso_z(event_time),
        best_known_last_qualifying_quest_signature=call.signature,
        quest_history_source="INDEPENDENT_LIVE",
        deep_history_status="LIVE_POST_CUTOVER",
    )


def apply_quest_restart_to_collection(
    states: MutableMapping[str, HeroState], call: WorldCall
) -> int:
    """Apply a user-level quest restart to every qualifying currently-staked Hero."""
    changed = 0
    for mint, state in list(states.items()):
        updated = apply_quest_restart(state, call)
        if updated != state:
            states[mint] = updated
            changed += 1
    return changed


def quest_bucket(state: HeroState, as_of: datetime) -> str | None:
    if state.current_world_staked != 1:
        return None
    qt = parse_iso(state.best_known_last_qualifying_quest_utc)
    if qt is None:
        return "Never quested"
    days = (_utc(as_of) - qt).total_seconds() / 86400.0
    if days <= 7:
        return "Active 0–7d"
    if days <= 30:
        return "Idle 8–30d"
    if days <= 90:
        return "Idle 31–90d"
    if days <= 180:
        return "Idle 91–180d"
    if days <= 365:
        return "Idle 181–365d"
    return "Idle 1+ year"
