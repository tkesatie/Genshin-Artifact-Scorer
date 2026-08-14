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
level per character and by how much. On top of that, `leveling.tier_gated`
(default true) enforces a soft tier order: we first try to fund actions
from tier 1 (Active + Farming); if none can be afforded, we move to tier 2,
etc. Mora is spent completing one tier before moving to the next.

Two multipliers scale how easily a slot resolves to Commit vs. stays on
Scout (see _decide_slot_action's expected_waste):

  - EXPOSURE (_exposure_multiplier): per-character. The fewer of a
    character's 5 on-set slots are already covered by something real, the
    more future domain-farming still lies ahead for them specifically -
    more incidental chances a better candidate turns up. A leader needs to
    be more certain before Commit beats scouting. A hard floor
    (`f_hard_block_remaining_slots`, default: blocked at 1-or-fewer covered
    slots) backs this up for characters too fresh for the smooth multiplier
    alone to be trusted - Scout is never blocked by it, only Commit.
  - SCARCITY (_scarcity_multiplier): roster-wide. The less lifetime Mora is
    left overall, the less it's worth paying to keep scouting - scouting
    only pays off if there's budget left afterward to act on what it
    reveals, and running out entirely with nothing committed is worse than
    settling for today's leader. This scales expected_waste *down* as
    remaining_lifetime_mora shrinks, nudging borderline slots toward Commit
    without ever overriding the absolute-probability floor that blocks
    committing to genuinely bad pieces.

The character-level admission gate itself is a single on-set coverage
check (`compute_coverage_metrics`): a character needs real (not just
theoretical-ceiling) coverage in enough on-set slots before Commit actions
are considered for them at all. Scout actions are exempt from this gate -
they're cheap, information-gathering, and the direct fix for "more farming
ahead = more chances to outclass what I'd commit today."

A legacy closed-form/threshold path (`build_leveling_plan`) is kept
unchanged as a fallback for characters the optimizer didn't run for (split
sets, characters with no equipped pieces, etc.) so they aren't silently
dropped from the plan; it has no Scout/Commit split, so it falls back to
the same coverage gate applied as a hard pass/fail. `build_combined_leveling_plan`
merges both.
"""

from typing import Dict, List, Any, Optional
from math import comb
import os
import datetime

from artifact_utils import MAX_LEVEL, STAT_LABEL, get_leveling_cost

# ---- Debug logging (writes to leveling_debug.log in CWD) ----
DEBUG_FILE = "leveling_debug.log"

def _log(message: str) -> None:
    """Append a timestamped message to the debug log file."""
    try:
        with open(DEBUG_FILE, "a", encoding="utf-8") as f:
            timestamp = datetime.datetime.now().strftime("%Y-%m-%d %H:%M:%S.%f")[:-3]
            f.write(f"[{timestamp}] {message}\n")
    except Exception:
        pass  # Don't let logging failures break the planner

def _log_reset() -> None:
    try:
        with open(DEBUG_FILE, "w", encoding="utf-8") as f:
            f.write(f"=== Leveling planner debug log started at {datetime.datetime.now()} ===\n")
    except Exception:
        pass

# ---- Character priority tier (shared by both planners) ----
def _char_tier(usage: Optional[str], status: Optional[str]) -> int:
    if usage == "Active" and status == "Farming":
        return 1
    elif usage == "IT Only" and status == "Farming":
        return 2
    elif usage == "Active" and status == "Finished":
        return 3
    elif usage == "IT Only" and status == "Finished":
        return 4
    else:
        return 5

def _tier_label(tier: int) -> str:
    return {
        1: "Active + Farming",
        2: "IT Only + Farming",
        3: "Active + Finished",
        4: "IT Only + Finished",
    }.get(tier, "Usable/Luxury")


# ---- Tier-upgrade gate (shared) ----
def reachable_tier_for(max_rolls: Optional[float], good: Optional[float], excellent: Optional[float]) -> Optional[str]:
    if max_rolls is None or good is None or excellent is None:
        return None
    if max_rolls >= excellent:
        return "Excellent"
    elif max_rolls >= good:
        return "Good"
    else:
        return "Needs Work"

def tier_upgrade_ok(equipped_tier: Optional[str], reachable_tier: Optional[str]) -> bool:
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
    min_probability: float = 0.0,
) -> bool:
    skip_chars = skip_chars or set()
    for char_name, slots in optimizer_candidates_by_char.items():
        if char_name in skip_chars:
            continue
        for candidates in slots.values():
            if any(
                c.get("tier_upgrade_ok") and _get_probability(c) >= min_probability
                for c in candidates
            ):
                return True
    for b in bench_results:
        if b.get("character") in skip_chars:
            continue
        if b.get("verdict") == "Dead end":
            continue
        equipped_tier = char_slot_tier_lookup.get((b.get("character"), b.get("slot")))
        reachable = reachable_tier_for(b.get("max_rolls"), b.get("good"), b.get("excellent"))
        prob = (b.get("optimal_probability") or 0.0) / 100.0
        if tier_upgrade_ok(equipped_tier, reachable) and prob >= min_probability:
            return True
    return False


# ---- Binomial tail (exact, fast for n <= 4) ----
def _binom_tail(n: int, p: float, k: int) -> float:
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
    active_subs = artifact.get("substats", [])
    useful_active = sum(
        1 for s in active_subs
        if STAT_LABEL.get(s.get("key")) in useful_stats
    )
    active_count = len(active_subs)
    hidden_gain = 0
    if hidden_subs and total_events > 0:
        hidden_useful = STAT_LABEL.get(hidden_subs[0].get("key")) in useful_stats
        if hidden_useful:
            hidden_gain = 1
            useful_active += 1
        active_count += 1
        total_events -= 1
    if total_events == 0 or active_count == 0:
        return 1.0 if current_rolls + hidden_gain >= threshold else 0.0
    p = useful_active / active_count
    needed = max(0, threshold - (current_rolls + hidden_gain))
    return _binom_tail(total_events, p, needed)


# ---- Safe target level ----
def safe_target_level(
    artifact: Dict[str, Any],
    useful_stats: List[str],
    threshold: int,
    current_rolls: int,
    soft_floor: float = 0.15
) -> int:
    rarity = artifact.get("rarity", 5)
    max_level = MAX_LEVEL.get(rarity, 20)
    current_level = artifact.get("level", 0)
    if current_level >= max_level:
        return max_level
    events_to_max = (max_level - current_level + 3) // 4
    if current_rolls + events_to_max < threshold:
        return current_level
    target = current_level
    for lvl in range(current_level + 4, max_level + 1, 4):
        if posterior_reach_probability(
            artifact, useful_stats, threshold, current_rolls, target_level=lvl
        ) >= soft_floor:
            target = lvl
            break
    return target


# ---- Build the plan (legacy) ----
def build_leveling_plan(
    bench_results: List[Dict[str, Any]],
    budget_config: Dict[str, Any],
    leveling_config: Dict[str, Any],
    roll_values: Dict[str, Any] = None,
    skip_chars: set = None,
    char_slot_tier_lookup: Dict[Any, str] = None,
    max_pieces: int = None,
    exclude_non_upgraders: bool = False,
    char_usage_lookup: Dict[str, str] = None,
    it_only_max_level: Optional[int] = None,
    char_tier_lookup: Dict[str, int] = None,
) -> Dict[str, Any]:
    char_slot_tier_lookup = char_slot_tier_lookup or {}
    char_usage_lookup = char_usage_lookup or {}
    char_tier_lookup = char_tier_lookup or {}
    require_tier_upgrade = leveling_config.get("require_tier_upgrade", True)
    _log(f"LEGACY PLANNER: bench_results count={len(bench_results)}, skip_chars={skip_chars}, "
         f"exclude_non_upgraders={exclude_non_upgraders}")
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

    candidates = []
    for b in bench_results:
        if b.get("verdict") == "Dead end":
            continue
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
        target = safe_target_level(
            artifact,
            useful_stats,
            good_threshold,
            current_rolls,
            soft_floor
        )
        if char_usage_lookup.get(b.get("character")) == "IT Only" and it_only_max_level:
            target = min(target, it_only_max_level)
        if target <= current_level:
            continue
        immediate_cost = get_leveling_cost(rarity, current_level, target)
        finish_target = (
            it_only_max_level
            if char_usage_lookup.get(b.get("character")) == "IT Only"
            else max_level
        )
        finish_cost = get_leveling_cost(rarity, current_level, finish_target)
        p_current = 1.0 if current_rolls >= good_threshold else 0.0
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

        equipped_tier = char_slot_tier_lookup.get((b.get("character"), b.get("slot")))
        reachable_tier = reachable_tier_for(b.get("max_rolls"), good_threshold, b.get("excellent"))
        candidate_tier_upgrade_ok = (
            True if not require_tier_upgrade
            else tier_upgrade_ok(equipped_tier, reachable_tier)
        )
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
            "group_tier_upgrade_ok": candidate_tier_upgrade_ok,
            "action_type": "Legacy",
            "probability": None,
            "character_score": 0,
            "tier": char_tier_lookup.get(b.get("character"), 5),
        })
    _log(f"LEGACY: generated {len(candidates)} candidates after all filters.")

    # Legacy uses hard tier gate (unchanged)
    tier_gated = leveling_config.get("tier_gated", True)
    top_tier = None
    if tier_gated and candidates:
        top_tier = min(c["tier"] for c in candidates)
        candidates = [c for c in candidates if c["tier"] == top_tier]
        _log(f"LEGACY: tier_gated -> keeping only tier {top_tier} candidates ({len(candidates)})")

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

    candidates.sort(key=lambda c: (c["tier"], -c["tier_upgrade_ok"], -c["efficiency_mora"], -c["efficiency_exp"]))
    selected = []
    remaining_immediate_mora = max_reveal_fraction * remaining_lifetime_mora
    remaining_immediate_exp = max_reveal_fraction * remaining_lifetime_exp
    per_char_count = {}
    max_eff = candidates[0]["efficiency_mora"]
    cliff = cliff_ratio * max_eff
    total_committed_mora = 0
    total_committed_exp = 0
    selected_lifetime_finish_sum_mora = 0
    selected_lifetime_finish_sum_exp = 0

    for c in candidates:
        if max_pieces is not None and len(selected) >= max_pieces:
            _log(f"LEGACY: stopped at max_pieces={max_pieces}")
            break
        char = c["character"]
        per_char_count[char] = per_char_count.get(char, 0)
        if per_char_count[char] >= max_per_character:
            _log(f"LEGACY: skip {char} {c['slot']} - already at max_per_character={max_per_character}")
            continue
        if c["immediate_cost"]["mora"] > remaining_immediate_mora:
            _log(f"LEGACY: skip {char} {c['slot']} - immediate Mora {c['immediate_cost']['mora']} > remaining {remaining_immediate_mora}")
            continue
        if c["immediate_cost"]["exp"] > remaining_immediate_exp:
            _log(f"LEGACY: skip {char} {c['slot']} - immediate EXP {c['immediate_cost']['exp']} > remaining {remaining_immediate_exp}")
            continue
        if total_committed_mora + c["immediate_cost"]["mora"] + c["finish_cost"]["mora"] > remaining_lifetime_mora:
            _log(f"LEGACY: skip {char} {c['slot']} - lifetime Mora overrun")
            continue
        if total_committed_exp + c["immediate_cost"]["exp"] + c["finish_cost"]["exp"] > remaining_lifetime_exp:
            _log(f"LEGACY: skip {char} {c['slot']} - lifetime EXP overrun")
            continue
        if c["efficiency_mora"] < cliff:
            _log(f"LEGACY: break at cliff - efficiency {c['efficiency_mora']} < {cliff}")
            break
        selected.append(c)
        remaining_immediate_mora -= c["immediate_cost"]["mora"]
        remaining_immediate_exp -= c["immediate_cost"]["exp"]
        total_committed_mora += c["immediate_cost"]["mora"] + c["finish_cost"]["mora"]
        total_committed_exp += c["immediate_cost"]["exp"] + c["finish_cost"]["exp"]
        selected_lifetime_finish_sum_mora += c["finish_cost"]["mora"]
        selected_lifetime_finish_sum_exp += c["finish_cost"]["exp"]
        per_char_count[char] += 1
        _log(f"LEGACY: selected {char} {c['slot']} (tier_upgrade_ok={c['tier_upgrade_ok']})")

    _log(f"LEGACY: selected {len(selected)} actions total.")

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
        "top_tier": top_tier,
    }


# ---- Helper: robust probability extraction ----
def _get_probability(cand: Dict[str, Any]) -> float:
    """Extract probability from candidate, handling 'probability' or 'optimal_probability' (as percentage)."""
    prob = cand.get("probability")
    if prob is None:
        prob = cand.get("optimal_probability")
    if prob is not None:
        # If it's > 1.0, assume it's a percentage (e.g., 27.0 -> 0.27)
        if prob > 1.0:
            prob /= 100.0
    else:
        prob = 0.0
    return prob

# ---- Optimizer-driven planning (primary path) ----
def _can_ever_reach_good(cand: Dict[str, Any]) -> bool:
    return cand.get("reachable_tier") != "Needs Work"

def _select_contenders(
    candidates: List[Dict[str, Any]],
    min_relevant_prob: float,
    contested_margin: float,
    max_contenders: int,
) -> List[Dict[str, Any]]:
    if not candidates:
        return []
    relevant = [c for c in candidates if _get_probability(c) >= min_relevant_prob]
    # Log probability values for debugging
    if relevant:
        _log(f"  relevant candidates: {[(_get_probability(c), c.get('artifact_id')) for c in relevant]}")
    return relevant[:max_contenders]

def _finish_mora_cost(rarity: int, current_level: int, max_level: int) -> float:
    cost = get_leveling_cost(rarity, current_level, max_level)
    return max(cost.get("mora", 1), 1)

def _cheapest_scout_step(artifact: Dict[str, Any], scout_options: List[int], max_scout_level: int):
    rarity = artifact.get("rarity", 5)
    max_level = MAX_LEVEL.get(rarity, 20)
    current_level = artifact.get("level", 0)
    hidden_subs = artifact.get("unactivatedSubstats", [])
    first_meaningful = current_level + (8 if hidden_subs else 4)
    best = None
    for step in scout_options:
        target = min(current_level + step, max_scout_level, max_level)
        target = (target // 4) * 4
        if target <= current_level or target < first_meaningful:
            continue
        mora = _finish_mora_cost(rarity, current_level, target)
        if best is None or mora < best[1]:
            best = (target, mora)
    return best

def _remaining_hidden_rolls(artifact: Dict[str, Any], max_level: int) -> int:
    current_level = artifact.get("level", 0)
    levels_left = max(max_level - current_level, 0)
    return min(levels_left // 4, 4)

def _average_priced_roll_value(char_roll_values: Optional[Dict[str, float]]) -> float:
    if not char_roll_values:
        return 0.0
    values = list(char_roll_values.values())
    return sum(values) / len(values) if values else 0.0

def _decide_slot_action(
    contenders: List[Dict[str, Any]],
    leveling_config: Dict[str, Any],
    char_roll_values: Optional[Dict[str, float]] = None,
    exposure_multiplier: float = 1.0,
    scarcity_multiplier: float = 1.0,
) -> Dict[str, Any]:
    leader = contenders[0]
    artifact = leader.get("artifact") or {}
    rarity = artifact.get("rarity", 5)
    max_level = MAX_LEVEL.get(rarity, 20)
    current_level = artifact.get("level", 0)
    leader_p = _get_probability(leader)
    runner_p = _get_probability(contenders[1]) if len(contenders) > 1 else 0.0

    close_damage_threshold = leveling_config.get("close_damage_threshold", 0.02)
    avg_roll_value = _average_priced_roll_value(char_roll_values)
    if len(contenders) >= 2 and avg_roll_value > 0:
        leader_expected = leader.get("expected_useful_rolls") or leader.get("expected_rolls", 0.0)
        runner_expected = contenders[1].get("expected_useful_rolls") or contenders[1].get("expected_rolls", 0.0)
        if leader_expected > 0 and runner_expected > 0:
            leader_rv = leader_expected * avg_roll_value
            runner_rv = runner_expected * avg_roll_value
            rel_diff = abs(leader_rv - runner_rv) / max(leader_rv, runner_rv)
            _log(f"  RV diff: leader={leader_rv:.1f}, runner={runner_rv:.1f}, rel={rel_diff:.2%}")
            if rel_diff < close_damage_threshold:
                _log(f"  RV diff {rel_diff:.2%} < {close_damage_threshold:.2%} -> forcing Commit (damage negligible)")
                remaining_finish_mora = _finish_mora_cost(rarity, current_level, max_level)
                remaining_rolls = _remaining_hidden_rolls(artifact, max_level)
                expected_damage_gain = leader_p * remaining_rolls * avg_roll_value
                return {
                    "resolved": True,
                    "expected_waste": 0.0,
                    "scout_cost": None,
                    "expected_damage_gain": expected_damage_gain,
                    "exposure_multiplier": exposure_multiplier,
                    "scarcity_multiplier": scarcity_multiplier,
                }

    fast_min_p = leveling_config.get("fast_path_min_leader_p", 0.70)
    fast_min_gap = leveling_config.get("fast_path_min_gap", 0.20)
    fast_min_abs = leveling_config.get("fast_path_min_abs_prob", 0.50)
    useful_stats = leader.get("useful_stats") or []
    good_threshold = leader.get("good")
    current_rolls = leader.get("current_rolls", 0)
    if good_threshold is not None and useful_stats:
        abs_prob = posterior_reach_probability(
            artifact, useful_stats, good_threshold, current_rolls, target_level=max_level
        )
        _log(f"  abs_prob of reaching Good by {max_level}: {abs_prob:.3f}")
    else:
        # No threshold metadata for this candidate (e.g. callers that don't
        # run threshold computation). It already passed the reachable_tier
        # gate, so leave the absolute probability unconstrained instead of
        # forcing every such slot to Scout.
        abs_prob = 1.0
        _log("  missing good/useful_stats -> abs_prob left unconstrained")

    # Fast-path only applies when there's a real runner-up to beat: for a
    # lone contender "gap" is meaningless and the cost-based expected_waste
    # vs. scout_cost comparison below should decide.
    if (len(contenders) >= 2 and
        leader_p >= fast_min_p and
        (leader_p - runner_p) >= fast_min_gap and
        abs_prob >= fast_min_abs):
        _log(f"  Fast-path Commit: leader_p={leader_p:.3f}, gap={leader_p - runner_p:.3f}, abs_prob={abs_prob:.3f}")
        remaining_finish_mora = _finish_mora_cost(rarity, current_level, max_level)
        remaining_rolls = _remaining_hidden_rolls(artifact, max_level)
        expected_damage_gain = leader_p * remaining_rolls * avg_roll_value
        return {
            "resolved": True,
            "expected_waste": 0.0,
            "scout_cost": None,
            "expected_damage_gain": expected_damage_gain,
            "exposure_multiplier": exposure_multiplier,
            "scarcity_multiplier": scarcity_multiplier,
        }

    min_commit_abs_prob = leveling_config.get("min_commit_absolute_probability", 0.50)
    remaining_finish_mora = _finish_mora_cost(rarity, current_level, max_level)
    expected_waste = remaining_finish_mora * (1.0 - leader_p) * exposure_multiplier * scarcity_multiplier
    scout_options = leveling_config.get("scout_step_options", [4, 8, 12, 16])
    max_scout_level = leveling_config.get("max_scout_level", 16)
    scout_step = _cheapest_scout_step(artifact, scout_options, max_scout_level)

    if abs_prob < min_commit_abs_prob:
        resolved = False
        _log(f"  abs_prob {abs_prob:.3f} < {min_commit_abs_prob} -> forced Scout")
    else:
        if scout_step is not None and expected_waste > scout_step[1]:
            resolved = False
        else:
            resolved = True

    _log(f"  _decide_slot_action: leader_p={leader_p:.3f}, finish_mora={remaining_finish_mora:.0f}, "
         f"exposure_multiplier={exposure_multiplier:.3f}, scarcity_multiplier={scarcity_multiplier:.3f}, "
         f"expected_waste={expected_waste:.0f}, "
         f"scout_cost={scout_step[1] if scout_step else None}, resolved={resolved}")

    avg_roll_value = _average_priced_roll_value(char_roll_values)
    remaining_rolls = _remaining_hidden_rolls(artifact, max_level)
    expected_damage_gain = leader_p * remaining_rolls * avg_roll_value

    return {
        "resolved": resolved,
        "expected_waste": expected_waste,
        "scout_cost": scout_step[1] if scout_step else None,
        "expected_damage_gain": expected_damage_gain,
        "exposure_multiplier": exposure_multiplier,
        "scarcity_multiplier": scarcity_multiplier,
    }

def explain_slot_decision(
    contenders: List[Dict[str, Any]],
    leveling_config: Dict[str, Any],
    char_roll_values: Optional[Dict[str, float]] = None,
) -> str:
    if not contenders:
        return "No contenders cleared min_relevant_probability - no action for this slot."
    decision = _decide_slot_action(contenders, leveling_config, char_roll_values)
    leader = contenders[0]
    artifact = leader.get("artifact") or {}
    p = _get_probability(leader)
    rarity = artifact.get("rarity", 5)
    max_level = MAX_LEVEL.get(rarity, 20)
    current_level = artifact.get("level", 0)
    lines = [
        f"Leader: probability={p * 100:.1f}%, level={current_level}, rarity={rarity}*",
    ]
    if len(contenders) > 1:
        runner_up_p = _get_probability(contenders[1])
        lines.append(f"Runner-up probability: {runner_up_p * 100:.1f}% ({len(contenders)} live contenders total)")
    remaining_finish = _finish_mora_cost(rarity, current_level, max_level)
    lines.append(f"Remaining finish cost (level {current_level} -> {max_level}): {remaining_finish:,.0f} Mora")
    lines.append(
        f"Expected waste = finish_cost x (1 - p) = {remaining_finish:,.0f} x "
        f"{1 - p:.3f} = {decision['expected_waste']:,.0f} Mora"
    )
    if decision["scout_cost"] is not None:
        lines.append(f"Cheapest next scout step costs: {decision['scout_cost']:,.0f} Mora")
        if decision["expected_waste"] > decision["scout_cost"]:
            lines.append(
                f"{decision['expected_waste']:,.0f} > {decision['scout_cost']:,.0f} "
                f"-> still cheaper to learn more than to gamble -> SCOUT"
            )
        else:
            lines.append(
                f"{decision['expected_waste']:,.0f} <= {decision['scout_cost']:,.0f} "
                f"-> the gamble is already cheaper than finding out more -> COMMIT"
            )
    else:
        lines.append("No scout step remains (terminal state) -> nothing cheaper left to try -> COMMIT")
        lines.append(
            f"Estimated remaining damage from finishing: p x remaining hidden rolls x avg priced "
            f"roll value = {decision['expected_damage_gain']:,.0f} (informational only - not yet gating)"
        )
    lines.append(f"VERDICT: {'Commit' if decision['resolved'] else 'Scout'}")
    return "\n".join(lines)

def plan_slot_actions(
    char_name: str,
    slot: str,
    candidates: List[Dict[str, Any]],
    leveling_config: Dict[str, Any],
    char_roll_values: Optional[Dict[str, float]] = None,
    effective_max_level: Optional[int] = None,
    exposure_multiplier: float = 1.0,
    scarcity_multiplier: float = 1.0,
) -> List[Dict[str, Any]]:
    min_relevant_prob = leveling_config.get("min_relevant_probability", 0.08)
    max_contenders = leveling_config.get("max_contenders_per_slot", 3)
    max_scout_level = leveling_config.get("max_scout_level", 16)
    scout_options = leveling_config.get("scout_step_options", [4, 8, 12, 16])

    _log(f"plan_slot_actions: {char_name} {slot} - {len(candidates)} raw candidates")

    contenders = _select_contenders(candidates, min_relevant_prob, 0.0, max_contenders)
    _log(f"  after min_relevant_prob ({min_relevant_prob}) + max_contenders: {len(contenders)} contenders")

    contenders = [c for c in contenders if _can_ever_reach_good(c)]
    _log(f"  after _can_ever_reach_good: {len(contenders)} contenders")

    if not contenders:
        return []

    filtered = []
    for c in contenders:
        artifact = c.get("artifact") or {}
        useful_stats = c.get("useful_stats") or []
        good_threshold = c.get("good")
        if not useful_stats or good_threshold is None:
            # No threshold metadata for this candidate (caller didn't compute
            # thresholds). It already passed the reachable_tier gate above
            # (not "Needs Work"), so keep it instead of dropping everything
            # on missing data.
            _log(f"    {c.get('artifact_id')} no good/useful_stats -> keeping (reachable_tier gate applies)")
            filtered.append(c)
            continue
        current_rolls = c.get("current_rolls", 0)
        rarity = artifact.get("rarity", 5)
        max_level = MAX_LEVEL.get(rarity, 20)
        abs_prob = posterior_reach_probability(
            artifact,
            useful_stats,
            good_threshold,
            current_rolls,
            target_level=max_level
        )
        _log(f"    {c.get('artifact_id')} abs_prob to reach Good by {max_level}: {abs_prob:.3f}")
        if abs_prob > 0.0:
            filtered.append(c)
        else:
            _log("    dropped: cannot reach Good (abs_prob = 0.0)")
    contenders = filtered
    _log(f"  after absolute probability gate (non-zero Good probability): {len(contenders)} contenders")

    if not contenders:
        return []

    # Remove maxed contenders from consideration for leveling actions
    maxed_contenders = [
        c for c in contenders
        if (c.get("artifact") or {}).get("level", 0)
        >= MAX_LEVEL.get((c.get("artifact") or {}).get("rarity", 5), 20)
    ]
    actionable = [c for c in contenders if c not in maxed_contenders]
    if maxed_contenders:
        _log(f"  {char_name} {slot}: {len(maxed_contenders)} maxed contender(s) set aside "
             f"(already finished, not levelable) -> {len(actionable)} actionable left")
    if not actionable:
        _log(f"  {char_name} {slot}: no under-leveled contenders - slot settled by an "
             f"existing finished piece, no leveling action")
        return []

    decision = _decide_slot_action(
        actionable, leveling_config, char_roll_values,
        exposure_multiplier=exposure_multiplier,
        scarcity_multiplier=scarcity_multiplier,
    )
    resolved = decision["resolved"]

    actions = []
    for i, cand in enumerate(actionable):
        artifact = cand.get("artifact")
        if not artifact:
            continue
        rarity = artifact.get("rarity", 5)
        max_level = MAX_LEVEL.get(rarity, 20)
        current_level = artifact.get("level", 0)
        if current_level >= max_level:
            continue
        if effective_max_level is not None and current_level >= effective_max_level:
            _log(f"  {char_name} {slot} -> already at IT cap {effective_max_level}, no action (slot closed)")
            continue

        if resolved:
            if i != 0:
                continue
            action_type = "Commit"
            target_level = min(max_level, effective_max_level) if effective_max_level else max_level
            _log(f"  {char_name} {slot} -> Commit to {target_level} (leader)")
        else:
            action_type = "Scout"
            hidden_subs = artifact.get("unactivatedSubstats", [])
            if hidden_subs:
                first_meaningful = current_level + 8
            else:
                first_meaningful = current_level + 4
            best_target = current_level
            best_priority = -1.0
            for step in scout_options:
                target = min(current_level + step, max_scout_level, max_level)
                if effective_max_level is not None:
                    target = min(target, effective_max_level)
                target = (target // 4) * 4
                if target <= current_level:
                    continue
                if target < first_meaningful:
                    continue
                cost = get_leveling_cost(rarity, current_level, target)
                mora = max(cost.get("mora", 1), 1)
                priority = _get_probability(cand) / mora
                if priority > best_priority:
                    best_priority = priority
                    best_target = target
            target_level = best_target
            if target_level <= current_level:
                _log(f"  {char_name} {slot} -> Scout: no meaningful target found")
                continue
            _log(f"  {char_name} {slot} -> Scout to {target_level} (contested)")

        if target_level <= current_level:
            _log(f"  {char_name} {slot} -> no-op guard: target {target_level} <= current {current_level}, dropped")
            continue

        actions.append({
            "character": char_name,
            "slot": slot,
            "artifact_id": artifact.get("id"),
            "artifact": artifact,
            "action_type": action_type,
            "slot_status": "Resolved" if resolved else "Contested",
            "probability": _get_probability(cand),  # store normalized probability
            "is_equipped": cand.get("is_equipped", False),
            "current_level": current_level,
            "target_level": target_level,
            "rarity": rarity,
            "tier_upgrade_ok": cand.get("tier_upgrade_ok", True),
            "reachable_tier": cand.get("reachable_tier"),
            "expected_waste_mora": decision["expected_waste"],
            "scout_cost_mora": decision["scout_cost"],
            "expected_damage_gain": decision["expected_damage_gain"],
        })

    return actions


# ---- Character-level admission gate ----
def compute_coverage_metrics(
    bench_results: List[Dict[str, Any]],
    char_slot_tier_lookup: Dict[Any, str],
) -> Dict[str, Dict[str, set]]:
    frac_by_char: Dict[str, Dict[str, float]] = {}
    for b in bench_results:
        expected_rolls = b.get("expected_rolls")
        good = b.get("good")
        char = b.get("character")
        slot = b.get("slot")
        if char is None or slot is None or good is None or good <= 0 or expected_rolls is None:
            continue
        frac = min(expected_rolls / good, 1.0)
        slots = frac_by_char.setdefault(char, {})
        if frac > slots.get(slot, 0.0):
            slots[slot] = frac

    # Only mark a slot as fully covered if the equipped piece is at max level
    # (we can't easily get level here, so we rely on bench_results for equipped pieces)
    # We'll pass level info via a separate mechanism, but for now we keep original behavior
    # as we will handle the level-awareness inside build_leveling_plan_from_optimizer.
    for (char, slot), status in (char_slot_tier_lookup or {}).items():
        if status in ("Good", "Excellent"):
            slots = frac_by_char.setdefault(char, {})
            slots[slot] = 1.0

    full: Dict[str, set] = {}
    for char, slots in frac_by_char.items():
        full[char] = {s for s, f in slots.items() if f >= 1.0}
    return {"full": full}


def _exposure_multiplier(
    char_name: str,
    covered_slots_by_char: Dict[str, set],
    leveling_config: Dict[str, Any],
    total_slots: int = 5,
) -> float:
    exposure_k = leveling_config.get("exposure_k", 0.15)
    covered = len(covered_slots_by_char.get(char_name, set()))
    remaining = max(0, total_slots - covered)
    return 1.0 + exposure_k * remaining

def _scarcity_multiplier(
    remaining_lifetime_mora: float,
    max_mora_total: float,
    leveling_config: Dict[str, Any],
) -> float:
    if max_mora_total <= 0:
        return 1.0
    frac_remaining = max(0.0, min(1.0, remaining_lifetime_mora / max_mora_total))
    scarcity_floor = leveling_config.get("scarcity_floor", 0.5)
    return scarcity_floor + (1.0 - scarcity_floor) * frac_remaining


def build_leveling_plan_from_optimizer(
    optimizer_candidates_by_char: Dict[str, Dict[str, List[Dict[str, Any]]]],
    char_score_lookup: Dict[str, float],
    budget_config: Dict[str, Any],
    leveling_config: Dict[str, Any],
    skip_chars: set = None,
    max_pieces: int = None,
    exclude_non_upgraders: bool = False,
    roll_value_by_char: Dict[str, Dict[str, float]] = None,
    char_usage_lookup: Dict[str, str] = None,
    it_only_max_level: Optional[int] = None,
    covered_slots_by_char: Dict[str, set] = None,
    commit_blocked_chars: set = None,
    char_tier_lookup: Dict[str, int] = None,
) -> Dict[str, Any]:
    commit_priority_multiplier = leveling_config.get("commit_priority_multiplier", 1.0)
    max_mora = budget_config.get("max_mora", 2000000)
    max_exp = budget_config.get("max_artifact_exp", 10000000)
    already_spent_mora = budget_config.get("already_spent_mora", 0)
    already_spent_exp = budget_config.get("already_spent_exp", 0)
    remaining_lifetime_mora = max_mora - already_spent_mora
    remaining_lifetime_exp = max_exp - already_spent_exp

    max_per_character = leveling_config.get("max_per_character", 2)
    char_usage_lookup = char_usage_lookup or {}
    char_tier_lookup = char_tier_lookup or {}

    scarcity_mult = _scarcity_multiplier(remaining_lifetime_mora, max_mora, leveling_config)

    _log(f"OPTIMIZER PLANNER: remaining_lifetime_mora={remaining_lifetime_mora}, max_pieces={max_pieces}, "
         f"exclude_non_upgraders={exclude_non_upgraders}, skip_chars={skip_chars}, "
         f"scarcity_multiplier={scarcity_mult:.3f}")

    raw_actions = []
    for char_name, slots in optimizer_candidates_by_char.items():
        if skip_chars and char_name in skip_chars:
            _log(f"  skip char {char_name} (covered by skip_chars)")
            continue
        effective_max_level = (
            it_only_max_level
            if char_usage_lookup.get(char_name) == "IT Only"
            else None
        )
        for slot, candidates in slots.items():
            # ---- FIX: Adjust tier_upgrade_ok based on equipped piece level ----
            equipped_cand = next((c for c in candidates if c.get("is_equipped")), None)
            equipped_level = None
            if equipped_cand:
                equipped_level = (equipped_cand.get("artifact") or {}).get("level", 0)
                equipped_rarity = (equipped_cand.get("artifact") or {}).get("rarity", 5)
                equipped_max = MAX_LEVEL.get(equipped_rarity, 20)
                if equipped_level is not None and equipped_level < equipped_max:
                    effective_equipped_tier = "Needs Work"
                else:
                    effective_equipped_tier = None
            else:
                effective_equipped_tier = None

            adjusted_candidates = []
            for c in candidates:
                if equipped_cand and effective_equipped_tier == "Needs Work" and not c.get("is_equipped"):
                    reachable = c.get("reachable_tier")
                    if reachable is None:
                        good = c.get("good")
                        excellent = c.get("excellent")
                        max_rolls = c.get("max_rolls")
                        reachable = reachable_tier_for(max_rolls, good, excellent)
                    new_ok = tier_upgrade_ok("Needs Work", reachable)
                    c = c.copy()
                    c["tier_upgrade_ok"] = new_ok
                adjusted_candidates.append(c)
            candidates = adjusted_candidates
            # ---- End fix ----

            if exclude_non_upgraders:
                before = len(candidates)
                candidates = [c for c in candidates if c.get("tier_upgrade_ok")]
                if not candidates:
                    _log(f"  {char_name} {slot}: all {before} candidates dropped by tier_upgrade_ok (exclude_non_upgraders)")
                    continue
                _log(f"  {char_name} {slot}: kept {len(candidates)}/{before} tier_upgrade_ok=True")
            exposure_mult = _exposure_multiplier(char_name, covered_slots_by_char or {}, leveling_config)
            char_tier = char_tier_lookup.get(char_name, 5)
            for a in plan_slot_actions(
                char_name, slot, candidates, leveling_config,
                char_roll_values=(roll_value_by_char or {}).get(char_name),
                effective_max_level=effective_max_level,
                exposure_multiplier=exposure_mult,
                scarcity_multiplier=scarcity_mult,
            ):
                a["tier"] = char_tier
                raw_actions.append(a)

    if commit_blocked_chars:
        before = len(raw_actions)
        raw_actions = [
            a for a in raw_actions
            if not (a["action_type"] == "Commit" and a["character"] in commit_blocked_chars)
        ]
        _log(f"  dropped {before - len(raw_actions)} blocked Commit action(s) for "
             f"{len(commit_blocked_chars)} under-covered character(s)")

    if not raw_actions:
        _log("OPTIMIZER: no raw_actions produced")
        return {
            "actions": [],
            "summary": {
                "total_immediate_mora": 0,
                "total_immediate_exp": 0,
                "total_finish_cost_if_all_completed": {"mora": 0, "exp": 0},
                "remaining_lifetime_mora": remaining_lifetime_mora,
                "remaining_lifetime_exp": remaining_lifetime_exp,
                "lifetime_warning": None,
                "recommendation_text": "No artifacts worth leveling right now.",
            },
            "top_tier": None,
        }

    # ---- Compute costs and priority for every raw action ----
    for a in raw_actions:
        immediate_cost = get_leveling_cost(a["rarity"], a["current_level"], a["target_level"])
        finish_target = (
            it_only_max_level
            if it_only_max_level is not None and char_usage_lookup.get(a["character"]) == "IT Only"
            else MAX_LEVEL.get(a["rarity"], 20)
        )
        finish_cost = get_leveling_cost(a["rarity"], a["current_level"], finish_target)
        a["immediate_cost"] = immediate_cost
        a["finish_cost"] = finish_cost

        score = char_score_lookup.get(a["character"], 0)
        a["character_score"] = score
        urgency = max(score, 1) / 1000.0
        value = max(a["probability"], 0.01) * urgency
        mora_cost = max(immediate_cost.get("mora", 1), 1)
        multiplier = commit_priority_multiplier if a["action_type"] == "Commit" else 1.0
        a["priority"] = (value / mora_cost) * multiplier

        _log(f"  action: {a['character']} {a['slot']} type={a['action_type']} prob={a['probability']:.3f} "
             f"score={score} urgency={urgency:.3f} priority={a['priority']:.4f} "
             f"immediate_mora={immediate_cost.get('mora',0)}")

    # ---- Soft tier gating: process tiers sequentially ----
    actions_by_tier: Dict[int, List[Dict]] = {}
    for a in raw_actions:
        tier = a.get("tier", 5)
        actions_by_tier.setdefault(tier, []).append(a)

    sorted_tiers = sorted(actions_by_tier.keys())

    def select_from_actions(actions: List[Dict], remaining_budget_mora: float, remaining_budget_exp: float, max_pieces_limit: int):
        groups = {}
        for a in actions:
            key = (a["character"], a["slot"])
            groups.setdefault(key, []).append(a)

        group_list = []
        for (char_name, slot), acts in groups.items():
            immediate_mora = sum(a["immediate_cost"]["mora"] for a in acts)
            immediate_exp = sum(a["immediate_cost"]["exp"] for a in acts)
            is_commit_group = all(a["action_type"] == "Commit" for a in acts)
            if is_commit_group:
                reserved_finish_mora = 0
                reserved_finish_exp = 0
            else:
                reserved_finish_mora = max(a["finish_cost"]["mora"] for a in acts)
                reserved_finish_exp = max(a["finish_cost"]["exp"] for a in acts)
            group_tier_upgrade_ok = any(a.get("tier_upgrade_ok", True) for a in acts)
            group_list.append({
                "character": char_name,
                "slot": slot,
                "tier": acts[0].get("tier", 5),
                "character_score": acts[0]["character_score"],
                "best_priority": max(a["priority"] for a in acts),
                "tier_upgrade_ok": group_tier_upgrade_ok,
                "actions": acts,
                "immediate_mora": immediate_mora,
                "immediate_exp": immediate_exp,
                "reserved_finish_mora": reserved_finish_mora,
                "reserved_finish_exp": reserved_finish_exp,
            })

        group_list.sort(key=lambda g: (g["tier"], -g["tier_upgrade_ok"], -g["character_score"], -g["best_priority"]))

        selected = []
        per_char_slots = {}
        total_committed_mora = 0
        total_committed_exp = 0
        committed_finish_mora = 0
        committed_finish_exp = 0

        for g in group_list:
            char = g["character"]
            char_slots = per_char_slots.setdefault(char, set())
            if g["slot"] not in char_slots and len(char_slots) >= max_per_character:
                _log(f"  skip group {char} {g['slot']}: already at max_per_character={max_per_character} (slots={char_slots})")
                continue
            if max_pieces_limit is not None and len(selected) + len(g["actions"]) > max_pieces_limit:
                _log(f"  skip group {char} {g['slot']}: would exceed max_pieces={max_pieces_limit} (current {len(selected)} + {len(g['actions'])})")
                continue
            if total_committed_mora + g["immediate_mora"] + g["reserved_finish_mora"] > remaining_budget_mora:
                _log(f"  skip group {char} {g['slot']}: lifetime Mora overrun (committed {total_committed_mora} + immediate {g['immediate_mora']} + reserve {g['reserved_finish_mora']} > {remaining_budget_mora})")
                continue
            if total_committed_exp + g["immediate_exp"] + g["reserved_finish_exp"] > remaining_budget_exp:
                _log(f"  skip group {char} {g['slot']}: lifetime EXP overrun")
                continue

            for a in g["actions"]:
                a["group_tier_upgrade_ok"] = g["tier_upgrade_ok"]
            selected.extend(g["actions"])
            total_committed_mora += g["immediate_mora"] + g["reserved_finish_mora"]
            total_committed_exp += g["immediate_exp"] + g["reserved_finish_exp"]
            committed_finish_mora += g["reserved_finish_mora"]
            committed_finish_exp += g["reserved_finish_exp"]
            char_slots.add(g["slot"])
            _log(f"  SELECTED group {char} {g['slot']}: immediate {g['immediate_mora']} Mora, reserve {g['reserved_finish_mora']} Mora")

        min_relevant_prob = leveling_config.get("min_relevant_probability", 0.08)
        selected = [a for a in selected if _get_probability(a) >= min_relevant_prob]

        return selected, total_committed_mora, total_committed_exp, committed_finish_mora, committed_finish_exp

    final_selected = []
    final_committed_mora = 0
    final_committed_exp = 0
    final_finish_mora = 0
    final_finish_exp = 0
    selected_tier = None

    tier_gated = leveling_config.get("tier_gated", True)

    if tier_gated:
        for tier in sorted_tiers:
            tier_actions = actions_by_tier[tier]
            _log(f"OPTIMIZER: attempting tier {tier} with {len(tier_actions)} actions")
            selected, committed_mora, committed_exp, finish_mora, finish_exp = select_from_actions(
                tier_actions,
                remaining_lifetime_mora,
                remaining_lifetime_exp,
                max_pieces
            )
            if selected:
                final_selected = selected
                final_committed_mora = committed_mora
                final_committed_exp = committed_exp
                final_finish_mora = finish_mora
                final_finish_exp = finish_exp
                selected_tier = tier
                _log(f"OPTIMIZER: selected {len(selected)} actions from tier {tier}, stopping.")
                break
            else:
                _log(f"OPTIMIZER: no affordable actions in tier {tier}, moving to next tier")
    else:
        # Gate off: one combined budget pass across every tier.
        # select_from_actions still sorts groups by tier, so higher tiers are
        # funded first, but nothing is dropped just for being a lower tier.
        all_actions = [a for tier in sorted_tiers for a in actions_by_tier[tier]]
        _log(f"OPTIMIZER: tier_gated=False -> single budget pass over {len(all_actions)} actions")
        selected, committed_mora, committed_exp, finish_mora, finish_exp = select_from_actions(
            all_actions,
            remaining_lifetime_mora,
            remaining_lifetime_exp,
            max_pieces
        )
        final_selected = selected
        final_committed_mora = committed_mora
        final_committed_exp = committed_exp
        final_finish_mora = finish_mora
        final_finish_exp = finish_exp
        selected_tier = min(a["tier"] for a in selected) if selected else None

    if not final_selected:
        return {
            "actions": [],
            "summary": {
                "total_immediate_mora": 0,
                "total_immediate_exp": 0,
                "total_finish_cost_if_all_completed": {"mora": 0, "exp": 0},
                "remaining_lifetime_mora": remaining_lifetime_mora,
                "remaining_lifetime_exp": remaining_lifetime_exp,
                "lifetime_warning": None,
                "recommendation_text": "No artifacts selected for leveling under current budget and constraints.",
            },
            "top_tier": None,
        }

    total_immediate_mora = sum(a["immediate_cost"]["mora"] for a in final_selected)
    total_immediate_exp = sum(a["immediate_cost"]["exp"] for a in final_selected)
    total_finish_mora = final_finish_mora
    total_finish_exp = final_finish_exp

    scout_count = sum(1 for a in final_selected if a["action_type"] == "Scout")
    commit_count = sum(1 for a in final_selected if a["action_type"] == "Commit")
    deferred_tier_count = sum(1 for a in final_selected if not a.get("tier_upgrade_ok", True))

    warning = None
    if total_finish_mora > remaining_lifetime_mora:
        warning = (
            f"Finishing all selected pieces would exceed your lifetime budget by "
            f"{total_finish_mora - remaining_lifetime_mora:,.0f} Mora."
        )

    if final_selected:
        parts = []
        if commit_count:
            parts.append(f"commit {commit_count} resolved piece(s) to max level")
        if scout_count:
            scout_steps = sorted({
                a["target_level"] - a["current_level"]
                for a in final_selected if a["action_type"] == "Scout"
            })
            if len(scout_steps) == 1:
                step_desc = f"+{scout_steps[0]} levels"
            else:
                step_desc = f"+{scout_steps[0]}-{scout_steps[-1]} levels"
            parts.append(f"scout {scout_count} contested piece(s) by {step_desc}")
        rec_text = (
            f"Spend {total_immediate_mora:,.0f} Mora and {total_immediate_exp:,.0f} EXP now to "
            + " and ".join(parts) + ". "
            f"Reserves {total_finish_mora:,.0f} Mora / {total_finish_exp:,.0f} EXP of your remaining "
            f"{remaining_lifetime_mora:,.0f} Mora to finish whichever piece wins each contested slot, "
            f"plus every committed piece. Re-run after leveling to re-check contested slots."
        )
        if tier_gated and selected_tier is not None:
            rec_text += f" (Limited to tier-{selected_tier} actions; lower tiers deferred.)"
    else:
        rec_text = "No artifacts selected for leveling under current budget and constraints."

    summary = {
        "total_immediate_mora": total_immediate_mora,
        "total_immediate_exp": total_immediate_exp,
        "total_finish_cost_if_all_completed": {"mora": total_finish_mora, "exp": total_finish_exp},
        "remaining_lifetime_mora": remaining_lifetime_mora,
        "remaining_lifetime_exp": remaining_lifetime_exp,
        "lifetime_warning": warning,
        "recommendation_text": rec_text,
        "scout_count": scout_count,
        "commit_count": commit_count,
        "deferred_tier_count": deferred_tier_count,
    }

    return {
        "actions": final_selected,
        "summary": summary,
        "top_tier": selected_tier,
    }

def build_combined_leveling_plan(
    bench_results: List[Dict[str, Any]],
    optimizer_candidates_by_char: Dict[str, Dict[str, List[Dict[str, Any]]]],
    char_score_lookup: Dict[str, float],
    budget_config: Dict[str, Any],
    leveling_config: Dict[str, Any],
    char_slot_tier_lookup: Dict[Any, str] = None,
    roll_value_by_char: Dict[str, Dict[str, float]] = None,
    char_usage_lookup: Dict[str, str] = None,
    char_status_lookup: Dict[str, str] = None,
) -> Dict[str, Any]:
    _log_reset()
    _log("=== build_combined_leveling_plan START ===")
    _log(f"budget_config: {budget_config}")
    _log(f"leveling_config keys: {list(leveling_config.keys())}")

    min_distinct_slots = leveling_config.get("min_distinct_on_set_slots", 4)
    f_hard_block_remaining_slots = leveling_config.get("f_hard_block_remaining_slots", 4)
    char_slot_tier_lookup = char_slot_tier_lookup or {}
    char_usage_lookup = char_usage_lookup or {}
    char_status_lookup = char_status_lookup or {}
    it_only_max_level = leveling_config.get("it_only_max_level")
    active_chars_only = leveling_config.get("active_chars_only", False)
    tier_gated = leveling_config.get("tier_gated", True)

    char_tier_lookup = {
        name: _char_tier(char_usage_lookup.get(name), char_status_lookup.get(name))
        for name in (set(char_usage_lookup) | set(char_status_lookup))
    }

    if active_chars_only:
        _log("active_chars_only=True: filtering IT Only chars out")
        optimizer_candidates_by_char = {
            char: slots for char, slots in optimizer_candidates_by_char.items()
            if char_usage_lookup.get(char) != "IT Only"
        }
        bench_results = [
            b for b in bench_results
            if char_usage_lookup.get(b.get("character")) != "IT Only"
        ]

    covered_slots_by_char = compute_coverage_metrics(bench_results, char_slot_tier_lookup)["full"]

    optimizer_chars = set(optimizer_candidates_by_char.keys())
    all_bench_chars = set(covered_slots_by_char.keys())

    def _not_eligible(eligible_set: set) -> set:
        return {char for char in (optimizer_chars | all_bench_chars) if char not in eligible_set}

    eligible_full = {c for c, slots in covered_slots_by_char.items() if len(slots) >= min_distinct_slots}
    not_eligible_full = _not_eligible(eligible_full)

    skip_chars_optimizer = set()
    skip_chars_legacy = not_eligible_full
    remaining_by_char = {
        c: max(0, 5 - len(covered_slots_by_char.get(c, set())))
        for c in (optimizer_chars | all_bench_chars)
    }
    commit_blocked_chars = {
        c for c, remaining in remaining_by_char.items()
        if remaining >= f_hard_block_remaining_slots
    }

    skipped_count = len(skip_chars_optimizer | skip_chars_legacy)

    _log(f"covered_slots_by_char (full): { {c: list(s) for c, s in covered_slots_by_char.items()} }")
    _log(f"skip_chars_legacy: {skip_chars_legacy}")
    _log(f"commit_blocked_chars: {commit_blocked_chars}")

    max_pieces = leveling_config.get("max_pieces_per_run", 10)

    require_tier_upgrade = leveling_config.get("require_tier_upgrade", True)
    min_prob_for_upgrade = leveling_config.get("min_relevant_probability", 0.25)
    upgrade_exists = any_tier_upgrade_available(
        optimizer_candidates_by_char, bench_results, skip_chars_optimizer, char_slot_tier_lookup or {},
        min_probability=min_prob_for_upgrade,
    )
    exclude_non_upgraders = require_tier_upgrade and upgrade_exists
    _log(f"require_tier_upgrade={require_tier_upgrade}, min_prob_for_upgrade={min_prob_for_upgrade}")
    _log(f"any_tier_upgrade_available={upgrade_exists} => exclude_non_upgraders={exclude_non_upgraders}")

    # No fixed_immediate_budget anymore; we pass None to the optimizer planner
    optimizer_plan = build_leveling_plan_from_optimizer(
        optimizer_candidates_by_char, char_score_lookup, budget_config, leveling_config,
        skip_chars=skip_chars_optimizer,
        max_pieces=max_pieces,
        exclude_non_upgraders=exclude_non_upgraders,
        roll_value_by_char=roll_value_by_char,
        char_usage_lookup=char_usage_lookup,
        it_only_max_level=it_only_max_level,
        covered_slots_by_char=covered_slots_by_char,
        commit_blocked_chars=commit_blocked_chars,
        char_tier_lookup=char_tier_lookup,
    )
    _log(f"optimizer_plan actions: {len(optimizer_plan['actions'])}")

    remaining_pieces = None
    if max_pieces is not None:
        remaining_pieces = max(0, max_pieces - len(optimizer_plan["actions"]))

    legacy_bench_results = [
        b for b in bench_results
        if b.get("character") not in optimizer_chars
        and b.get("character") not in skip_chars_legacy
    ]
    _log(f"legacy_bench_results count (after filtering optimizer_chars + skip_chars_legacy): {len(legacy_bench_results)}")
    if legacy_bench_results and (remaining_pieces is None or remaining_pieces > 0):
        legacy_plan = build_leveling_plan(legacy_bench_results, budget_config, leveling_config,
                                          skip_chars=skip_chars_legacy,
                                          char_slot_tier_lookup=char_slot_tier_lookup,
                                          max_pieces=remaining_pieces,
                                          exclude_non_upgraders=exclude_non_upgraders,
                                          char_usage_lookup=char_usage_lookup,
                                          it_only_max_level=it_only_max_level,
                                          char_tier_lookup=char_tier_lookup)
        for a in legacy_plan["actions"]:
            a["character_score"] = char_score_lookup.get(a["character"], 0)
        _log(f"legacy_plan actions: {len(legacy_plan['actions'])}")
    else:
        legacy_plan = {"actions": [], "summary": {}}
        _log("legacy_plan skipped (no bench results or remaining_pieces=0)")

    combined_actions = optimizer_plan["actions"] + legacy_plan["actions"]

    # If tier_gated is True, we filter combined actions to the highest tier present
    # (but the optimizer planner already did soft tier gating, so this is mainly for legacy)
    top_tier = None
    if tier_gated and combined_actions:
        top_tier = min(a.get("tier", 5) for a in combined_actions)
        combined_actions = [a for a in combined_actions if a.get("tier", 5) == top_tier]
        _log(f"COMBINED: tier_gated -> global top tier {top_tier}, {len(combined_actions)} action(s) kept")

    combined_actions.sort(
        key=lambda a: (
            a.get("tier", 5),
            -a.get("group_tier_upgrade_ok", a.get("tier_upgrade_ok", True)),
            -a.get("character_score", 0),
            a.get("character", ""),
        )
    )

    opt_summary = optimizer_plan["summary"]
    leg_summary = legacy_plan.get("summary", {}) or {}

    opt_kept = any(a.get("action_type") != "Legacy" for a in combined_actions)
    leg_kept = any(a.get("action_type") == "Legacy" for a in combined_actions)

    total_immediate_mora = (opt_summary.get("total_immediate_mora", 0) if opt_kept else 0) + (leg_summary.get("total_immediate_mora", 0) if leg_kept else 0)
    total_immediate_exp = (opt_summary.get("total_immediate_exp", 0) if opt_kept else 0) + (leg_summary.get("total_immediate_exp", 0) if leg_kept else 0)
    total_finish_mora = (
        (opt_summary.get("total_finish_cost_if_all_completed", {}).get("mora", 0) if opt_kept else 0)
        + (leg_summary.get("total_finish_cost_if_all_completed", {}).get("mora", 0) if leg_kept else 0)
    )
    total_finish_exp = (
        (opt_summary.get("total_finish_cost_if_all_completed", {}).get("exp", 0) if opt_kept else 0)
        + (leg_summary.get("total_finish_cost_if_all_completed", {}).get("exp", 0) if leg_kept else 0)
    )

    warnings = [w for w in (opt_summary.get("lifetime_warning"), leg_summary.get("lifetime_warning")) if w]
    warning = " ".join(warnings) if warnings else None

    if combined_actions:
        rec_text = opt_summary.get("recommendation_text", "")
        kept_legacy_count = sum(1 for a in combined_actions if a.get("action_type") == "Legacy")
        if kept_legacy_count:
            rec_text += (
                f" ({kept_legacy_count} additional action(s) for characters without "
                f"optimizer data, using the legacy threshold planner.)"
            )
    else:
        rec_text = "No artifacts selected for leveling under current budget and constraints."

    if tier_gated and top_tier is not None:
        rec_text += (
            f" Limited to tier-{top_tier} ({_tier_label(top_tier)}) characters; "
            f"lower-tier suggestions are deferred until these have no more scouting/commits left."
        )

    commit_blocked_count = len(commit_blocked_chars)
    if commit_blocked_count:
        rec_text += (
            f" {commit_blocked_count} character(s) restricted to Scout-only actions "
            f"pending more on-set coverage."
        )
    if skipped_count:
        rec_text += (
            f" {skipped_count} character(s) without optimizer data skipped by the legacy "
            f"planner's coverage gate."
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
        "total_finish_cost_if_all_completed": {"mora": total_finish_mora, "exp": total_finish_exp},
        "remaining_lifetime_mora": opt_summary.get("remaining_lifetime_mora"),
        "remaining_lifetime_exp": opt_summary.get("remaining_lifetime_exp"),
        "lifetime_warning": warning,
        "recommendation_text": rec_text,
        "scout_count": opt_summary.get("scout_count", 0),
        "commit_count": opt_summary.get("commit_count", 0),
        "legacy_count": sum(1 for a in combined_actions if a.get("action_type") == "Legacy"),
        "deferred_tier_count": deferred_tier_count,
        "piece_count": len(combined_actions),
        "max_pieces_per_run": max_pieces,
        "skipped_count": skipped_count,
        "commit_blocked_count": commit_blocked_count,
        "tier_gated": tier_gated,
        "top_tier": top_tier,
    }

    _log("=== build_combined_leveling_plan END ===")
    _log(f"final summary: piece_count={summary['piece_count']}, scout={summary['scout_count']}, commit={summary['commit_count']}")

    return {
        "actions": combined_actions,
        "summary": summary,
    }