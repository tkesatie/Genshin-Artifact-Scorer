"""
Tests for the optimizer's early-pruning feature.

Verifies that compute_optimal_probabilities (via _prune_slot_candidates) drops
candidates whose probability of being in the optimal build is below a threshold
after a warm-up phase, while preserving build legality and reporting
probabilities based on the full simulation run.
"""
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).parent.parent))

from optimizer import compute_optimal_probabilities, _prune_slot_candidates

SLOTS = ["Flower", "Feather", "Sands", "Goblet", "Circlet"]


def make_artifact(art_id, slot, main_key, main_val, substats=None, rarity=5, level=20, set_key="TestSet"):
    """Build a minimal artifact dict for testing."""
    return {
        "id": art_id,
        "slotKey": slot,
        "mainStatKey": main_key,
        "mainStatValue": main_val,
        "substats": substats or [],
        "unactivatedSubstats": [],
        "rarity": rarity,
        "level": level,
        "setKey": set_key,
    }


# ---- Unit tests for _prune_slot_candidates (the pruning logic) ----


def _sc(entries):
    """Helper: build a slot_candidates dict with a single slot from (id, is_in) entries."""
    return {"Flower": [(art_id, {"id": art_id}, is_in) for art_id, is_in in entries]}


def test_drops_below_threshold_keeps_at_or_above():
    """50/35/11/4 over 100 warm-up sims with a 5% cutoff -> the 4% one is dropped."""
    win_counts = {"a": 50, "b": 35, "c": 11, "d": 4}
    sc = _sc([("a", True), ("b", True), ("c", True), ("d", True)])
    out = _prune_slot_candidates(sc, win_counts, warmup_sims=100,
                                 threshold=0.05, min_keep=1, current_artifacts={})
    survivors = {c[0] for c in out["Flower"]}
    assert survivors == {"a", "b", "c"}
    assert "d" not in survivors


def test_strict_cutoff_exact_five_percent_kept():
    """Exactly 5.0% is >= 0.05, so it is kept; strictly-below is dropped."""
    win_counts = {"a": 5, "b": 4}
    sc = _sc([("a", True), ("b", True)])
    out = _prune_slot_candidates(sc, win_counts, warmup_sims=100,
                                 threshold=0.05, min_keep=1, current_artifacts={})
    assert {c[0] for c in out["Flower"]} == {"a"}


def test_min_keep_floor():
    """When the threshold would empty a slot, min_keep preserves the top by win rate."""
    win_counts = {"a": 1, "b": 2, "c": 3}
    sc = _sc([("a", True), ("b", True), ("c", True)])
    out = _prune_slot_candidates(sc, win_counts, warmup_sims=100,
                                 threshold=0.05, min_keep=2, current_artifacts={})
    survivors = {c[0] for c in out["Flower"]}
    assert len(survivors) == 2
    assert "c" in survivors  # best by win rate
    assert "b" in survivors


def test_keeps_at_least_one_in_set():
    """Even if every in-set candidate is below threshold, one in-set is kept."""
    win_counts = {"a": 1, "b": 2, "c": 3}
    sc = _sc([("a", True), ("b", True), ("c", True)])
    out = _prune_slot_candidates(sc, win_counts, warmup_sims=100,
                                 threshold=0.05, min_keep=1, current_artifacts={})
    survivors = {c[0] for c in out["Flower"]}
    assert len(survivors) == 1
    assert survivors == {"c"}  # top in-set by win rate


def test_keeps_equipped_piece_even_if_below_threshold():
    """The currently-equipped piece is always retained regardless of win rate."""
    win_counts = {"a": 0}
    sc = _sc([("a", True)])
    out = _prune_slot_candidates(sc, win_counts, warmup_sims=100,
                                 threshold=0.05, min_keep=1,
                                 current_artifacts={"Flower": {"id": "a"}})
    assert {c[0] for c in out["Flower"]} == {"a"}


# ---- Integration tests for compute_optimal_probabilities ----


def make_pools(artifacts_by_slot):
    in_set = {slot: [] for slot in SLOTS}
    off_set = {slot: [] for slot in SLOTS}
    for slot, arts in artifacts_by_slot.items():
        in_set[slot] = arts
    return in_set, off_set


CHAR_CONFIG = {
    "primary_stat": "ATK",
    "base_atk": 1033.1,
    "base_crit_rate": 0.05,
    "base_crit_damage": 0.50,
    "base_dmg_bonus": 0.0,
    "base_em": 0.0,
    "base_er": 1.0,
    "modifiers": [],
    "evaluation_pipeline": [
        {"type": "standard_damage"},
        {"type": "personal_damage"},
    ],
}

ROLL_VALUES = {
    "five_star": {
        "atk_": [5.0, 5.0, 5.0],
        "critRate_": [3.0, 3.0, 3.0],
        "critDMG_": [6.0, 6.0, 6.0],
        "enerRech_": [5.0, 5.0, 5.0],
        "eleMas": [20.0, 20.0, 20.0],
    },
    "four_star": {},
}

