"""
Unit tests for the leveling-plan decision logic in leveling_efficiency.py:
  - the explore-vs-exploit Scout/Commit rule (_decide_slot_action)
  - the probability floor (_select_contenders)
  - the tier-upgrade gate (tier_upgrade_ok / reachable_tier_for)
  - the roster-wide hard-exclude check (any_tier_upgrade_available)

These don't need score.py/bench.py/optimizer.py running - they exercise the
decision functions directly with small, hand-computable inputs, so every
assertion here is something you can verify with a calculator, not just
"trust the code." The Mora cost curve is replaced with a simple linear fake
(10,000 Mora/level) for the duration of these tests, via the
patch_cost_curve fixture below, specifically so the expected numbers in each
test are round and easy to check by hand rather than depending on
artifact_utils' real (more complex) cost table.

Run with: pytest test_leveling_decision.py -v
"""
import pytest
import sys
from pathlib import Path
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).parent.parent))

import leveling_efficiency as le


# ---------------------------------------------------------------------------
# Helpers: build minimal fake "candidate"/"artifact" dicts, the same shape
# score.py's optimizer_candidates_by_char produces.
# ---------------------------------------------------------------------------

def make_artifact(level=0, rarity=5, hidden=True):
    return {
        "id": "test-art",
        "level": level,
        "rarity": rarity,
        "unactivatedSubstats": ["critDMG_"] if hidden else [],
    }


def make_candidate(probability, level=0, rarity=5, hidden=True, tier_upgrade_ok=True, reachable_tier=None):
    return {
        "artifact": make_artifact(level=level, rarity=rarity, hidden=hidden),
        "probability": probability,
        "is_equipped": False,
        "tier_upgrade_ok": tier_upgrade_ok,
        "reachable_tier": (
            reachable_tier
            if reachable_tier is not None
            else ("Excellent" if tier_upgrade_ok else "Good")
        ),
    }


def fake_get_leveling_cost(rarity, current_level, target_level):
    """Linear stand-in for the real cost curve: 10,000 Mora/level, so every
    expected value below is (levels_climbed) x 10,000, easy to verify."""
    levels = target_level - current_level
    return {"mora": levels * 10_000, "exp": levels * 5_000}


FAKE_MAX_LEVEL = {5: 20, 4: 16}


@pytest.fixture(autouse=True)
def patch_cost_curve():
    with patch.object(le, "get_leveling_cost", fake_get_leveling_cost), \
         patch.object(le, "MAX_LEVEL", FAKE_MAX_LEVEL):
        yield


DEFAULT_LEVELING_CONFIG = {
    "min_relevant_probability": 0.25,
    "max_contenders_per_slot": 3,
    "max_scout_level": 16,
    "scout_step_options": [4, 8, 12, 16],
}


# ---------------------------------------------------------------------------
# _finish_mora_cost / _cheapest_scout_step - the raw cost building blocks
# ---------------------------------------------------------------------------

def test_finish_mora_cost_uses_fake_curve():
    # 0 -> 20 at 10,000/level = 200,000
    assert le._finish_mora_cost(5, 0, 20) == 200_000


def test_cheapest_scout_step_skips_non_meaningful_targets_when_hidden():
    # A hidden substat means +4 only reveals it (no new roll value yet) -
    # the first MEANINGFUL checkpoint is +8, so level 4 gets skipped.
    artifact = make_artifact(level=0, hidden=True)
    step = le._cheapest_scout_step(artifact, [4, 8, 12, 16], max_scout_level=16)
    assert step == (8, 80_000)  # (0 -> 8) x 10,000


def test_cheapest_scout_step_allows_level_4_when_no_hidden_substat():
    # All 4 substats already unlocked -> +4 is already a real roll.
    artifact = make_artifact(level=0, hidden=False)
    step = le._cheapest_scout_step(artifact, [4, 8, 12, 16], max_scout_level=16)
    assert step == (4, 40_000)


def test_cheapest_scout_step_returns_none_at_max_scout_level():
    # Nothing left to learn -> the terminal case.
    artifact = make_artifact(level=16, hidden=False)
    step = le._cheapest_scout_step(artifact, [4, 8, 12, 16], max_scout_level=16)
    assert step is None


