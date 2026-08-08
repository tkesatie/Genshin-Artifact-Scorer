"""
Module: leveling_efficiency.py

Purpose:
Artifact leveling recommendations.

The primary path (`build_leveling_plan_from_optimizer`) is driven by the
global optimizer's "build optimality" probabilities (see
optimizer.compute_optimal_probabilities) rather than a raw roll-count/
threshold heuristic:

  - CONTESTED slot: two or more candidate pieces have comparable, unresolved
    probability of ending up the optimal piece for that slot (e.g. three
    pieces each ~33%). Committing real budget to any one of them risks
    leveling the wrong piece, so each viable contender only gets a small,
    cheap "scouting" step - one checkpoint's worth of levels (default +4),
    which reveals its next roll/hidden substat and sharpens the probability
    estimate for the *next* run.
  - RESOLVED slot: one candidate's probability clearly separates from the
    rest. Its own future-roll variance is already priced into that
    probability by the optimizer's Monte Carlo simulation, so it's safe to
    commit it the rest of the way to max level in one action.

Character urgency (character_scoring.score_character's `score`) prioritizes
WHICH character's actions get budget first when the immediate/lifetime
budgets can't cover everything; the contest/resolve logic decides WHAT to
level per character and by how much.

A legacy closed-form/threshold path (`build_leveling_plan`) is kept
unchanged as a fallback for characters the optimizer didn't run for (split
sets, characters with no equipped pieces, etc.) so they aren't silently
dropped from the plan. `build_combined_leveling_plan` merges both.
"""

from typing import Dict, List, Any, Optional
from math import comb

from artifact_utils import MAX_LEVEL, STAT_LABEL, get_leveling_cost


# ---- Tier-upgrade gate (shared by both the legacy and optimizer planners) ----
#
# A leveling action is only "high priority" if the candidate piece can raise
# the slot's tier above what's currently equipped there:
#   Equipped Needs Work / Missing -> candidate must reach at least Good
#   Equipped Good                 -> candidate must reach Excellent
#   Equipped Excellent            -> already top tier, nothing beats it
# This never hard-blocks an action - non-upgrading actions just sink to the
# bottom of the priority order (funded only once every tier-upgrading action
# has been funded). Controlled by rules.yaml leveling.require_tier_upgrade.

def reachable_tier_for(max_rolls: Optional[float], good: Optional[float], excellent: Optional[float]) -> Optional[str]:
    """Same tier boundaries character_scoring.score_character uses for the
    equipped piece, applied to a candidate's own ceiling (max reachable
    useful rolls) instead of its current roll count."""
    if max_rolls is None or good is None or excellent is None:
        return None
    if max_rolls >= excellent:
        return "Excellent"
    elif max_rolls >= good:
        return "Good"
    else:
        return "Needs Work"


def tier_upgrade_ok(equipped_tier: Optional[str], reachable_tier: Optional[str]) -> bool:
    """Does a candidate with `reachable_tier` beat the slot's `equipped_tier`?"""
    if reachable_tier is None:
        return False
    if equipped_tier == "Excellent":
        return False
    if equipped_tier == "Good":
        return reachable_tier == "Excellent"
    # Needs Work / Missing / unknown equipped tier -> any real tier clears it
    return reachable_tier in ("Good", "Excellent")


def any_tier_upgrade_available(
    optimizer_candidates_by_char: Dict[str, Dict[str, List[Dict[str, Any]]]],
    bench_results: List[Dict[str, Any]],
    skip_chars: set,
    char_slot_tier_lookup: Dict[Any, str],
) -> bool:
    """
    Existence check across the WHOLE roster (both planners, every
    character), independent of budget/Mora - used to decide whether
    sidegrades are eligible for selection at all this run. If even one
    tier-upgrading candidate exists anywhere, no sidegrade should be a
    selectable candidate for anyone; sidegrades only become eligible once
    nothing better exists roster-wide.
    """
    skip_chars = skip_chars or set()

    for char_name, slots in optimizer_candidates_by_char.items():
        if char_name in skip_chars:
            continue
        for candidates in slots.values():
            if any(c.get("tier_upgrade_ok") for c in candidates):
                return True

    for b in bench_results:
        if b.get("character") in skip_chars:
            continue
        if b.get("verdict") == "Dead end":
            continue
        equipped_tier = char_slot_tier_lookup.get((b.get("character"), b.get("slot")))
        reachable = reachable_tier_for(b.get("max_rolls"), b.get("good"), b.get("excellent"))
        if tier_upgrade_ok(equipped_tier, reachable):
            return True

    return False


# ---- Binomial tail (exact, fast for n <= 4) ----

def _binom_tail(n: int, p: float, k: int) -> float:
    """P(X >= k) for X ~ Binomial(n, p). n is small (<=4) in our use case."""
    if k <= 0:
        return 1.0
    if k > n:
        return 0.0
    if p == 0.0:
        return 0.0
    if p == 1.0:
        return 1.0 if k <= n else 0.0

    prob = 0.0
    for x in range(k, n + 1):
        prob += comb(n, x) * (p ** x) * ((1 - p) ** (n - x))
    return prob


# ---- Posterior probability (closed-form, no simulation) ----

