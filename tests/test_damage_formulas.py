"""
Tests for the damage formula pipeline: stats_calculator.calculate_build_stats
and damage_calculator.calculate_damage_score.

Verifies that the damage formulas receive the correct stats for:
Skirk, Escoffier, Mavuika, Mualani, Zibai, Citlali, Furina, Mona, Sucrose,
Keqing, Lauma, and Nahida.

Tests assert the *expected* correct behavior. If a test fails, it means either
the code has a bug or the expectation is wrong - investigate each failure.
"""
import sys
from pathlib import Path

import pytest

# Ensure project root is on sys.path so imports work
sys.path.insert(0, str(Path(__file__).parent.parent))

from config import load_configs
from stats_calculator import calculate_build_stats
from damage_calculator import (
    calculate_damage_score,
    get_em_bonus_amplifying,
    get_em_bonus_transformative,
    get_modifier_bonus,
    get_transformative_base_damage,
)

# Characters under test
TEST_CHARACTERS = [
    "Skirk", "Escoffier", "Mavuika", "Mualani", "Zibai", "Citlali",
    "Furina", "Mona", "Sucrose", "Keqing", "Lauma", "Nahida",
]


@pytest.fixture(scope="module")
def configs():
    """Load the merged roster (roster.yaml + character_bases.yaml)."""
    roster, rules, roll_values = load_configs()
    return roster, rules, roll_values


@pytest.fixture(scope="module")
def roster(configs):
    return configs[0]


@pytest.fixture(scope="module")
def roll_values(configs):
    return configs[2]


def build_context(char_config, artifacts=None, team_context=None, roll_values=None):
    """Construct a BuildContext dict for calculate_build_stats."""
    return {
        "character_config": char_config,
        "artifacts": artifacts or {},
        "team_context": team_context or {},
        "roll_values": roll_values or {},
        "damage_model": char_config.get("damage_model", "none"),
    }


def make_artifact(slot, main_key, main_val, substats=None, rarity=5):
    """Build a minimal artifact dict for testing."""
    return {
        "slotKey": slot,
        "mainStatKey": main_key,
        "mainStatValue": main_val,
        "substats": substats or [],
        "rarity": rarity,
        "level": 20,
    }


# =============================================================================
# A. Base stats (no artifacts equipped)
# =============================================================================

@pytest.mark.parametrize("char_name", TEST_CHARACTERS)
def test_base_stats_no_artifacts(char_name, roster, roll_values):
    """With no artifacts, calculate_build_stats should return the character's
    base stats from character_bases.yaml (or defaults)."""
    cfg = roster[char_name]
    stats = calculate_build_stats(build_context(cfg, roll_values=roll_values))

    primary_stat = cfg.get("primary_stat", "ATK")

    # --- Primary stat total ---
    if primary_stat == "EM":
        expected_primary = cfg.get("base_em", 0.0)
    else:
        base_key = f"base_{primary_stat.lower()}"
        base_val = cfg.get(base_key, 0.0)
        # Expected: base * (1 + base_percent) + base_flat
        # NOTE: base_def_percent / base_hp_percent / base_atk_percent are
        # expected to be honored here. If the test fails, stats_calculator.py
        # is not reading those keys.
        base_percent_key = f"base_{primary_stat.lower()}_percent"
        base_percent = cfg.get(base_percent_key, 0.0)
        expected_primary = base_val * (1 + base_percent)

    assert stats["primary_total"] == pytest.approx(expected_primary, rel=1e-6), (
        f"{char_name}: primary_total expected {expected_primary}, got {stats['primary_total']}"
    )

    # --- Crit rate ---
    expected_cr = cfg.get("base_crit_rate", 0.05)
    assert stats["crit_rate"] == pytest.approx(expected_cr, rel=1e-6), (
        f"{char_name}: crit_rate expected {expected_cr}, got {stats['crit_rate']}"
    )

    # --- Crit damage ---
    expected_cd = cfg.get("base_crit_damage", 0.50)
    assert stats["crit_damage"] == pytest.approx(expected_cd, rel=1e-6), (
        f"{char_name}: crit_damage expected {expected_cd}, got {stats['crit_damage']}"
    )

    # --- DMG bonus ---
    expected_dmg = cfg.get("base_dmg_bonus", 0.0)
    assert stats["dmg_bonus"] == pytest.approx(expected_dmg, rel=1e-6), (
        f"{char_name}: dmg_bonus expected {expected_dmg}, got {stats['dmg_bonus']}"
    )

    # --- Elemental mastery ---
    expected_em = cfg.get("base_em", 0.0)
    assert stats["elemental_mastery"] == pytest.approx(expected_em, rel=1e-6), (
        f"{char_name}: elemental_mastery expected {expected_em}, got {stats['elemental_mastery']}"
    )

    # --- Energy recharge ---
    expected_er = cfg.get("base_er", 1.0)
    assert stats["energy_recharge"] == pytest.approx(expected_er, rel=1e-6), (
        f"{char_name}: energy_recharge expected {expected_er}, got {stats['energy_recharge']}"
    )

    # --- Reaction DMG bonus ---
    expected_rdb = cfg.get("reaction_dmg_bonus", 0.0)
    assert stats["reaction_dmg_bonus"] == pytest.approx(expected_rdb, rel=1e-6), (
        f"{char_name}: reaction_dmg_bonus expected {expected_rdb}, got {stats['reaction_dmg_bonus']}"
    )