def test_cheapest_scout_step_returns_none_at_artifact_max_level():
    artifact = make_artifact(level=20, rarity=5, hidden=False)
    step = le._cheapest_scout_step(artifact, [4, 8, 12, 16], max_scout_level=16)
    assert step is None


# ---------------------------------------------------------------------------
# _decide_slot_action - the core explore-vs-exploit rule
# ---------------------------------------------------------------------------

def test_low_probability_leader_scouts_when_cheap_to_learn_more():
    """
    Leader at 40%, level 0, hidden substat.
      expected_waste = 200,000 x (1 - 0.40) = 120,000
      scout_cost     = 80,000  (0 -> 8, hidden reveal)
      120,000 > 80,000 -> Scout.
    This is the shape of the real Mona-Feather row: moderate confidence,
    large finish cost, cheap to learn more before committing.
    """
    contenders = [make_candidate(0.40, level=0, hidden=True)]
    decision = le._decide_slot_action(contenders, DEFAULT_LEVELING_CONFIG)
    assert decision["expected_waste"] == pytest.approx(120_000)
    assert decision["scout_cost"] == 80_000
    assert decision["resolved"] is False


def test_high_probability_leader_commits_even_with_scouting_available():
    """
    Leader at 90%, level 0, hidden substat.
      expected_waste = 200,000 x (1 - 0.90) = 20,000
      scout_cost     = 80,000
      20,000 <= 80,000 -> the gamble is already cheaper than finding out
      more, even though scouting is still technically available. Commit.
    """
    contenders = [make_candidate(0.90, level=0, hidden=True)]
    decision = le._decide_slot_action(contenders, DEFAULT_LEVELING_CONFIG)
    assert decision["expected_waste"] == pytest.approx(20_000)
    assert decision["resolved"] is True


def test_breakeven_probability_ties_go_to_commit():
    """
    Solve for the exact probability where expected_waste == scout_cost:
      200,000 x (1 - p) = 80,000  ->  p = 0.60
    Confirms ties resolve to Commit (the code uses a strict '>' for Scout,
    so equality falls through to Commit).
    """
    contenders = [make_candidate(0.60, level=0, hidden=True)]
    decision = le._decide_slot_action(contenders, DEFAULT_LEVELING_CONFIG)
    assert decision["expected_waste"] == pytest.approx(80_000)
    assert decision["resolved"] is True


def test_nothing_left_to_scout_forces_terminal_commit_regardless_of_confidence():
    """
    Leader already at max_scout_level (16) - no scout step exists at all.
    Even a middling 50% probability still resolves to Commit: there's no
    cheaper alternative left, so this is a terminal-state collapse, not a
    confidence judgment.
    """
    contenders = [make_candidate(0.50, level=16, hidden=False)]
    decision = le._decide_slot_action(contenders, DEFAULT_LEVELING_CONFIG)
    assert decision["scout_cost"] is None
    assert decision["resolved"] is True


def test_cost_asymmetry_means_realistic_probabilities_favor_scout():
    """
    Documents the real-run finding from this conversation: with a finish
    cost roughly 2.5-6x the scout cost (matching production's ~270,475 vs
    ~44,725-16,300 ratio), the breakeven probability sits around 60% - well
    above the min_relevant_probability floor (25%). That's WHY a normal
    early run's table is almost entirely Scout rows with no Commits: it's
    the cost ratio doing its job, not a bug.
    """
    breakeven = 1 - (80_000 / 200_000)
    assert breakeven == pytest.approx(0.60)

    just_below = le._decide_slot_action([make_candidate(0.59, level=0, hidden=True)], DEFAULT_LEVELING_CONFIG)
    just_above = le._decide_slot_action([make_candidate(0.61, level=0, hidden=True)], DEFAULT_LEVELING_CONFIG)
    assert just_below["resolved"] is False  # Scout
    assert just_above["resolved"] is True   # Commit