def posterior_reach_probability(
    artifact: Dict[str, Any],
    useful_stats: List[str],
    threshold: int,
    current_rolls: int,
    target_level: int = None
) -> float:
    """
    Exact probability that the artifact reaches `threshold` useful rolls
    by `target_level`.

    Uses the fact that:
    - Hidden line reveal is deterministic.
    - Remaining upgrades follow Binomial(remaining_events, p),
      where p = useful_active / active_count.
    """
    rarity = artifact.get("rarity", 5)
    max_level = MAX_LEVEL.get(rarity, 20)
    current_level = artifact.get("level", 0)

    if target_level is None:
        target_level = max_level

    if target_level <= current_level:
        return 1.0 if current_rolls >= threshold else 0.0

    remaining_levels = target_level - current_level
    total_events = (remaining_levels + 3) // 4

    hidden_subs = artifact.get("unactivatedSubstats", [])

    hidden_gain = 0
    if hidden_subs and total_events > 0:
        if STAT_LABEL.get(hidden_subs[0].get("key")) in useful_stats:
            hidden_gain = 1
        total_events -= 1

    active_subs = artifact.get("substats", [])
    useful_active = sum(
        1 for s in active_subs
        if STAT_LABEL.get(s.get("key")) in useful_stats
    )
    active_count = len(active_subs) + (1 if hidden_subs and total_events >= 0 else 0)

    if total_events == 0 or active_count == 0:
        return 1.0 if current_rolls + hidden_gain >= threshold else 0.0

    p = useful_active / active_count
    needed = max(0, threshold - (current_rolls + hidden_gain))

    return _binom_tail(total_events, p, needed)


# ---- Safe target level (O(1) per candidate) ----

def safe_target_level(
    artifact: Dict[str, Any],
    useful_stats: List[str],
    threshold: int,
    current_rolls: int,
    soft_floor: float = 0.15
) -> int:
    """
    Returns the highest safe level to jump to without re-checking.
    Uses hard ceiling and a deterministic worst-case estimate.
    """
    rarity = artifact.get("rarity", 5)
    max_level = MAX_LEVEL.get(rarity, 20)
    current_level = artifact.get("level", 0)

    if current_level >= max_level:
        return max_level

    hidden_subs = artifact.get("unactivatedSubstats", [])
    hidden_is_useful = (
        STAT_LABEL.get(hidden_subs[0].get("key")) in useful_stats
        if hidden_subs else False
    )

    hard_critical = max_level
    soft_critical = max_level

    # ---- Hard critical ----
    # For each level, compute ceiling = current_rolls + (hidden_gain if revealed) + remaining_events
    for lvl in range(current_level + 4, max_level + 1, 4):
        remaining_levels = lvl - current_level
        events = (remaining_levels + 3) // 4

        # Hidden gain if events > 0
        hidden_gain = 1 if (hidden_is_useful and events > 0) else 0
        # Remaining random events after revealing hidden
        random_events = events - (1 if hidden_is_useful and events > 0 else 0)
        ceiling = current_rolls + hidden_gain + random_events

        if ceiling < threshold:
            hard_critical = lvl
            break

    # ---- Soft critical (worst-case deterministic) ----
    # Worst-case: random upgrades all miss; only hidden gain (if any) counts.
    for lvl in range(current_level + 4, max_level + 1, 4):
        remaining_levels = lvl - current_level
        events = (remaining_levels + 3) // 4
        worst_gain = 1 if (hidden_is_useful and events > 0) else 0
        worst_total = current_rolls + worst_gain

        if worst_total < threshold:
            soft_critical = lvl
            break

    earliest_critical = min(hard_critical, soft_critical)
    if earliest_critical <= current_level:
        return current_level
    return max(current_level, earliest_critical - 4)


# ---- Build the plan (single pass over 2000 candidates) ----