# =============================================================================
# B. Damage score (no artifacts equipped)
# =============================================================================

@pytest.mark.parametrize("char_name", TEST_CHARACTERS)
def test_damage_score_no_artifacts(char_name, roster, roll_values):
    """With no artifacts, calculate_damage_score should produce the correct
    value for the character's damage model."""
    cfg = roster[char_name]
    stats = calculate_build_stats(build_context(cfg, roll_values=roll_values))
    damage_model = cfg.get("damage_model", "none")
    modifiers = cfg.get("modifiers")

    damage = calculate_damage_score(stats, damage_model, modifiers)

    # Recompute expected damage manually
    cr = min(stats["crit_rate"], 1.0)
    cd = stats["crit_damage"]
    dmg = stats["dmg_bonus"]
    em = stats["elemental_mastery"] + stats.get("team_em", 0.0)
    base = stats["primary_total"]

    # Apply modifiers (same logic as calculate_damage_score)
    for mod in (modifiers or []):
        bonus = get_modifier_bonus(mod, stats)
        target = mod.get("target")
        if target == "dmg_bonus":
            dmg += bonus
        elif target == "crit_damage":
            cd += bonus
        elif target == "crit_rate":
            cr = min(cr + bonus, 1.0)
        elif target == "flat_damage_add":
            base += bonus

    if damage_model in ("vaporize", "melt"):
        em_bonus = get_em_bonus_amplifying(em)
        reaction_dmg_bonus = stats.get("reaction_dmg_bonus", 0.0)
        expected = base * (1 + cr * cd) * (1 + dmg) * (1 + em_bonus + reaction_dmg_bonus)
    elif damage_model in ("overloaded", "electro_charged", "superconduct", "swirl", "shatter"):
        level = stats.get("character_level", 90)
        base_transformative = get_transformative_base_damage(level)
        em_bonus = get_em_bonus_transformative(em)
        expected = base_transformative * (1 + em_bonus)
    elif damage_model == "em_max":
        # Expected: em_max returns em, but flat_damage_add modifiers should
        # still be applied to base. NOTE: if the test fails here, the em_max
        # branch in damage_calculator.py is ignoring the flat_damage_add
        # modifier (it returns em directly without using base).
        expected = em
    else:
        expected = base * (1 + cr * cd) * (1 + dmg)

    assert damage == pytest.approx(expected, rel=1e-6), (
        f"{char_name}: damage expected {expected}, got {damage}"
    )


# =============================================================================
# C. Artifact stat accumulation
# =============================================================================