def test_cheaper_scout_lowers_the_breakeven_probability():
    """
    Same leader confidence (70%), but no hidden substat means the cheapest
    scout checkpoint is level 4 (40,000) instead of level 8 (80,000) for a
    hidden piece. Cheaper information changes the verdict even at identical
    probability - confirms cost, not just confidence, is doing real work.
      expected_waste = 200,000 x 0.30 = 60,000
      hidden:     scout_cost=80,000 -> 60,000 <= 80,000 -> Commit
      not hidden: scout_cost=40,000 -> 60,000 >  40,000 -> Scout
    """
    hidden_case = le._decide_slot_action([make_candidate(0.70, level=0, hidden=True)], DEFAULT_LEVELING_CONFIG)
    revealed_case = le._decide_slot_action([make_candidate(0.70, level=0, hidden=False)], DEFAULT_LEVELING_CONFIG)
    assert hidden_case["resolved"] is True     # scouting is relatively expensive here -> Commit
    assert revealed_case["resolved"] is False  # scouting is cheap here -> Scout


# ---------------------------------------------------------------------------
# _select_contenders - the hard probability floor
# ---------------------------------------------------------------------------

def test_select_contenders_drops_everything_below_floor():
    candidates = [make_candidate(0.10), make_candidate(0.05)]
    result = le._select_contenders(candidates, min_relevant_prob=0.25, contested_margin=0.0, max_contenders=3)
    assert result == []


def test_select_contenders_keeps_only_what_clears_floor_sorted_desc():
    candidates = [make_candidate(0.10), make_candidate(0.40), make_candidate(0.30)]
    result = le._select_contenders(candidates, min_relevant_prob=0.25, contested_margin=0.0, max_contenders=3)
    assert [c["probability"] for c in result] == [0.40, 0.30]


def test_select_contenders_respects_max_contenders_cap():
    candidates = [make_candidate(p) for p in [0.90, 0.80, 0.70, 0.60]]
    result = le._select_contenders(candidates, min_relevant_prob=0.25, contested_margin=0.0, max_contenders=2)
    assert [c["probability"] for c in result] == [0.90, 0.80]


def test_select_contenders_boundary_value_is_included():
    # Exactly AT the floor should clear it (>=), not be excluded.
    candidates = [make_candidate(0.25)]
    result = le._select_contenders(candidates, min_relevant_prob=0.25, contested_margin=0.0, max_contenders=3)
    assert len(result) == 1


# ---------------------------------------------------------------------------
# plan_slot_actions - end-to-end for one slot, tying the above together
# ---------------------------------------------------------------------------

def test_plan_slot_actions_produces_no_rows_when_everything_below_floor():
    candidates = [make_candidate(0.10), make_candidate(0.05)]
    actions = le.plan_slot_actions("TestChar", "Circlet", candidates, DEFAULT_LEVELING_CONFIG)
    assert actions == []


def test_plan_slot_actions_scouts_every_live_contender_when_contested():
    candidates = [make_candidate(0.40, hidden=True), make_candidate(0.30, hidden=True)]
    actions = le.plan_slot_actions("TestChar", "Circlet", candidates, DEFAULT_LEVELING_CONFIG)
    assert len(actions) == 2
    assert all(a["action_type"] == "Scout" for a in actions)
    assert all(a["target_level"] == 8 for a in actions)  # cheapest meaningful reveal


def test_plan_slot_actions_commits_only_the_leader_when_resolved():
    candidates = [make_candidate(0.90, hidden=True), make_candidate(0.10, hidden=True)]
    actions = le.plan_slot_actions("TestChar", "Circlet", candidates, DEFAULT_LEVELING_CONFIG)
    # The 0.10 candidate never reaches `contenders` at all (below the 0.25
    # floor) - only the leader produces a row, and it's a Commit.
    assert len(actions) == 1
    assert actions[0]["action_type"] == "Commit"
    assert actions[0]["target_level"] == 20  # FAKE_MAX_LEVEL[5]