def build_leveling_plan(
    bench_results: List[Dict[str, Any]],
    budget_config: Dict[str, Any],
    leveling_config: Dict[str, Any],
    roll_values: Dict[str, Any] = None,  # kept for API compatibility, not used
    skip_chars: set = None,              # characters excluded by the coverage gate
    char_slot_tier_lookup: Dict[Any, str] = None,  # (character, slot) -> equipped tier status
    max_pieces: int = None,              # hard cap on number of pieces selected (None = no cap)
    exclude_non_upgraders: bool = False, # hard-exclude sidegrades (only relevant if a real upgrade exists elsewhere)
) -> Dict[str, Any]:
    """
    Generate a leveling plan from bench results.
    Optimized for 2000+ candidates – O(N) with no heavy re-computation.
    """
    char_slot_tier_lookup = char_slot_tier_lookup or {}
    require_tier_upgrade = leveling_config.get("require_tier_upgrade", True)

    # ---- Extract config ----
    max_mora = budget_config.get("max_mora", 2000000)
    max_exp = budget_config.get("max_artifact_exp", 10000000)
    already_spent_mora = budget_config.get("already_spent_mora", 0)
    already_spent_exp = budget_config.get("already_spent_exp", 0)

    remaining_lifetime_mora = max_mora - already_spent_mora
    remaining_lifetime_exp = max_exp - already_spent_exp

    soft_floor = leveling_config.get("soft_stop_floor", 0.15)
    cliff_ratio = leveling_config.get("cliff_ratio", 0.25)
    max_reveal_fraction = leveling_config.get("max_reveal_fraction", 0.40)
    max_per_character = leveling_config.get("max_per_character", 2)

    # ---- Generate candidates in one pass ----
    candidates = []
    for b in bench_results:
        # Skip dead ends
        if b.get("verdict") == "Dead end":
            continue
        # Skip characters the coverage gate already filtered out
        if skip_chars and b.get("character") in skip_chars:
            continue

        rarity = b.get("rarity", 5)
        max_level = MAX_LEVEL.get(rarity, 20)
        current_level = b.get("level", 0)
        if current_level >= max_level:
            continue

        artifact = b.get("original_artifact")
        if not artifact:
            continue

        good_threshold = b.get("good")
        if good_threshold is None:
            continue

        useful_stats = b.get("useful_stats", [])
        if not useful_stats:
            continue

        current_rolls = b.get("current_rolls", 0)

        # Compute target level (deterministic, fast)
        target = safe_target_level(
            artifact,
            useful_stats,
            good_threshold,
            current_rolls,
            soft_floor
        )

        if target <= current_level:
            continue

        # Costs
        immediate_cost = get_leveling_cost(rarity, current_level, target)
        finish_cost = get_leveling_cost(rarity, current_level, max_level)

        # p_current: we already know if current_rolls >= threshold
        p_current = 1.0 if current_rolls >= good_threshold else 0.0

        # p_target: closed-form
        p_target = posterior_reach_probability(
            artifact,
            useful_stats,
            good_threshold,
            current_rolls,
            target_level=target
        )

        delta_p = p_target - p_current
        if delta_p <= 0:
            continue

        mora_cost = immediate_cost.get("mora", 1)
        exp_cost = immediate_cost.get("exp", 1)
        efficiency_mora = delta_p / max(mora_cost, 1)
        efficiency_exp = delta_p / max(exp_cost, 1)

        # Tier-upgrade gate: does this candidate's own ceiling beat the tier
        # currently equipped in this slot? See module-level tier_upgrade_ok().
        equipped_tier = char_slot_tier_lookup.get((b.get("character"), b.get("slot")))
        reachable_tier = reachable_tier_for(b.get("max_rolls"), good_threshold, b.get("excellent"))
        candidate_tier_upgrade_ok = (
            True if not require_tier_upgrade
            else tier_upgrade_ok(equipped_tier, reachable_tier)
        )

        # Hard exclude: a real tier-upgrading candidate exists somewhere on
        # the roster this run, so this sidegrade never becomes a selectable
        # candidate at all - not deprioritized, not considered regardless of
        # leftover budget. Only skipped when the gate is off.
        if exclude_non_upgraders and not candidate_tier_upgrade_ok:
            continue

        candidates.append({
            "character": b.get("character"),
            "slot": b.get("slot"),
            "artifact_id": b.get("artifact_id"),
            "artifact": artifact,
            "current_level": current_level,
            "target_level": target,
            "immediate_cost": immediate_cost,
            "finish_cost": finish_cost,
            "delta_p": delta_p,
            "efficiency_mora": efficiency_mora,
            "efficiency_exp": efficiency_exp,
            "reachable_tier": reachable_tier,
            "tier_upgrade_ok": candidate_tier_upgrade_ok,
            # Legacy candidates are each their own atomic decision (no
            # contested-slot grouping like the optimizer path), so the
            # group-level flag is just the candidate's own value.
            "group_tier_upgrade_ok": candidate_tier_upgrade_ok,
            # Unified-schema fields so this can sit next to optimizer-driven
            # actions in the combined plan (see build_combined_leveling_plan).
            # These characters had no optimizer data to give a real build
            # optimality probability, so "action_type" is flagged distinctly
            # rather than faked as Scout/Commit.
            "action_type": "Legacy",
            "probability": None,
            "character_score": 0,
        })

    if not candidates:
        return {
            "actions": [],
            "summary": {
                "total_immediate_mora": 0,
                "total_immediate_exp": 0,
                "calculated_immediate_budget_used_fraction": 0.0,
                "total_finish_cost_if_all_completed": {"mora": 0, "exp": 0},
                "remaining_lifetime_mora": remaining_lifetime_mora,
                "remaining_lifetime_exp": remaining_lifetime_exp,
                "lifetime_warning": "No candidates found.",
                "recommendation_text": "No artifacts worth revealing at this time."
            }
        }

    # ---- Sort and select (greedy) ----
    # Tier-upgrading candidates are funded first; non-upgraders (piece can't
    # raise the slot's tier over what's equipped) only get funded once every
    # upgrading candidate has already been considered.
    candidates.sort(key=lambda c: (-c["tier_upgrade_ok"], -c["efficiency_mora"], -c["efficiency_exp"]))

    selected = []
    remaining_immediate_mora = max_reveal_fraction * remaining_lifetime_mora
    remaining_immediate_exp = max_reveal_fraction * remaining_lifetime_exp
    selected_lifetime_finish_sum_mora = 0
    selected_lifetime_finish_sum_exp = 0
    per_char_count = {}
    max_eff = candidates[0]["efficiency_mora"]
    cliff = cliff_ratio * max_eff

    total_committed_mora = 0
    total_committed_exp = 0
    selected_lifetime_finish_sum_mora = 0
    selected_lifetime_finish_sum_exp = 0

    for c in candidates:
        if max_pieces is not None and len(selected) >= max_pieces:
            break

        char = c["character"]
        per_char_count[char] = per_char_count.get(char, 0)
        if per_char_count[char] >= max_per_character:
            continue

        if c["immediate_cost"]["mora"] > remaining_immediate_mora:
            continue
        if c["immediate_cost"]["exp"] > remaining_immediate_exp:
            continue

        if total_committed_mora + c["immediate_cost"]["mora"] + c["finish_cost"]["mora"] > remaining_lifetime_mora:
            continue
        if total_committed_exp + c["immediate_cost"]["exp"] + c["finish_cost"]["exp"] > remaining_lifetime_exp:
            continue

        if c["efficiency_mora"] < cliff:
            break

        selected.append(c)
        remaining_immediate_mora -= c["immediate_cost"]["mora"]
        remaining_immediate_exp -= c["immediate_cost"]["exp"]
        total_committed_mora += c["immediate_cost"]["mora"] + c["finish_cost"]["mora"]
        total_committed_exp += c["immediate_cost"]["exp"] + c["finish_cost"]["exp"]
        selected_lifetime_finish_sum_mora += c["finish_cost"]["mora"]
        selected_lifetime_finish_sum_exp += c["finish_cost"]["exp"]
        per_char_count[char] += 1

    # ---- Summary ----
    total_immediate_mora = sum(c["immediate_cost"]["mora"] for c in selected)
    total_immediate_exp = sum(c["immediate_cost"]["exp"] for c in selected)
    total_finish_mora = sum(c["finish_cost"]["mora"] for c in selected)
    total_finish_exp = sum(c["finish_cost"]["exp"] for c in selected)

    warning = None
    if total_finish_mora > remaining_lifetime_mora:
        warning = f"Finishing all revealed pieces would exceed your lifetime budget by {total_finish_mora - remaining_lifetime_mora:,.0f} Mora."

    if selected:
        rec_text = (
            f"Spend {total_immediate_mora:,.0f} Mora and {total_immediate_exp:,.0f} EXP "
            f"to reveal substats on {len(selected)} artifact(s). "
            f"After importing, you'll have {remaining_lifetime_mora - total_finish_mora:,.0f} Mora left "
            f"to finish the best one(s)."
        )
    else:
        rec_text = "No artifacts selected for revealing under current budget and constraints."

    summary = {
        "total_immediate_mora": total_immediate_mora,
        "total_immediate_exp": total_immediate_exp,
        "calculated_immediate_budget_used_fraction": (
            total_immediate_mora / (max_reveal_fraction * remaining_lifetime_mora)
            if remaining_lifetime_mora > 0 else 0
        ),
        "total_finish_cost_if_all_completed": {"mora": total_finish_mora, "exp": total_finish_exp},
        "remaining_lifetime_mora": remaining_lifetime_mora,
        "remaining_lifetime_exp": remaining_lifetime_exp,
        "lifetime_warning": warning,
        "recommendation_text": rec_text,
    }

    return {
        "actions": selected,
        "summary": summary,
    }


