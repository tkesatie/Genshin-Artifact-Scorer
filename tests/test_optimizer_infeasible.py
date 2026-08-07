"""
Tests for the optimizer's infeasible-rate tracking.

Verifies that compute_optimal_probabilities reports the fraction of
simulations where no candidate combo met the configured stat floors
(e.g. ER/EM minimums from stat_targets.yaml), so the dashboard can
explain why per-artifact probabilities don't sum to 100%.
"""
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).parent.parent))

from optimizer import compute_optimal_probabilities

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


def make_pools(artifacts_by_slot):
    """Convert {slot: [artifacts]} into in_set/off_set pool dicts."""
    in_set = {slot: [] for slot in SLOTS}
    off_set = {slot: [] for slot in SLOTS}
    for slot, arts in artifacts_by_slot.items():
        in_set[slot] = arts
    return in_set, off_set


# A minimal ATK-primary character config (Skirk-like).
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


def build_full_set():
    """A full 5-piece in-set build with no ER substats (ER stays at base 1.0)."""
    return {
        "Flower": [make_artifact(1, "flower", "hp", 4780, substats=[
            {"key": "atk_", "value": 10.0},
            {"key": "critRate_", "value": 5.0},
            {"key": "critDMG_", "value": 10.0},
        ])],
        "Feather": [make_artifact(2, "plume", "atk", 311, substats=[
            {"key": "atk_", "value": 10.0},
            {"key": "critRate_", "value": 5.0},
            {"key": "critDMG_", "value": 10.0},
        ])],
        "Sands": [make_artifact(3, "sands", "atk_", 46.6, substats=[
            {"key": "atk_", "value": 10.0},
            {"key": "critRate_", "value": 5.0},
            {"key": "critDMG_", "value": 10.0},
        ])],
        "Goblet": [make_artifact(4, "goblet", "atk_", 46.6, substats=[
            {"key": "atk_", "value": 10.0},
            {"key": "critRate_", "value": 5.0},
            {"key": "critDMG_", "value": 10.0},
        ])],
        "Circlet": [make_artifact(5, "circlet", "critRate_", 31.1, substats=[
            {"key": "atk_", "value": 10.0},
            {"key": "critRate_", "value": 5.0},
            {"key": "critDMG_", "value": 10.0},
        ])],
    }


def test_no_stat_floors_all_valid():
    """With no stat floors, every sim has a valid combo: infeasible_rate == 0
    and each slot's probabilities sum to 100%."""
    in_set, off_set = make_pools(build_full_set())
    current = {slot: arts[0] for slot, arts in build_full_set().items()}

    result = compute_optimal_probabilities(
        char_config=CHAR_CONFIG,
        in_set_pools=in_set,
        off_set_pools=off_set,
        current_artifacts=current,
        roll_values=ROLL_VALUES,
        target_set_keys=TARGET_SET,
        num_sims=50,
        stat_floors=None,
        team_context={},
    )

    assert result["infeasible_rate"] == 0.0
    # Each slot has exactly 1 candidate, so each artifact's probability is 1.0
    # (it's in the best combo every sim). Sum across all 5 slots = 5.0.
    total_prob = sum(result["probabilities"].values())
    assert total_prob == pytest.approx(5.0, abs=1e-6)
    # Per-slot: each slot's single candidate has probability 1.0
    for art_id in range(1, 6):
        assert result["probabilities"][art_id] == pytest.approx(1.0, abs=1e-6)


def test_impossible_er_floor_all_infeasible():
    """An ER floor that no combo can reach (base ER is 1.0, no ER substats)
    means every sim is infeasible: infeasible_rate == 1.0 and all
    probabilities are 0."""
    in_set, off_set = make_pools(build_full_set())
    current = {slot: arts[0] for slot, arts in build_full_set().items()}

    result = compute_optimal_probabilities(
        char_config=CHAR_CONFIG,
        in_set_pools=in_set,
        off_set_pools=off_set,
        current_artifacts=current,
        roll_values=ROLL_VALUES,
        target_set_keys=TARGET_SET,
        num_sims=50,
        stat_floors={"energy_recharge": 2.0},  # base is 1.0, no ER substats
        team_context={},
    )

    assert result["infeasible_rate"] == 1.0
    total_prob = sum(result["probabilities"].values())
    assert total_prob == pytest.approx(0.0, abs=1e-6)


def test_mixed_feasibility():
    """When some combos meet the floor and others don't, infeasible_rate is
    between 0 and 1, and per-slot probabilities + infeasible_rate sum to 1.0."""
    # The Sands is under-leveled (level 16, 1 upgrade event left) with an ER
    # substat at 5.0 (ER = 1.05). The floor is 1.1. The remaining upgrade
    # event rolls randomly among 4 active substats: only rolling ER again
    # (ER -> 1.10) makes the build valid. So ~3/4 of sims are infeasible.
    arts = build_full_set()
    arts["Sands"] = [
        make_artifact(3, "sands", "atk_", 46.6, level=16, substats=[
            {"key": "atk_", "value": 10.0},
            {"key": "critRate_", "value": 5.0},
            {"key": "critDMG_", "value": 10.0},
            {"key": "enerRech_", "value": 5.0},  # ER 1.05, needs one more ER roll to hit 1.1
        ]),
    ]
    in_set, off_set = make_pools(arts)
    current = {slot: arts_list[0] for slot, arts_list in arts.items()}

    result = compute_optimal_probabilities(
        char_config=CHAR_CONFIG,
        in_set_pools=in_set,
        off_set_pools=off_set,
        current_artifacts=current,
        roll_values=ROLL_VALUES,
        target_set_keys=TARGET_SET,
        num_sims=200,
        stat_floors={"energy_recharge": 1.1},  # only met if the last roll lands on ER
        team_context={},
    )

    assert 0.0 < result["infeasible_rate"] < 1.0
    # The Sands is the only Sands candidate, so when a sim is valid it's
    # always in the best combo: P(Sands) = 1 - infeasible_rate.
    assert result["probabilities"][3] == pytest.approx(1.0 - result["infeasible_rate"], abs=1e-6)
    # Per-slot: Sands probability + infeasible_rate = 1.0
    assert result["probabilities"][3] + result["infeasible_rate"] == pytest.approx(1.0, abs=1e-6)