def test_plan_slot_actions_single_survivor_still_needs_real_confidence():
    """
    Regression test for the bug fixed earlier this conversation: a lone
    surviving contender (e.g. everything else got hard-excluded by the
    tier-upgrade filter upstream) must NOT be auto-committed just for being
    alone. At 40% with a hidden substat, expected_waste (120,000) still
    beats the 80,000 scout cost, so this must Scout, not Commit - even
    though it's the only candidate in the list.
    """
    candidates = [make_candidate(0.40, hidden=True)]
    actions = le.plan_slot_actions("TestChar", "Circlet", candidates, DEFAULT_LEVELING_CONFIG)
    assert len(actions) == 1
    assert actions[0]["action_type"] == "Scout"


# ---------------------------------------------------------------------------
# plan_slot_actions - max-level (already-finished) contenders
# ---------------------------------------------------------------------------

def test_plan_slot_actions_only_maxed_contenders_returns_no_action():
    # A finished piece is a zero-Mora slot option - with nothing under-leveled
    # left to contend, the slot is settled and must not spend any Mora.
    candidates = [make_candidate(0.90, level=20)]
    actions = le.plan_slot_actions("TestChar", "Circlet", candidates, DEFAULT_LEVELING_CONFIG)
    assert actions == []


def test_plan_slot_actions_maxed_leader_does_not_close_contested_slot():
    # A maxed leader used to force Commit via finish_cost(20->20)=0 and then be
    # skipped in the action loop, silently closing the slot. Now the maxed piece
    # is set aside and the under-leveled live contender is decided on its own
    # probability: 30% at level 0 with a hidden substat -> expected_waste
    # 200,000 x (1-0.30) = 140,000 > 80,000 scout cost -> Scout, not a closed slot.
    candidates = [
        make_candidate(0.40, level=20),           # finished bench piece
        make_candidate(0.30, level=0, hidden=True),
    ]
    actions = le.plan_slot_actions("TestChar", "Circlet", candidates, DEFAULT_LEVELING_CONFIG)
    assert len(actions) == 1
    assert actions[0]["action_type"] == "Scout"
    assert actions[0]["target_level"] == 8  # cheapest meaningful reveal


def test_plan_slot_actions_maxed_runner_up_decision_uses_actionable_only():
    # The maxed piece at 90% would otherwise Commit-and-skip (0 actions). With it
    # set aside, the 60% under-leveled leader hits the breakeven Commit
    # (expected_waste == scout_cost at 60%) and gets committed to max.
    candidates = [
        make_candidate(0.90, level=20),
        make_candidate(0.60, level=0, hidden=True),
    ]
    actions = le.plan_slot_actions("TestChar", "Circlet", candidates, DEFAULT_LEVELING_CONFIG)
    assert len(actions) == 1
    assert actions[0]["action_type"] == "Commit"
    assert actions[0]["target_level"] == 20  # FAKE_MAX_LEVEL[5]


def test_plan_slot_actions_maxed_contender_with_sub_floor_actionable_no_action():
    # The finished piece dominates (90%); the lone under-leveled contender is
    # below min_relevant_probability, so it never reaches `contenders` at all.
    # Nothing to level -> the slot is settled by the finished piece.
    candidates = [
        make_candidate(0.90, level=20),
        make_candidate(0.10, level=0),
    ]
    actions = le.plan_slot_actions("TestChar", "Circlet", candidates, DEFAULT_LEVELING_CONFIG)
    assert actions == []


# ---------------------------------------------------------------------------
# tier_upgrade_ok / reachable_tier_for - the tier-upgrade gate
# ---------------------------------------------------------------------------

@pytest.mark.parametrize("equipped_tier,reachable_tier,expected", [
    ("Needs Work", "Good", True),
    ("Needs Work", "Excellent", True),
    ("Needs Work", "Needs Work", False),
    ("Missing", "Good", True),
    (None, "Good", True),               # unknown equipped tier treated as needing work
    ("Good", "Excellent", True),
    ("Good", "Good", False),            # same tier isn't an upgrade
    ("Excellent", "Excellent", False),  # already top tier - nothing beats it
    ("Excellent", "Good", False),
    ("Good", None, False),              # candidate's own ceiling unknown -> can't confirm upgrade
])
def test_tier_upgrade_ok_matrix(equipped_tier, reachable_tier, expected):
    assert le.tier_upgrade_ok(equipped_tier, reachable_tier) is expected