# ---- Optimizer-driven planning (primary path) ----

def _select_contenders(
    candidates: List[Dict[str, Any]],
    min_relevant_prob: float,
    contested_margin: float,  # kept for API compatibility, no longer used in filtering
    max_contenders: int,
) -> List[Dict[str, Any]]:
    if not candidates:
        return []
    # rules.yaml is explicit: a candidate below min_relevant_probability is
    # "dead weight, not worth planning for" - ignored entirely, no
    # exceptions. Previously, if NOTHING cleared that floor, the top
    # candidate got force-included anyway regardless of how low its real
    # probability was - directly contradicting that rule. That was rare on
    # its own (a slot's full bench pool usually has SOMETHING above 8%),
    # but combined with the tier-upgrade hard-exclude in
    # build_leveling_plan_from_optimizer (which can strip out the strongest
    # candidates for a slot before this function ever sees them, without
    # redistributing their probability mass to what's left), it became
    # common: a slot's entire surviving candidate list would fall below the
    # floor and still get funded. If nothing clears the bar, the slot
    # simply gets no action this run - not a forced worst-case pick.
    relevant = [c for c in candidates if c.get("probability", 0.0) >= min_relevant_prob]
    return relevant[:max_contenders]


def _slot_is_resolved(
    contenders: List[Dict[str, Any]],
    resolved_prob_threshold: float,
    contested_margin: float,
) -> bool:
    """
    A slot is RESOLVED (safe to commit the top candidate to max level) when
    the top candidate's probability clears an absolute confidence bar - and,
    if there's a runner-up, leads it by enough margin too. Otherwise it's
    CONTESTED: nothing here is confident enough to commit yet.

    A single surviving contender is NOT automatically resolved. "Only one
    candidate left" can mean two very different things: genuinely the only
    real option on the bench (confidently committable), or just the only
    one left after some other filter (e.g. the roster-wide tier-upgrade
    hard-exclude) stripped out a stronger candidate that couldn't clear a
    different bar. Those aren't the same thing, so being alone no longer
    substitutes for being good - it still has to clear resolved_prob_threshold
    on its own probability.
    """
    if not contenders:
        return False
    top_prob = contenders[0].get("probability", 0.0)
    if len(contenders) == 1:
        return top_prob >= resolved_prob_threshold
    second_prob = contenders[1].get("probability", 0.0)
    return top_prob >= resolved_prob_threshold and (top_prob - second_prob) >= contested_margin


def plan_slot_actions(
    char_name: str,
    slot: str,
    candidates: List[Dict[str, Any]],
    leveling_config: Dict[str, Any],
) -> List[Dict[str, Any]]:
    min_relevant_prob = leveling_config.get("min_relevant_probability", 0.08)
    max_contenders = leveling_config.get("max_contenders_per_slot", 3)
    resolved_prob_threshold = leveling_config.get("resolved_probability", 0.60)
    contested_margin = leveling_config.get("contested_margin", 0.15)
    max_scout_level = leveling_config.get("max_scout_level", 16)
    # Options to evaluate for a scout step (in levels)
    scout_options = leveling_config.get("scout_step_options", [4, 8, 12, 16])

    contenders = _select_contenders(candidates, min_relevant_prob, contested_margin, max_contenders)
    if not contenders:
        return []
    resolved = _slot_is_resolved(contenders, resolved_prob_threshold, contested_margin)

    actions = []
    for i, cand in enumerate(contenders):
        artifact = cand.get("artifact")
        if not artifact:
            continue
        rarity = artifact.get("rarity", 5)
        max_level = MAX_LEVEL.get(rarity, 20)
        current_level = artifact.get("level", 0)

        if current_level >= max_level:
            continue

        if resolved:
            if i != 0:
                continue
            action_type = "Commit"
            target_level = max_level
        else:
            action_type = "Scout"
            # Determine the first level that actually gives new information
            hidden_subs = artifact.get("unactivatedSubstats", [])
            if hidden_subs:
                first_meaningful = current_level + 8   # +4 reveals hidden, +8 is first roll
            else:
                first_meaningful = current_level + 4   # +4 is first roll

            best_target = current_level
            best_priority = -1.0

            for step in scout_options:
                target = min(current_level + step, max_scout_level, max_level)
                if target <= current_level:
                    continue
                # Skip if this step doesn't reach the first meaningful upgrade
                if target < first_meaningful:
                    continue
                cost = get_leveling_cost(rarity, current_level, target)
                mora = max(cost.get("mora", 1), 1)
                # Priority = probability / cost (higher is better)
                priority = cand.get("probability", 0.0) / mora
                if priority > best_priority:
                    best_priority = priority
                    best_target = target

            target_level = best_target
            if target_level <= current_level:
                continue

        actions.append({
            "character": char_name,
            "slot": slot,
            "artifact_id": artifact.get("id"),
            "artifact": artifact,
            "action_type": action_type,
            "slot_status": "Resolved" if resolved else "Contested",
            "probability": cand.get("probability", 0.0),
            "is_equipped": cand.get("is_equipped", False),
            "current_level": current_level,
            "target_level": target_level,
            "rarity": rarity,
            # Precomputed strictly upstream (score.py) from this candidate's
            # own max reachable rolls vs the equipped tier for (char, slot) -
            # see tier_upgrade_ok() at the top of this module. Defaults to
            # True if missing so callers that don't supply it aren't
            # regressed/blocked.
            "tier_upgrade_ok": cand.get("tier_upgrade_ok", True),
            "reachable_tier": cand.get("reachable_tier"),
        })

    return actions