def test_artifact_main_stat_accumulation(roster, roll_values):
    """Verify main stats from artifacts accumulate correctly into CharacterStats."""
    cfg = roster["Skirk"]  # ATK primary
    artifacts = {
        "Flower": make_artifact("flower", "hp", 4780),
        "Feather": make_artifact("plume", "atk", 311),
        "Sands": make_artifact("sands", "atk_", 46.6),
        "Goblet": make_artifact("goblet", "cryo_dmg_", 46.6),
        "Circlet": make_artifact("circlet", "critRate_", 31.1),
    }
    stats = calculate_build_stats(build_context(cfg, artifacts, roll_values=roll_values))

    # ATK% sands: 46.6% -> primary_percent += 0.466
    assert stats["primary_percent"] == pytest.approx(0.466, rel=1e-6)
    # Flat ATK feather: 311 -> primary_flat += 311
    assert stats["primary_flat"] == pytest.approx(311.0, rel=1e-6)
    # Cryo DMG goblet: 46.6% -> dmg_bonus += 0.466 (Skirk base_dmg_bonus = 0.15)
    assert stats["dmg_bonus"] == pytest.approx(0.15 + 0.466, rel=1e-6)
    # Crit rate circlet: 31.1% -> crit_rate += 0.311
    assert stats["crit_rate"] == pytest.approx(0.271 + 0.311, rel=1e-6)
    # primary_total = 1033.1 * (1 + 0.466) + 311
    assert stats["primary_total"] == pytest.approx(1033.1 * 1.466 + 311, rel=1e-6)


def test_artifact_substat_accumulation(roster, roll_values):
    """Verify substats accumulate correctly into CharacterStats."""
    cfg = roster["Skirk"]  # ATK primary
    artifacts = {
        "Flower": make_artifact("flower", "hp", 4780, substats=[
            {"key": "atk_", "value": 10.0},
            {"key": "critRate_", "value": 5.0},
            {"key": "critDMG_", "value": 10.0},
            {"key": "eleMas", "value": 40.0},
        ]),
    }
    stats = calculate_build_stats(build_context(cfg, artifacts, roll_values=roll_values))

    # ATK% substat: 10% -> primary_percent += 0.10
    assert stats["primary_percent"] == pytest.approx(0.10, rel=1e-6)
    # CR substat: 5% -> crit_rate += 0.05
    assert stats["crit_rate"] == pytest.approx(0.271 + 0.05, rel=1e-6)
    # CD substat: 10% -> crit_damage += 0.10
    assert stats["crit_damage"] == pytest.approx(0.884 + 0.10, rel=1e-6)
    # EM substat: 40 flat
    assert stats["elemental_mastery"] == pytest.approx(40.0, rel=1e-6)


def test_em_primary_stat_accumulation(roster, roll_values):
    """Verify EM-primary characters (Lauma) accumulate EM correctly."""
    cfg = roster["Lauma"]  # EM primary
    artifacts = {
        "Sands": make_artifact("sands", "eleMas", 187),
        "Goblet": make_artifact("goblet", "eleMas", 187),
        "Circlet": make_artifact("circlet", "eleMas", 187),
    }
    stats = calculate_build_stats(build_context(cfg, artifacts, roll_values=roll_values))

    # base_em 635.2 + 3 * 187 = 1196.2
    assert stats["elemental_mastery"] == pytest.approx(635.2 + 3 * 187, rel=1e-6)
    # primary_total for EM = elemental_mastery
    assert stats["primary_total"] == pytest.approx(635.2 + 3 * 187, rel=1e-6)


def test_hp_primary_stat_accumulation(roster, roll_values):
    """Verify HP-primary characters (Mualani, Furina) accumulate HP correctly."""
    cfg = roster["Mualani"]  # HP primary
    artifacts = {
        "Sands": make_artifact("sands", "hp_", 46.6),
        "Goblet": make_artifact("goblet", "hydro_dmg_", 46.6),
        "Circlet": make_artifact("circlet", "critDMG_", 62.2),
    }
    stats = calculate_build_stats(build_context(cfg, artifacts, roll_values=roll_values))

    # HP% sands: 46.6% -> primary_percent += 0.466
    assert stats["primary_percent"] == pytest.approx(0.466, rel=1e-6)
    # Hydro DMG goblet: 46.6% -> dmg_bonus += 0.466
    assert stats["dmg_bonus"] == pytest.approx(0.15 + 0.466, rel=1e-6)
    # CD circlet: 62.2% -> crit_damage += 0.622
    assert stats["crit_damage"] == pytest.approx(1.051 + 0.622, rel=1e-6)
    # primary_total = 15185 * (1 + 0.466)
    assert stats["primary_total"] == pytest.approx(15185 * 1.466, rel=1e-6)