@pytest.mark.parametrize("max_rolls,good,excellent,expected", [
    (5.0, 6.0, 8.0, "Needs Work"),
    (6.0, 6.0, 8.0, "Good"),        # boundary: exactly at `good` counts as Good
    (8.0, 6.0, 8.0, "Excellent"),   # boundary: exactly at `excellent` counts as Excellent
    (None, 6.0, 8.0, None),
])
def test_reachable_tier_for_matrix(max_rolls, good, excellent, expected):
    assert le.reachable_tier_for(max_rolls, good, excellent) == expected


# ---------------------------------------------------------------------------
# any_tier_upgrade_available - the roster-wide hard-exclude gate
# ---------------------------------------------------------------------------

def test_any_tier_upgrade_available_true_when_one_candidate_qualifies():
    optimizer_candidates = {
        "CharA": {"Circlet": [make_candidate(0.5, tier_upgrade_ok=False)]},
        "CharB": {"Feather": [make_candidate(0.3, tier_upgrade_ok=True)]},
    }
    assert le.any_tier_upgrade_available(optimizer_candidates, [], set(), {}) is True


def test_any_tier_upgrade_available_false_when_nothing_qualifies():
    optimizer_candidates = {
        "CharA": {"Circlet": [make_candidate(0.5, tier_upgrade_ok=False)]},
    }
    assert le.any_tier_upgrade_available(optimizer_candidates, [], set(), {}) is False


def test_any_tier_upgrade_available_ignores_skipped_characters():
    # CharA's tier-upgrading candidate exists, but CharA is in skip_chars
    # (e.g. failed the coverage gate) - shouldn't count towards "available".
    optimizer_candidates = {
        "CharA": {"Circlet": [make_candidate(0.5, tier_upgrade_ok=True)]},
    }
    assert le.any_tier_upgrade_available(optimizer_candidates, [], {"CharA"}, {}) is False


# ---------------------------------------------------------------------------
# "can never reach Good" hard gate - the Kuki flower regression
# ---------------------------------------------------------------------------

def test_plan_slot_actions_never_plans_pieces_that_cant_reach_good():
    """
    Regression for the Kuki flower report: a piece whose honest ceiling is
    'Needs Work' (best case still can't clear the slot's `good` bar) must not
    be committed just for being the only in-set candidate. Optimizer
    probability is a measure of "best of what's available", not of absolute
    quality - a ~100%-probability throwaway gets no Scout and no Commit.
    """
    candidates = [make_candidate(0.99, hidden=False, reachable_tier="Needs Work")]
    actions = le.plan_slot_actions("KukiShinobu", "Flower", candidates, DEFAULT_LEVELING_CONFIG)
    assert actions == []


def test_plan_slot_actions_filters_needs_work_candidates_out_of_contention():
    # A Needs Work candidate is dropped before any Scout/Commit decision
    # without dragging down the slot's real contenders.
    candidates = [
        make_candidate(0.95, hidden=False, reachable_tier="Excellent"),
        make_candidate(0.50, hidden=False, reachable_tier="Needs Work"),
    ]
    actions = le.plan_slot_actions("TestChar", "Circlet", candidates, DEFAULT_LEVELING_CONFIG)
    assert len(actions) == 1
    assert actions[0]["action_type"] == "Commit"


def test_plan_slot_actions_allows_candidates_without_reachable_tier():
    # Callers that don't compute tier reachability (require_tier_upgrade
    # disabled) must be unaffected - the gate only hard-blocks on an explicit
    # "Needs Work".
    cand = make_candidate(0.99, hidden=False)
    del cand["reachable_tier"]
    actions = le.plan_slot_actions("TestChar", "Circlet", [cand], DEFAULT_LEVELING_CONFIG)
    assert len(actions) == 1
    assert actions[0]["action_type"] == "Commit"