TARGET_SET = {"TestSet"}


def build_good_set():
    """A full 5-piece in-set build with strong substats (ids 1..5)."""
    sub = [
        {"key": "atk_", "value": 10.0},
        {"key": "critRate_", "value": 5.0},
        {"key": "critDMG_", "value": 10.0},
    ]
    return {
        "Flower": [make_artifact(1, "flower", "hp", 4780, substats=sub)],
        "Feather": [make_artifact(2, "plume", "atk", 311, substats=sub)],
        "Sands": [make_artifact(3, "sands", "atk_", 46.6, substats=sub)],
        "Goblet": [make_artifact(4, "goblet", "atk_", 46.6, substats=sub)],
        "Circlet": [make_artifact(5, "circlet", "critRate_", 31.1, substats=sub)],
    }


def build_pool_with_dominated():
    """Good set plus a strictly-worse dominated candidate (id 100..104) per slot."""
    arts = build_good_set()
    weak_sub = [
        {"key": "atk_", "value": 1.0},
        {"key": "critRate_", "value": 0.5},
        {"key": "critDMG_", "value": 1.0},
    ]
    arts["Flower"].append(make_artifact(100, "flower", "hp", 4780, substats=weak_sub))
    arts["Feather"].append(make_artifact(101, "plume", "atk", 311, substats=weak_sub))
    arts["Sands"].append(make_artifact(102, "sands", "atk_", 46.6, substats=weak_sub))
    arts["Goblet"].append(make_artifact(103, "goblet", "atk_", 46.6, substats=weak_sub))
    arts["Circlet"].append(make_artifact(104, "circlet", "critRate_", 31.1, substats=weak_sub))
    return arts


def test_pruning_end_to_end_drops_dominated_candidates():
    """With pruning enabled, dominated candidates are dropped after the warm-up,
    the good pieces stay at probability 1.0, and probabilities are based on the
    full num_sims run (per-slot sum over all 5 slots == 5.0)."""
    arts = build_pool_with_dominated()
    in_set, off_set = make_pools(arts)
    current = {slot: good[0] for slot, good in build_good_set().items()}

    result = compute_optimal_probabilities(
        char_config=CHAR_CONFIG,
        in_set_pools=in_set,
        off_set_pools=off_set,
        current_artifacts=current,
        roll_values=ROLL_VALUES,
        target_set_keys=TARGET_SET,
        num_sims=40,
        stat_floors=None,
        team_context={},
        prune_after=20,
        prune_threshold=0.05,
        prune_min_keep=1,
    )

    assert result["infeasible_rate"] == 0.0
    probs = result["probabilities"]
    # Every artifact (including dominated ones) is still reported, based on the
    # full 40-sim run; good pieces win every sim.
    for art_id in range(1, 6):
        assert probs[art_id] == pytest.approx(1.0, abs=1e-6)
    # Dominated pieces never win (dropped after warm-up and never win anyway).
    for art_id in range(100, 105):
        assert probs[art_id] == pytest.approx(0.0, abs=1e-6)
    # One full 5-piece best combo per valid sim -> sum over all artifacts is 5.0.
    assert sum(probs.values()) == pytest.approx(5.0, abs=1e-6)


def test_pruning_disabled_is_noop():
    """prune_after=0 leaves behavior unchanged (runs, no crash, same output shape)."""
    arts = build_pool_with_dominated()
    in_set, off_set = make_pools(arts)
    current = {slot: good[0] for slot, good in build_good_set().items()}

    result = compute_optimal_probabilities(
        char_config=CHAR_CONFIG,
        in_set_pools=in_set,
        off_set_pools=off_set,
        current_artifacts=current,
        roll_values=ROLL_VALUES,
        target_set_keys=TARGET_SET,
        num_sims=40,
        stat_floors=None,
        team_context={},
        prune_after=0,
    )

    assert result["infeasible_rate"] == 0.0
    assert sum(result["probabilities"].values()) == pytest.approx(5.0, abs=1e-6)


def test_pruning_after_never_triggers_when_prune_after_ge_num_sims():
    """If prune_after >= num_sims, no pruning happens (no crash, valid output)."""
    arts = build_pool_with_dominated()
    in_set, off_set = make_pools(arts)
    current = {slot: good[0] for slot, good in build_good_set().items()}

    result = compute_optimal_probabilities(
        char_config=CHAR_CONFIG,
        in_set_pools=in_set,
        off_set_pools=off_set,
        current_artifacts=current,
        roll_values=ROLL_VALUES,
        target_set_keys=TARGET_SET,
        num_sims=40,
        stat_floors=None,
        team_context={},
        prune_after=100,
        prune_threshold=0.05,
        prune_min_keep=1,
    )

    assert result["infeasible_rate"] == 0.0
    assert sum(result["probabilities"].values()) == pytest.approx(5.0, abs=1e-6)