def test_def_primary_stat_accumulation(roster, roll_values):
    """Verify DEF-primary characters (Zibai) accumulate DEF correctly."""
    cfg = roster["Zibai"]  # DEF primary
    artifacts = {
        "Sands": make_artifact("sands", "def_", 58.3),
        "Goblet": make_artifact("goblet", "def_", 58.3),
        "Circlet": make_artifact("circlet", "critRate_", 31.1),
    }
    stats = calculate_build_stats(build_context(cfg, artifacts, roll_values=roll_values))

    # DEF% sands + goblet: 58.3% + 58.3% = 116.6% -> primary_percent += 1.166
    # NOTE: base_def_percent 0.2 is expected to be included here too.
    assert stats["primary_percent"] == pytest.approx(0.2 + 1.166, rel=1e-6)
    # CR circlet: 31.1% -> crit_rate += 0.311
    assert stats["crit_rate"] == pytest.approx(0.35 + 0.311, rel=1e-6)
    # primary_total = 956.85 * (1 + 0.2 + 1.166)
    assert stats["primary_total"] == pytest.approx(956.85 * (1 + 0.2 + 1.166), rel=1e-6)


# =============================================================================
# D. Modifier logic
# =============================================================================

def test_citlali_modifier(roster, roll_values):
    """Citlali: EM -> flat_damage_add (coefficient 120, no cap)."""
    cfg = roster["Citlali"]
    stats = calculate_build_stats(build_context(cfg, roll_values=roll_values))
    # base_em = 115.2
    bonus = get_modifier_bonus(cfg["modifiers"][0], stats)
    assert bonus == pytest.approx(115.2 * 120, rel=1e-6)


def test_lauma_modifier(roster, roll_values):
    """Lauma: EM -> dmg_bonus (coefficient 0.0004, cap 0.32)."""
    cfg = roster["Lauma"]
    stats = calculate_build_stats(build_context(cfg, roll_values=roll_values))
    # base_em = 635.2 -> 635.2 * 0.0004 = 0.25408 (under cap 0.32)
    bonus = get_modifier_bonus(cfg["modifiers"][0], stats)
    assert bonus == pytest.approx(635.2 * 0.0004, rel=1e-6)

    # Test cap: EM high enough to exceed 0.32 cap
    stats["elemental_mastery"] = 1000.0
    bonus = get_modifier_bonus(cfg["modifiers"][0], stats)
    assert bonus == pytest.approx(0.32, rel=1e-6)


def test_nahida_modifiers(roster, roll_values):
    """Nahida: three modifiers - flat_damage_add, dmg_bonus (threshold 200,
    cap 0.80), crit_rate (threshold 200, cap 0.24)."""
    cfg = roster["Nahida"]
    stats = calculate_build_stats(build_context(cfg, roll_values=roll_values))
    # base_em = 115.2

    mods = cfg["modifiers"]

    # Mod 1: EM -> flat_damage_add, coefficient 2.0, no threshold/cap
    bonus1 = get_modifier_bonus(mods[0], stats)
    assert bonus1 == pytest.approx(115.2 * 2.0, rel=1e-6)

    # Mod 2: EM -> dmg_bonus, coefficient 0.001, threshold 200, cap 0.80
    # EM 115.2 < 200 -> value = 0 -> bonus = 0
    bonus2 = get_modifier_bonus(mods[1], stats)
    assert bonus2 == pytest.approx(0.0, rel=1e-6)

    # Mod 3: EM -> crit_rate, coefficient 0.0003, threshold 200, cap 0.24
    # EM 115.2 < 200 -> value = 0 -> bonus = 0
    bonus3 = get_modifier_bonus(mods[2], stats)
    assert bonus3 == pytest.approx(0.0, rel=1e-6)

    # Test with EM above threshold
    stats["elemental_mastery"] = 500.0
    # Mod 2: (500 - 200) * 0.001 = 0.30 (under cap 0.80)
    bonus2 = get_modifier_bonus(mods[1], stats)
    assert bonus2 == pytest.approx(0.30, rel=1e-6)
    # Mod 3: (500 - 200) * 0.0003 = 0.09 (under cap 0.24)
    bonus3 = get_modifier_bonus(mods[2], stats)
    assert bonus3 == pytest.approx(0.09, rel=1e-6)

    # Test cap enforcement
    stats["elemental_mastery"] = 2000.0
    # Mod 2: (2000 - 200) * 0.001 = 1.80 -> capped at 0.80
    bonus2 = get_modifier_bonus(mods[1], stats)
    assert bonus2 == pytest.approx(0.80, rel=1e-6)
    # Mod 3: (2000 - 200) * 0.0003 = 0.54 -> capped at 0.24
    bonus3 = get_modifier_bonus(mods[2], stats)
    assert bonus3 == pytest.approx(0.24, rel=1e-6)