def test_plan_slot_actions_still_plans_candidates_that_reach_good_or_excellent():
    # Good/Excellent ceilings are unaffected by the gate.
    candidates = [
        make_candidate(0.90, hidden=False, reachable_tier="Good"),
        make_candidate(0.30, hidden=False, reachable_tier="Good"),
    ]
    actions = le.plan_slot_actions("TestChar", "Circlet", candidates, DEFAULT_LEVELING_CONFIG)
    assert len(actions) == 1
    assert actions[0]["action_type"] == "Commit"


def test_plan_slot_actions_absolute_gate_keeps_candidate_that_can_reach_good():
    # Candidates with a non-zero posterior probability of reaching Good must
    # pass the absolute probability gate even if that probability is small.
    artifact = make_artifact(level=0, hidden=True)
    artifact["substats"] = [
        {"key": "critRate_", "value": 3.5},
        {"key": "critDMG_", "value": 7.8},
        {"key": "hp_", "value": 269},
        {"key": "def_", "value": 19},
    ]
    artifact["unactivatedSubstats"] = [{"key": "critDMG_", "value": 7.8}]
    cand = {
        "artifact": artifact,
        "probability": 0.90,
        "is_equipped": False,
        "tier_upgrade_ok": True,
        "reachable_tier": "Good",
        "good": 5,
        "excellent": 7,
        "useful_stats": ["CR", "CD"],
        "current_rolls": 2,
        "max_rolls": 6,
    }
    actions = le.plan_slot_actions("TestChar", "Circlet", [cand], DEFAULT_LEVELING_CONFIG)
    assert len(actions) == 1
    assert actions[0]["action_type"] == "Commit"


def test_plan_slot_actions_absolute_gate_drops_candidate_that_cannot_reach_good():
    # abs_prob == 0 (best case still below `good`) -> dropped by the gate,
    # even if the cached reachable_tier is stale/optimistic.
    artifact = make_artifact(level=0, hidden=False)
    artifact["substats"] = [
        {"key": "hp_", "value": 269},
        {"key": "def_", "value": 19},
    ]
    cand = {
        "artifact": artifact,
        "probability": 0.90,
        "is_equipped": False,
        "tier_upgrade_ok": True,
        "reachable_tier": "Good",  # stale cache - honest ceiling is Needs Work
        "good": 5,
        "excellent": 7,
        "useful_stats": ["CR", "CD"],
        "current_rolls": 0,
        "max_rolls": 2,
    }
    actions = le.plan_slot_actions("TestChar", "Circlet", [cand], DEFAULT_LEVELING_CONFIG)
    assert actions == []


def test_plan_slot_actions_no_action_when_leader_already_at_it_cap():
    """
    Regression for the 16 -> 16 rows: an IT Only piece already sitting at
    rules.yaml leveling.it_only_max_level (16) has nothing left to level.
    Analysis still treats it as improvable to its true max (20), so the slot
    resolves - but a Commit with target_level == current_level is a no-op and
    must never appear as a recommendation.
    """
    candidates = [make_candidate(0.90, level=16, hidden=False)]
    actions = le.plan_slot_actions(
        "ITChar", "Feather", candidates, DEFAULT_LEVELING_CONFIG,
        effective_max_level=16,
    )
    assert actions == []


def test_plan_slot_actions_at_cap_leader_closes_slot_no_weaker_promotion():
    """
    The stronger piece is already at the IT cap and (having no scout step
    left) the slot resolves to Commit. We must NOT drop it from contention and
    then start recommending the weaker level-8 runner-up as a "new leader" -
    closing the slot means closing it: zero actions for the whole slot and the
    weaker piece left unleveled.
    """
    candidates = [
        make_candidate(0.90, level=16, hidden=False),  # at cap -> slot's final answer
        make_candidate(0.30, level=8, hidden=False),   # weaker - must not be promoted
    ]
    actions = le.plan_slot_actions(
        "ITChar", "Feather", candidates, DEFAULT_LEVELING_CONFIG,
        effective_max_level=16,
    )
    assert actions == []


