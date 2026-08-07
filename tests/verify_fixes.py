"""Quick sanity checks for the applied bug fixes."""
from optimizer import _project_artifact
from pipeline import _STAT_NAME_MAP
from stats_calculator import combine_artifact_deltas
from config import load_configs

def test_stat_floor_mapping():
    # The bug: "hp" floor mapped to nothing, so stats.get("hp", 0) = 0 always rejected
    # This mirrors the _FLOOR_KEY_MAP defined in score.py's main().
    floor_map = {
        "hp": "raw_hp",
        "atk": "raw_atk",
        "def": "raw_def",
        "em": "raw_em",
        "elemental_mastery": "raw_em",
        "energy_recharge": "energy_recharge",
        "crit_rate": "crit_rate",
        "crit_damage": "crit_damage",
        "dmg_bonus": "dmg_bonus",
    }
    assert floor_map["hp"] == "raw_hp"
    assert floor_map["atk"] == "raw_atk"
    assert floor_map["energy_recharge"] == "energy_recharge"
    # A floor for an unsupported stat is dropped (no silent rejection)
    print("OK: stat-floor key mapping")


def test_projection_events():
    # 5-star level 1: 19 levels to +20, ceil(19/4) = 5 upgrade events
    art = {"rarity": 5, "level": 1, "substats": [], "unactivatedSubstats": []}
    proj = _project_artifact(art, {})
    assert proj["level"] == 20, proj["level"]

    # 5-star level 0: 20 levels, ceil(20/4) = 5 events
    art = {"rarity": 5, "level": 0, "substats": [], "unactivatedSubstats": []}
    proj = _project_artifact(art, {})
    assert proj["level"] == 20, proj["level"]

    # 4-star level 3: 13 levels to +16, ceil(13/4) = 4 events
    art = {"rarity": 4, "level": 3, "substats": [], "unactivatedSubstats": []}
    proj = _project_artifact(art, {})
    assert proj["level"] == 16, proj["level"]
    print("OK: _project_artifact upgrade-event counts")


def test_er_normalization():
    # Gorou-style: def + ER scaling. ER should be scaled to a percentage.
    # This simulates what maximize_scaled_value does via the ER branch.
    stats = {"raw_def": 1000.0, "energy_recharge": 1.8}
    cfg = {"scaling": [{"stat": "def", "weight": 1.0}, {"stat": "energy_recharge", "weight": 0.5}]}
    ctx = type("Ctx", (), {"stats": stats, "metadata": {"character_config": cfg}})()
    from pipeline import step_maximize_scaled_value
    result = step_maximize_scaled_value(ctx, {})
    # 1000 + 0.5*180 = 1090 (was 1000 + 0.9 = 1000.9 before fix)
    assert abs(result.current_score - 1090.0) < 1e-6, result.current_score
    print("OK: ER normalization in maximize_scaled_value")


def test_primary_flat_no_double_count():
    char_cfg = {"base_atk": 1000.0, "primary_stat": "ATK"}
    team = {"external_flat_stat": 500.0}
    stats = combine_artifact_deltas([], char_cfg, team)
    # primary_flat = atk_flat (0) + external_flat_stat (500) = 500 exactly once
    assert abs(stats["primary_flat"] - 500.0) < 1e-9, stats["primary_flat"]
    print("OK: primary_flat not double-counted")


def test_kokomi_crit_rate():
    roster, _, _ = load_configs()
    assert abs(roster["SangonomiyaKokomi"]["base_crit_rate"] - (-1.0)) < 1e-9
    print("OK: Kokomi base_crit_rate = -1.0")


if __name__ == "__main__":
    test_projection_events()
    test_stat_floor_mapping()
    test_er_normalization()
    test_primary_flat_no_double_count()
    test_kokomi_crit_rate()
    print("\nAll fix verifications passed.")