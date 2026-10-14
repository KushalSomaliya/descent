"""Pure rule functions. No randomness, no I/O."""

from __future__ import annotations

from dataclasses import dataclass

from .config import Rules
from .state import Agent, Allocation


# ---------- combat ----------

def attack_strength(alloc: Allocation, item: str, rules: Rules) -> int:
    if alloc.attack <= 0:
        return 0
    bonus = 0
    if item == "knife":
        bonus = rules.knife_attack_bonus
    elif item == "frying_pan":
        bonus = rules.pan_attack_bonus
    return alloc.attack + bonus


def guard_strength(alloc: Allocation, item: str, rules: Rules) -> int:
    if alloc.guard <= 0:
        return 0
    bonus = 0
    if item == "helmet":
        bonus = rules.helmet_guard_bonus
    elif item == "frying_pan":
        bonus = rules.pan_guard_bonus
    return alloc.guard + bonus


@dataclass
class Hit:
    attacker: str
    defender: str
    attack: int
    guard: int
    damage_points: int
    damage_hp: float


def resolve_hit(attacker: Agent, a_alloc: Allocation, defender: Agent, d_alloc: Allocation, rules: Rules) -> Hit | None:
    atk = attack_strength(a_alloc, attacker.item, rules)
    if atk <= 0:
        return None
    grd = guard_strength(d_alloc, defender.item, rules)
    pts = max(0, atk - grd)
    return Hit(attacker.id, defender.id, atk, grd, pts, pts * rules.damage_hp_per_point)


# ---------- eating ----------

def bite_size(eat_points: int, fullness: float, item: str, rules: Rules) -> float:
    if eat_points <= 0:
        return 0.0
    hunger_factor = max(rules.min_hunger_factor, 1.0 - fullness / rules.fullness_max)
    mult = rules.spoon_eat_multiplier if item == "big_spoon" else 1.0
    return rules.eat_rate * eat_points * mult * hunger_factor


def apply_food(agent: Agent, amount: float, rules: Rules) -> None:
    """Food fills fullness first; overflow heals HP; anything beyond that is wasted."""
    if amount <= 0:
        return
    space = max(0.0, rules.fullness_max - agent.fullness)
    to_belly = min(space, amount)
    agent.fullness += to_belly
    overflow = amount - to_belly
    if overflow > 0:
        agent.hp = min(rules.max_hp, agent.hp + overflow * rules.overflow_hp_per_food)


def eat_from_pool(
    pool: float,
    eaters: list[tuple[Agent, int, float | None]],
    rules: Rules,
) -> tuple[dict[str, float], float]:
    """Simulate `ticks_per_floor` ticks of eating from a shared pool.

    eaters: (agent, eat_points, limit) where limit is the max total the agent
    will take from this pool (None = no limit).
    Returns ({agent_id: amount_eaten}, remaining_pool).
    """
    taken = {a.id: 0.0 for a, _, _ in eaters}
    for _ in range(rules.ticks_per_floor):
        if pool <= 1e-9:
            break
        wants: dict[str, float] = {}
        for agent, pts, limit in eaters:
            b = bite_size(pts, agent.fullness, agent.item, rules)
            if limit is not None:
                b = min(b, max(0.0, limit - taken[agent.id]))
            if b > 1e-9:
                wants[agent.id] = b
        if not wants:
            break
        total = sum(wants.values())
        scale = min(1.0, pool / total)
        for agent, _, _ in eaters:
            if agent.id in wants:
                got = wants[agent.id] * scale
                taken[agent.id] += got
                apply_food(agent, got, rules)
        pool -= total * scale
    return taken, max(0.0, pool)


# ---------- night ----------

@dataclass
class NightResult:
    decay: float
    missing_food: float
    hp_lost: float
    hp_regen: float


def night(agent: Agent, rest_points: int, rules: Rules) -> NightResult:
    decay = max(rules.min_hunger_decay, rules.hunger_decay - rules.rest_decay_per_point * rest_points)
    if agent.item == "blanket":
        decay = max(0.0, decay - rules.blanket_decay_reduction)
    agent.fullness -= decay
    missing = 0.0
    hp_lost = 0.0
    if agent.fullness < 0:
        missing = -agent.fullness
        agent.fullness = 0.0
        hp_lost = missing * rules.hp_loss_per_missing_food
        agent.hp -= hp_lost
    regen = 0.0
    if agent.hp > 0:
        if agent.fullness >= rules.regen_fullness_threshold:
            regen += rules.regen_hp
        before = agent.hp
        agent.hp = min(rules.max_hp, agent.hp + regen)
        regen = agent.hp - before
    return NightResult(decay, missing, hp_lost, regen)


def body_food(victim_hearts_at_start: int, rules: Rules) -> float:
    return float(min(rules.body_food_max, max(rules.body_food_min, victim_hearts_at_start)))


def clamp_allocation(raw: dict, budget: int, talk_used: int) -> tuple[Allocation, bool]:
    """Validate an evening allocation. Returns (allocation, was_valid)."""
    valid = True
    remaining = max(0, budget - talk_used)
    vals: dict[str, int] = {}
    for key in ("eat", "guard", "attack", "rest"):
        v = raw.get(key, 0) if isinstance(raw, dict) else 0
        try:
            iv = int(round(float(v)))
        except (TypeError, ValueError):
            iv, valid = 0, False
        if iv < 0:
            iv, valid = 0, False
        vals[key] = iv
    total = sum(vals.values())
    if total > remaining:
        valid = False
        # Scale down proportionally, then trim from the largest buckets.
        if total > 0:
            scaled = {k: int(v * remaining / total) for k, v in vals.items()}
            leftover = remaining - sum(scaled.values())
            for k in sorted(vals, key=lambda k: vals[k] * remaining / total - scaled[k], reverse=True):
                if leftover <= 0:
                    break
                scaled[k] += 1
                leftover -= 1
            vals = scaled
    limit = raw.get("platform_limit") if isinstance(raw, dict) else None
    plimit: float | None = None
    if limit is not None:
        try:
            plimit = max(0.0, float(limit))
        except (TypeError, ValueError):
            plimit, valid = None, False
    use_kit = bool(raw.get("use_kit")) if isinstance(raw, dict) and raw.get("use_kit") not in ("false", "no", "0") else False
    return Allocation(talk=talk_used, platform_limit=plimit, use_kit=use_kit, **vals), valid


def use_first_aid(agent: Agent, rules: Rules) -> tuple[float, str]:
    """Spend one bandage on wounds. Returns (hp_healed, reason). Hunger damage is never healed."""
    if agent.item != "first_aid_kit":
        return 0.0, "no_kit"
    if agent.kit_charges <= 0:
        return 0.0, "empty"
    heal = min(rules.first_aid_heal_hp, agent.wounds, max(0.0, rules.max_hp - agent.hp))
    if heal <= 1e-9:
        return 0.0, "no_wounds"
    agent.hp += heal
    agent.wounds -= heal
    agent.kit_charges -= 1
    agent.kit_used += 1
    return heal, "healed"