def test_plan_slot_actions_it_piece_below_cap_still_commits_to_cap():
    # The at-cap guard must not over-filter: an IT piece below the cap still
    # gets recommended, but only as far as the cap (16), not its true max (20).
    candidates = [make_candidate(0.90, level=8, hidden=True)]
    actions = le.plan_slot_actions(
        "ITChar", "Feather", candidates, DEFAULT_LEVELING_CONFIG,
        effective_max_level=16,
    )
    assert len(actions) == 1
    assert actions[0]["action_type"] == "Commit"
    assert actions[0]["current_level"] == 8
    assert actions[0]["target_level"] == 16


def test_plan_slot_actions_at_cap_runner_up_skipped_in_contested_slot():
    # A contested slot's still-levelable leader is scouted; an at-cap runner-up
    # contributes no action (its scout target would be <= current level anyway).
    candidates = [
        make_candidate(0.30, level=8, hidden=True),   # below cap -> live contender
        make_candidate(0.40, level=16, hidden=False), # at cap -> no action
    ]
    actions = le.plan_slot_actions(
        "ITChar", "Feather", candidates, DEFAULT_LEVELING_CONFIG,
        effective_max_level=16,
    )
    assert len(actions) == 1
    assert actions[0]["action_type"] == "Scout"
    assert actions[0]["current_level"] == 8
    assert actions[0]["target_level"] == 16


# ---------------------------------------------------------------------------
# Character priority tier gate (leveling.tier_gated)
# ---------------------------------------------------------------------------

def test_char_tier_mapping():
    # Mirrors character_scoring.score_character's 1-5 priority tiers.
    assert le._char_tier("Active", "Farming") == 1
    assert le._char_tier("IT Only", "Farming") == 2
    assert le._char_tier("Active", "Finished") == 3
    assert le._char_tier("IT Only", "Finished") == 4
    assert le._char_tier("Active", "Usable") == 5
    assert le._char_tier("IT Only", "Luxury") == 5
    assert le._char_tier(None, None) == 5


def _optimizer_budget():
    return {
        "max_mora": 2_000_000,
        "max_artifact_exp": 100_000_000,
        "already_spent_mora": 0,
        "already_spent_exp": 0,
    }


def test_tier_gated_optimizer_keeps_only_top_tier():
    # Tier-1 (Active+Farming) and tier-2 (IT Only+Farming) both have viable
    # commits this run -> only the tier-1 action may be suggested.
    candidates_by_char = {
        "ActiveFarmer": {"Feather": [make_candidate(0.90)]},
        "ITFarmer": {"Flower": [make_candidate(0.90)]},
    }
    plan = le.build_leveling_plan_from_optimizer(
        candidates_by_char,
        {"ActiveFarmer": 900, "ITFarmer": 800},
        _optimizer_budget(),
        {**DEFAULT_LEVELING_CONFIG, "tier_gated": True},
        char_tier_lookup={"ActiveFarmer": 1, "ITFarmer": 2},
    )
    assert plan["top_tier"] == 1
    chars = {a["character"] for a in plan["actions"]}
    assert chars == {"ActiveFarmer"}
    assert all(a["tier"] == 1 for a in plan["actions"])


def test_tier_gated_optimizer_falls_through_when_top_tier_has_no_viable_actions():
    # The tier-1 character has nothing that meets a scout/commit threshold
    # (below min_relevant_probability) -> tier-2 becomes the top tier.
    candidates_by_char = {
        "ActiveFarmer": {"Feather": [make_candidate(0.05)]},   # below floor -> no action
        "ITFarmer": {"Flower": [make_candidate(0.90)]},
    }
    plan = le.build_leveling_plan_from_optimizer(
        candidates_by_char,
        {"ActiveFarmer": 900, "ITFarmer": 800},
        _optimizer_budget(),
        {**DEFAULT_LEVELING_CONFIG, "tier_gated": True},
        char_tier_lookup={"ActiveFarmer": 1, "ITFarmer": 2},
    )
    assert plan["top_tier"] == 2
    chars = {a["character"] for a in plan["actions"]}
    assert chars == {"ITFarmer"}
    assert all(a["tier"] == 2 for a in plan["actions"])