def build_leveling_plan_from_optimizer(
    optimizer_candidates_by_char: Dict[str, Dict[str, List[Dict[str, Any]]]],
    char_score_lookup: Dict[str, float],
    budget_config: Dict[str, Any],
    leveling_config: Dict[str, Any],
    skip_chars: set = None,              # characters excluded by the coverage gate
    max_pieces: int = None,              # hard cap on number of pieces selected (None = no cap)
    exclude_non_upgraders: bool = False, # hard-exclude sidegrades (only relevant if a real upgrade exists elsewhere)
) -> Dict[str, Any]:
    """
    Build a leveling plan from the global optimizer's build-optimality
    probabilities (optimizer_candidates_by_char) instead of the closed-form
    roll-threshold heuristic. See module docstring for the Contested/
    Resolved rationale.

    Selection across the whole roster is still budget-gated the same way as
    the legacy planner (immediate spend capped to a fraction of remaining
    lifetime currency, finish cost checked against lifetime budget, a
    per-character action cap) but the sort key now leads with character
    urgency (character_scoring.score_character's `score`) so more urgent
    characters get first claim on the budget, with the action's own
    cost-efficiency breaking ties within a character.
    """
    max_mora = budget_config.get("max_mora", 2000000)
    max_exp = budget_config.get("max_artifact_exp", 10000000)
    already_spent_mora = budget_config.get("already_spent_mora", 0)
    already_spent_exp = budget_config.get("already_spent_exp", 0)

    remaining_lifetime_mora = max_mora - already_spent_mora
    remaining_lifetime_exp = max_exp - already_spent_exp

    max_reveal_fraction = leveling_config.get("max_reveal_fraction", 0.40)
    max_per_character = leveling_config.get("max_per_character", 2)

    raw_actions = []
    for char_name, slots in optimizer_candidates_by_char.items():
        if skip_chars and char_name in skip_chars:
            continue
        for slot, candidates in slots.items():
            if exclude_non_upgraders:
                # A real tier-upgrading candidate exists somewhere on the
                # roster this run, so sidegrades never even reach contender
                # selection for this slot - not deprioritized, hard dropped.
                # (This can include dropping the currently-equipped piece
                # from contention if leveling it further can't raise its
                # own tier - correct, since there's nothing to gain there
                # while a real upgrade sits unfunded elsewhere.)
                candidates = [c for c in candidates if c.get("tier_upgrade_ok")]
                if not candidates:
                    continue
            raw_actions.extend(plan_slot_actions(char_name, slot, candidates, leveling_config))

    if not raw_actions:
        return {
            "actions": [],
            "summary": {
                "total_immediate_mora": 0,
                "total_immediate_exp": 0,
                "calculated_immediate_budget_used_fraction": 0.0,
                "total_finish_cost_if_all_completed": {"mora": 0, "exp": 0},
                "remaining_lifetime_mora": remaining_lifetime_mora,
                "remaining_lifetime_exp": remaining_lifetime_exp,
                "lifetime_warning": None,
                "recommendation_text": "No artifacts worth leveling right now.",
            },
        }

    for a in raw_actions:
        immediate_cost = get_leveling_cost(a["rarity"], a["current_level"], a["target_level"])
        finish_cost = get_leveling_cost(a["rarity"], a["current_level"], MAX_LEVEL.get(a["rarity"], 20))
        a["immediate_cost"] = immediate_cost
        a["finish_cost"] = finish_cost

        score = char_score_lookup.get(a["character"], 0)
        a["character_score"] = score
        # Normalize the roster's 0-1000ish urgency score into a small
        # multiplier, and weight every action - Scout included - by its own
        # win probability. A 55%-probability contender is a more efficient
        # use of budget than a 10% one even though both are "in contention";
        # the contested_margin filter upstream already keeps hopeless
        # candidates out entirely, this just orders what's left honestly.
        urgency = max(score, 1) / 1000.0
        value = max(a["probability"], 0.01) * urgency
        mora_cost = max(immediate_cost.get("mora", 1), 1)
        a["priority"] = value / mora_cost

    # ---- Group into atomic per-(character, slot) decisions ----
    # A contested slot's contenders are one decision (scout everyone still
    # live for it, or none of them) - group so selection can't split a group
    # apart, and so the lifetime-budget reservation below can be computed
    # per-slot rather than per-action.
    groups = {}
    for a in raw_actions:
        key = (a["character"], a["slot"])
        groups.setdefault(key, []).append(a)

    group_list = []
    for (char_name, slot), acts in groups.items():
        immediate_mora = sum(a["immediate_cost"]["mora"] for a in acts)
        immediate_exp = sum(a["immediate_cost"]["exp"] for a in acts)
        # Realistic lifetime reservation: in a contested (Scout) group, only
        # ONE contender ever actually gets finished - whichever wins once
        # the slot resolves. Reserving the max finish cost in the group (not
        # the sum of every contender's finish cost) is what guarantees "if
        # you commit to what's currently being leveled and it pans out, you
        # can still finish it" without being pointlessly conservative about
        # candidates that will end up abandoned once the slot resolves. A
        # Commit/Legacy group is just the one action's own finish cost.
        reserved_finish_mora = max(a["finish_cost"]["mora"] for a in acts)
        reserved_finish_exp = max(a["finish_cost"]["exp"] for a in acts)
        naive_finish_mora = sum(a["finish_cost"]["mora"] for a in acts)
        naive_finish_exp = sum(a["finish_cost"]["exp"] for a in acts)
        # A group counts as tier-upgrading if ANY of its contenders can still
        # raise the slot's tier - a contested slot shouldn't lose its scout
        # budget just because one of several contenders can't tier up.
        group_tier_upgrade_ok = any(a.get("tier_upgrade_ok", True) for a in acts)
        group_list.append({
            "character": char_name,
            "slot": slot,
            "character_score": acts[0]["character_score"],
            "best_priority": max(a["priority"] for a in acts),
            "tier_upgrade_ok": group_tier_upgrade_ok,
            "actions": acts,
            "immediate_mora": immediate_mora,
            "immediate_exp": immediate_exp,
            "reserved_finish_mora": reserved_finish_mora,
            "reserved_finish_exp": reserved_finish_exp,
            "naive_finish_mora": naive_finish_mora,
            "naive_finish_exp": naive_finish_exp,
        })

    # Tier-upgrading groups (can raise the slot's tier over what's equipped)
    # get first claim on the budget globally, across the whole roster -
    # ahead of character urgency. Only once every tier-upgrading group has
    # been considered does character urgency start deciding among the
    # non-upgraders. Priority (which folds in probability, urgency, and
    # cost) breaks any remaining ties within each tier.
    group_list.sort(key=lambda g: (-g["tier_upgrade_ok"], -g["character_score"], -g["best_priority"]))

    selected = []
    remaining_immediate_mora = max_reveal_fraction * remaining_lifetime_mora
    remaining_immediate_exp = max_reveal_fraction * remaining_lifetime_exp
    # max_per_character caps how many distinct SLOTS one character can claim
    # budget on per run, not how many individual actions.
    per_char_slots = {}
    # Running total of lifetime budget already reserved by selected groups -
    # this is what was missing before: each group's reservation now has to
    # fit what's LEFT after every previously-selected group, not just the
    # full lifetime pool in isolation. This is the hard guarantee: the sum
    # of every selected group's reservation can never exceed your lifetime
    # budget, so committing to what's currently being leveled and following
    # it through to completion is always affordable.
    total_committed_mora = 0
    total_committed_exp = 0
    committed_finish_mora = 0
    committed_finish_exp = 0

    for g in group_list:
        char = g["character"]
        char_slots = per_char_slots.setdefault(char, set())
        if g["slot"] not in char_slots and len(char_slots) >= max_per_character:
            continue
        # Piece cap: a group is one atomic decision (e.g. every live
        # contender in a contested slot gets scouted together), so a group
        # that would push the total over the cap is skipped whole rather
        # than partially split.
        if max_pieces is not None and len(selected) + len(g["actions"]) > max_pieces:
            continue
        if g["immediate_mora"] > remaining_immediate_mora:
            continue
        if g["immediate_exp"] > remaining_immediate_exp:
            continue
        # Check lifetime budget: immediate + reserved must fit
        if total_committed_mora + g["immediate_mora"] + g["reserved_finish_mora"] > remaining_lifetime_mora:
            continue
        if total_committed_exp + g["immediate_exp"] + g["reserved_finish_exp"] > remaining_lifetime_exp:
            continue

        # Tag each action with the GROUP's tier-upgrade status (not its own
        # individual one) so the combined plan's final sort keeps this
        # atomic scouting/commit decision contiguous instead of splitting a
        # contested slot's contenders apart by their individual reachability.
        # The action's own "tier_upgrade_ok" is left untouched for display
        # (e.g. per-row "does this specific candidate upgrade?" labeling).
        for a in g["actions"]:
            a["group_tier_upgrade_ok"] = g["tier_upgrade_ok"]
        selected.extend(g["actions"])
        remaining_immediate_mora -= g["immediate_mora"]
        remaining_immediate_exp -= g["immediate_exp"]
        total_committed_mora += g["immediate_mora"] + g["reserved_finish_mora"]
        total_committed_exp += g["immediate_exp"] + g["reserved_finish_exp"]
        committed_finish_mora += g["reserved_finish_mora"]
        committed_finish_exp += g["reserved_finish_exp"]
        char_slots.add(g["slot"])

    # Absolute final guarantee, independent of every upstream code path:
    # nothing below min_relevant_probability leaves this function. Every
    # other gate (contender selection, tier-upgrade hard-exclude, group
    # atomicity) should already prevent this, but this check doesn't trust
    # that chain to stay correct forever - a below-floor "optimal 0% of the
    # time" candidate is never fundable, full stop, no matter how it ended
    # up in `selected`.
    min_relevant_prob = leveling_config.get("min_relevant_probability", 0.08)
    selected = [a for a in selected if a.get("probability", 0.0) >= min_relevant_prob]

    total_immediate_mora = sum(a["immediate_cost"]["mora"] for a in selected)
    total_immediate_exp = sum(a["immediate_cost"]["exp"] for a in selected)
    # committed_finish_mora/exp is already the sum of every selected group's
    # reservation, i.e. the guaranteed-affordable number - use it directly
    # rather than re-summing raw per-action finish costs (which would
    # double-count contested groups and reintroduce the budget-overrun bug).
    total_finish_mora = committed_finish_mora
    total_finish_exp = committed_finish_exp
    naive_finish_mora = sum(a["finish_cost"]["mora"] for a in selected)
    naive_finish_exp = sum(a["finish_cost"]["exp"] for a in selected)

    scout_count = sum(1 for a in selected if a["action_type"] == "Scout")
    commit_count = sum(1 for a in selected if a["action_type"] == "Commit")
    deferred_tier_count = sum(1 for a in selected if not a.get("tier_upgrade_ok", True))

    # This should be structurally unreachable now (the selection loop above
    # never lets committed_finish exceed the lifetime budget) - kept as a
    # defensive check rather than removed outright, in case future changes
    # to the reservation math introduce an edge case.
    warning = None
    if total_finish_mora > remaining_lifetime_mora:
        warning = (
            f"Finishing all selected pieces would exceed your lifetime budget by "
            f"{total_finish_mora - remaining_lifetime_mora:,.0f} Mora."
        )

    if selected:
        parts = []
        if commit_count:
            parts.append(f"commit {commit_count} resolved piece(s) to max level")
        if scout_count:
            parts.append(f"scout {scout_count} contested piece(s) by +{leveling_config.get('scout_step_levels', 4)} levels")
        rec_text = (
            f"Spend {total_immediate_mora:,.0f} Mora and {total_immediate_exp:,.0f} EXP now to "
            + " and ".join(parts) + ". "
            f"Reserves {total_finish_mora:,.0f} Mora / {total_finish_exp:,.0f} EXP of your remaining "
            f"{remaining_lifetime_mora:,.0f} Mora to finish whichever piece wins each contested slot, "
            f"plus every committed piece. Re-run after leveling to re-check contested slots."
        )
    else:
        rec_text = "No artifacts selected for leveling under current budget and constraints."

    summary = {
        "total_immediate_mora": total_immediate_mora,
        "total_immediate_exp": total_immediate_exp,
        "calculated_immediate_budget_used_fraction": (
            total_immediate_mora / (max_reveal_fraction * remaining_lifetime_mora)
            if remaining_lifetime_mora > 0 else 0
        ),
        # The guaranteed-affordable reservation (max-per-contested-group, not
        # sum) - this is what's checked against the lifetime budget.
        "total_finish_cost_if_all_completed": {"mora": total_finish_mora, "exp": total_finish_exp},
        # FYI only: what it would cost if every scouted contender in every
        # contested slot were ALSO fully maxed at once, which isn't the plan
        # - only whichever piece wins each slot is expected to get finished.
        "total_finish_cost_worst_case": {"mora": naive_finish_mora, "exp": naive_finish_exp},
        "remaining_lifetime_mora": remaining_lifetime_mora,
        "remaining_lifetime_exp": remaining_lifetime_exp,
        "lifetime_warning": warning,
        "recommendation_text": rec_text,
        "scout_count": scout_count,
        "commit_count": commit_count,
        "deferred_tier_count": deferred_tier_count,
    }

    return {
        "actions": selected,
        "summary": summary,
    }


