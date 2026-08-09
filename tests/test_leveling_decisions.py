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


def make_candidate(probability, level=0, rarity=5, hidden=True, tier_upgrade_ok=True):
    return {
        "artifact": make_artifact(level=level, rarity=rarity, hidden=hidden),
        "probability": probability,
        "is_equipped": False,
        "tier_upgrade_ok": tier_upgrade_ok,
        "reachable_tier": "Excellent" if tier_upgrade_ok else "Good",
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