def test_tier_gated_disabled_shows_both_tiers():
    # With the gate off, both tiers' actions are allowed (tier-1 still sorts
    # first, but nothing is dropped).
    candidates_by_char = {
        "ActiveFarmer": {"Feather": [make_candidate(0.90)]},
        "ITFarmer": {"Flower": [make_candidate(0.90)]},
    }
    plan = le.build_leveling_plan_from_optimizer(
        candidates_by_char,
        {"ActiveFarmer": 900, "ITFarmer": 800},
        _optimizer_budget(),
        {**DEFAULT_LEVELING_CONFIG, "tier_gated": False},
        char_tier_lookup={"ActiveFarmer": 1, "ITFarmer": 2},
    )
    chars = [a["character"] for a in plan["actions"]]
    assert chars == ["ActiveFarmer", "ITFarmer"]


def _make_bench(character, slot, expected_rolls=8, good=5, excellent=7,
                current_rolls=2, max_rolls=8):
    return {
        "character": character,
        "slot": slot,
        "artifact_id": f"{character}-{slot}",
        "rarity": 5,
        "level": 0,
        "original_artifact": {
            "id": f"{character}-{slot}",
            "level": 0,
            "rarity": 5,
            "substats": [
                {"key": "critDMG_", "value": 7.8},
                {"key": "critRate_", "value": 3.5},
            ],
            "unactivatedSubstats": [],
        },
        "good": good,
        "excellent": excellent,
        "useful_stats": ["CR", "CD"],
        "current_rolls": current_rolls,
        "expected_rolls": expected_rolls,
        "max_rolls": max_rolls,
        "verdict": "Could reach Good",
    }


def test_tier_gated_legacy_planner_keeps_only_top_tier():
    # Both a tier-1 and a tier-2 character have viable legacy candidates ->
    # only the tier-1 candidate survives the gate.
    bench_results = [
        _make_bench("LegacyT1", "Feather"),
        _make_bench("LegacyT2", "Flower"),
    ]
    plan = le.build_leveling_plan(
        bench_results,
        _optimizer_budget(),
        {**DEFAULT_LEVELING_CONFIG, "tier_gated": True, "soft_stop_floor": 0.15},
        char_tier_lookup={"LegacyT1": 1, "LegacyT2": 2},
    )
    assert plan["top_tier"] == 1
    chars = {a["character"] for a in plan["actions"]}
    assert chars == {"LegacyT1"}
    assert all(a["tier"] == 1 for a in plan["actions"])


def test_tier_gated_combined_reconciles_across_planners():
    # The optimizer planner has a viable tier-1 commit while the legacy
    # planner (fallback) has viable tier-2 candidates -> the tier-2 legacy
    # actions must be dropped from the merged plan.
    candidates_by_char = {
        "ActiveFarmer": {"Feather": [make_candidate(0.90)]},
    }
    slots = ["Flower", "Feather", "Sands", "Goblet", "Circlet"]
    bench_results = (
        [_make_bench("ActiveFarmer", s) for s in slots]
        + [_make_bench("ITLegacy", s) for s in slots]
    )
    plan = le.build_combined_leveling_plan(
        bench_results,
        candidates_by_char,
        {"ActiveFarmer": 900, "ITLegacy": 800},
        _optimizer_budget(),
        {
            **DEFAULT_LEVELING_CONFIG,
            "tier_gated": True,
            "soft_stop_floor": 0.15,
            "min_distinct_on_set_slots": 4,
            "max_pieces_per_run": 10,
            "require_tier_upgrade": True,
            "it_only_max_level": 16,
        },
        char_usage_lookup={"ActiveFarmer": "Active", "ITLegacy": "IT Only"},
        char_status_lookup={"ActiveFarmer": "Farming", "ITLegacy": "Farming"},
    )
    assert plan["summary"]["top_tier"] == 1
    chars = {a["character"] for a in plan["actions"]}
    assert chars == {"ActiveFarmer"}
    assert "tier-1" in plan["summary"]["recommendation_text"]