def build_combined_leveling_plan(
    bench_results: List[Dict[str, Any]],
    optimizer_candidates_by_char: Dict[str, Dict[str, List[Dict[str, Any]]]],
    char_score_lookup: Dict[str, float],
    budget_config: Dict[str, Any],
    leveling_config: Dict[str, Any],
    char_slot_tier_lookup: Dict[Any, str] = None,
) -> Dict[str, Any]:
    """
    Primary entry point. Runs the optimizer-driven planner for every
    character the global optimizer produced candidates for, and falls back
    to the legacy closed-form planner (build_leveling_plan) for the rest
    (split-set characters, characters with no equipped pieces, etc.) so
    they aren't silently dropped from the plan. Both actions lists share a
    unified schema and are budget-selected together as one plan.

    General gate: a character is not eligible for ANY leveling action until
    their bench has at least min_distinct_on_set_slots distinct on-set slots
    (Flower/Feather/Sands/Goblet/Circlet) with a piece that can reach Good or
    Excellent (bench verdict != "Dead end"). Counting distinct slots, not raw
    piece count, so three feathers + one goblet doesn't clear the bar - you
    need real coverage before leveling anything for that character is worth
    budget. Optimizer-only characters with no bench coverage at all (no
    eligible on-set pieces) are likewise skipped.
    """
    min_distinct_slots = leveling_config.get("min_distinct_on_set_slots", 4)
    char_slot_tier_lookup = char_slot_tier_lookup or {}

    # On-set slot coverage per character: a slot counts as "covered" only if
    # something REAL clears the Good bar there - not merely a piece whose
    # ceiling (every remaining roll landing useful) could theoretically get
    # there. Farming enough for a set means having pieces actually likely to
    # be good, not pieces that might get lucky. Two ways a slot counts:
    #   1. A bench candidate's expected_rolls (not max_rolls/ceiling) already
    #      meets or beats that slot's `good` threshold.
    #   2. The piece currently EQUIPPED in that slot already has a Good or
    #      Excellent status (character_scoring already found real rolls
    #      there - already-leveled pieces should count towards coverage,
    #      not just bench candidates still on the shelf).
    # Distinct slots, not raw piece count, so 3 feathers + 1 goblet only
    # counts as 2 slots, not 4 pieces.
    covered_slots_by_char = {}
    for b in bench_results:
        expected_rolls = b.get("expected_rolls")
        good = b.get("good")
        if expected_rolls is None or good is None or expected_rolls < good:
            continue
        char = b.get("character")
        if char is None:
            continue
        covered_slots_by_char.setdefault(char, set()).add(b.get("slot"))

    for (char, slot), status in char_slot_tier_lookup.items():
        if status in ("Good", "Excellent"):
            covered_slots_by_char.setdefault(char, set()).add(slot)

    # Characters with no optimizer data OR no bench candidates never appear in
    # either map; they produce no actions anyway. What we need to actively
    # exclude is anyone who WOULD have produced actions but doesn't have the
    # required on-set coverage.
    eligible_by_coverage = {
        char for char, slots in covered_slots_by_char.items()
        if len(slots) >= min_distinct_slots
    }
    optimizer_chars = set(optimizer_candidates_by_char.keys())
    # A character with optimizer candidates but zero eligible bench results
    # has no real on-set build being assembled - hold their leveling too.
    skip_chars = {
        char for char in (optimizer_chars | set(covered_slots_by_char.keys()))
        if char not in eligible_by_coverage
    }
    skipped_count = len(skip_chars)

    # Global hard cap on the total number of pieces (artifacts) leveled in
    # one run, across both planners combined. The optimizer path is the
    # primary/preferred planner (see module docstring), so it gets first
    # claim on the cap; the legacy fallback only fills whatever's left.
    max_pieces = leveling_config.get("max_pieces_per_run", 10)

    # Hard sidegrade exclusion: computed once, ONCE, across the whole
    # roster (both planners, every eligible character) before either
    # planner runs any budget selection. If a real tier-upgrading candidate
    # exists ANYWHERE, no sidegrade is a selectable candidate for ANYONE
    # this run - this is a pure existence check, independent of Mora/EXP
    # budget, per rules.yaml's leveling.require_tier_upgrade. Sidegrades
    # only become eligible again once nothing better exists roster-wide.
    require_tier_upgrade = leveling_config.get("require_tier_upgrade", True)
    exclude_non_upgraders = require_tier_upgrade and any_tier_upgrade_available(
        optimizer_candidates_by_char, bench_results, skip_chars, char_slot_tier_lookup or {},
    )

    optimizer_plan = build_leveling_plan_from_optimizer(
        optimizer_candidates_by_char, char_score_lookup, budget_config, leveling_config,
        skip_chars=skip_chars,
        max_pieces=max_pieces,
        exclude_non_upgraders=exclude_non_upgraders,
    )

    remaining_pieces = None
    if max_pieces is not None:
        remaining_pieces = max(0, max_pieces - len(optimizer_plan["actions"]))

    legacy_bench_results = [
        b for b in bench_results
        if b.get("character") not in optimizer_chars
        and b.get("character") not in skip_chars
    ]
    if legacy_bench_results and (remaining_pieces is None or remaining_pieces > 0):
        legacy_plan = build_leveling_plan(legacy_bench_results, budget_config, leveling_config,
                                          skip_chars=skip_chars,
                                          char_slot_tier_lookup=char_slot_tier_lookup,
                                          max_pieces=remaining_pieces,
                                          exclude_non_upgraders=exclude_non_upgraders)
        for a in legacy_plan["actions"]:
            a["character_score"] = char_score_lookup.get(a["character"], 0)
    else:
        legacy_plan = {"actions": [], "summary": {}}

    combined_actions = optimizer_plan["actions"] + legacy_plan["actions"]
    # Global priority: every tier-upgrading GROUP (a contested slot's whole
    # set of scouted contenders, or a single commit/legacy action) across
    # the whole roster outranks every non-upgrading group, regardless of
    # character urgency. Sorting on group_tier_upgrade_ok - not each row's
    # own individual tier_upgrade_ok - keeps a contested slot's contenders
    # contiguous in the displayed list, matching how they were actually
    # funded as one atomic decision, instead of splitting the group apart
    # because one contender happens to reach a lower tier than another.
    # Character urgency and character name only break ties within a tier.
    combined_actions.sort(
        key=lambda a: (
            -a.get("group_tier_upgrade_ok", a.get("tier_upgrade_ok", True)),
            -a.get("character_score", 0),
            a.get("character", ""),
        )
    )

    opt_summary = optimizer_plan["summary"]
    leg_summary = legacy_plan.get("summary", {}) or {}

    total_immediate_mora = opt_summary.get("total_immediate_mora", 0) + leg_summary.get("total_immediate_mora", 0)
    total_immediate_exp = opt_summary.get("total_immediate_exp", 0) + leg_summary.get("total_immediate_exp", 0)
    total_finish_mora = (
        opt_summary.get("total_finish_cost_if_all_completed", {}).get("mora", 0)
        + leg_summary.get("total_finish_cost_if_all_completed", {}).get("mora", 0)
    )
    total_finish_exp = (
        opt_summary.get("total_finish_cost_if_all_completed", {}).get("exp", 0)
        + leg_summary.get("total_finish_cost_if_all_completed", {}).get("exp", 0)
    )

    warnings = [w for w in (opt_summary.get("lifetime_warning"), leg_summary.get("lifetime_warning")) if w]
    warning = " ".join(warnings) if warnings else None

    if combined_actions:
        rec_text = opt_summary.get("recommendation_text", "")
        if legacy_plan["actions"]:
            rec_text += (
                f" ({len(legacy_plan['actions'])} additional action(s) for characters without "
                f"optimizer data, using the legacy threshold planner.)"
            )
    else:
        rec_text = "No artifacts selected for leveling under current budget and constraints."

    if skipped_count:
        rec_text += (
            f" Skipped {skipped_count} character(s) with fewer than {min_distinct_slots} "
            f"distinct on-set slots of potential Good/Excellent coverage."
        )

    deferred_tier_count = sum(1 for a in combined_actions if not a.get("tier_upgrade_ok", True))
    if deferred_tier_count:
        rec_text += (
            f" ({deferred_tier_count} action(s) deferred to lowest priority - they can't "
            f"raise their slot's tier over what's currently equipped.)"
        )

    if max_pieces is not None and len(combined_actions) >= max_pieces:
        rec_text += f" Capped at {max_pieces} piece(s) for this run."

    summary = {
        "total_immediate_mora": total_immediate_mora,
        "total_immediate_exp": total_immediate_exp,
        "calculated_immediate_budget_used_fraction": opt_summary.get("calculated_immediate_budget_used_fraction", 0.0),
        "total_finish_cost_if_all_completed": {"mora": total_finish_mora, "exp": total_finish_exp},
        "remaining_lifetime_mora": opt_summary.get("remaining_lifetime_mora"),
        "remaining_lifetime_exp": opt_summary.get("remaining_lifetime_exp"),
        "lifetime_warning": warning,
        "recommendation_text": rec_text,
        "scout_count": opt_summary.get("scout_count", 0),
        "commit_count": opt_summary.get("commit_count", 0),
        "legacy_count": len(legacy_plan["actions"]),
        "deferred_tier_count": deferred_tier_count,
        "piece_count": len(combined_actions),
        "max_pieces_per_run": max_pieces,
    }

    return {
        "actions": combined_actions,
        "summary": summary,
    }