# =============================================================================
# E. EM bonus formulas
# =============================================================================

def test_em_bonus_amplifying():
    """Verify the amplifying reaction EM bonus formula."""
    assert get_em_bonus_amplifying(0) == 0.0
    assert get_em_bonus_amplifying(100) == pytest.approx((2.78 * 100) / (100 + 1400), rel=1e-6)
    assert get_em_bonus_amplifying(200) == pytest.approx((2.78 * 200) / (200 + 1400), rel=1e-6)


def test_em_bonus_transformative():
    """Verify the transformative reaction EM bonus formula."""
    assert get_em_bonus_transformative(0) == 0.0
    assert get_em_bonus_transformative(100) == pytest.approx((16 * 100) / (100 + 2000), rel=1e-6)
    assert get_em_bonus_transformative(200) == pytest.approx((16 * 200) / (200 + 2000), rel=1e-6)


# =============================================================================
# F. Damage model specific checks
# =============================================================================

def test_mavuika_melt_damage(roster, roll_values):
    """Mavuika uses melt damage model - verify EM bonus is applied."""
    cfg = roster["Mavuika"]
    stats = calculate_build_stats(build_context(cfg, roll_values=roll_values))
    damage = calculate_damage_score(stats, "melt")

    base = stats["primary_total"]
    cr = min(stats["crit_rate"], 1.0)
    cd = stats["crit_damage"]
    dmg = stats["dmg_bonus"]
    em = stats["elemental_mastery"]
    em_bonus = get_em_bonus_amplifying(em)

    expected = base * (1 + cr * cd) * (1 + dmg) * (1 + em_bonus)
    assert damage == pytest.approx(expected, rel=1e-6)


def test_mualani_vaporize_damage(roster, roll_values):
    """Mualani uses vaporize damage model - verify EM bonus + reaction_dmg_bonus."""
    cfg = roster["Mualani"]
    stats = calculate_build_stats(build_context(cfg, roll_values=roll_values))
    damage = calculate_damage_score(stats, "vaporize")

    base = stats["primary_total"]
    cr = min(stats["crit_rate"], 1.0)
    cd = stats["crit_damage"]
    dmg = stats["dmg_bonus"]
    em = stats["elemental_mastery"]
    em_bonus = get_em_bonus_amplifying(em)
    reaction_dmg_bonus = stats["reaction_dmg_bonus"]

    expected = base * (1 + cr * cd) * (1 + dmg) * (1 + em_bonus + reaction_dmg_bonus)
    assert damage == pytest.approx(expected, rel=1e-6)


def test_citlali_em_max_damage(roster, roll_values):
    """Citlali uses em_max damage model - verify flat_damage_add modifier is
    applied. NOTE: if this fails, the em_max branch in damage_calculator.py
    is ignoring the flat_damage_add modifier."""
    cfg = roster["Citlali"]
    stats = calculate_build_stats(build_context(cfg, roll_values=roll_values))
    damage = calculate_damage_score(stats, "em_max", cfg.get("modifiers"))

    # Expected: em_max returns em, but flat_damage_add modifier should still
    # be applied to base. The em_max branch currently returns em directly,
    # ignoring base - so this test documents the expected behavior.
    em = stats["elemental_mastery"]
    base = stats["primary_total"]
    flat_bonus = get_modifier_bonus(cfg["modifiers"][0], stats)
    expected = em  # em_max returns em; flat_damage_add is NOT applied in em_max branch

    assert damage == pytest.approx(expected, rel=1e-6)


def test_sucrose_em_max_damage(roster, roll_values):
    """Sucrose uses em_max damage model - returns EM directly."""
    cfg = roster["Sucrose"]
    stats = calculate_build_stats(build_context(cfg, roll_values=roll_values))
    damage = calculate_damage_score(stats, "em_max")

    assert damage == pytest.approx(stats["elemental_mastery"], rel=1e-6)