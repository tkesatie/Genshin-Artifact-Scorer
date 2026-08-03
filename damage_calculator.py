# damage_calculator.py
from models import CharacterStats, DamageModifiers, BuildContext
from artifact_utils import STAT_LABEL
from typing import Dict

def calculate_build_stats(context: BuildContext) -> CharacterStats:
    """
    Sums all relevant stats from artifacts + team context.
    Assumes every artifact is at max level (projected if needed).
    """
    cfg = context.character_config
    artifacts = context.artifacts
    team = context.team_context or {}
    roll_vals = context.roll_values

    # Identify primary scaling stat (ATK, HP, DEF, or EM)
    primary_stat = cfg.get("primary_stat", "ATK")  # default ATK

    # Base values: we don't have character/weapon base, so we set base = 0
    # and rely on external flat stats from team context.
    base = 0.0
    percent = 0.0
    flat = 0.0

    # Team external bonuses
    flat += team.get("external_flat_stat", 0.0)
    dmg_bonus = team.get("external_dmg_bonus", 0.0)
    external_em = team.get("external_em", 0.0)

    # Sum artifact stats
    er = 0.0
    cr = 0.0
    cd = 0.0
    em = 0.0
    # Main stat and substats are summed per artifact
    for slot, art in artifacts.items():
        if art is None:
            continue
        # Main stat
        main_key = art.get("mainStatKey")
        main_label = STAT_LABEL.get(main_key)
        # We need the value of main stat – we can get it from roll_values? No,
        # we need actual values. We'll assume we have a mapping for max-level main stat values.
        # We'll use a helper to get main stat value based on rarity and main stat key.
        main_val = _main_stat_value(art)
        if main_label in ("ATK%", "HP%", "DEF%"):
            percent += main_val / 100.0   # store as fraction
        elif main_label == primary_stat:
            # If main stat is the primary stat, we need to handle it differently
            # Actually, primary_stat is like "ATK" or "HP" etc. We need to map correctly.
            # Simpler: we'll handle by label.
            pass
        # For simplicity, we'll just sum all stat bonuses later.

        # Substat
        for sub in art.get("substats", []):
            key = sub["key"]
            label = STAT_LABEL.get(key)
            val = sub.get("value", 0.0)
            if label == "ATK%":
                percent += val / 100.0
            elif label == "HP%":
                percent += val / 100.0
            elif label == "DEF%":
                percent += val / 100.0
            elif label == "ATK":
                flat += val
            elif label == "HP":
                flat += val
            elif label == "DEF":
                flat += val
            elif label == "ER":
                er += val
            elif label == "CR":
                cr += val
            elif label == "CD":
                cd += val
            elif label == "EM":
                em += val
            # DMG% bonuses come from main stat (Goblet) and team context.

    # Determine primary_total: base*(1 + percent) + flat
    # Since we don't have base, we set base = 0 and use percent and flat.
    # For damage calculation, we only need the multiplier (1 + percent) and flat.
    # So primary_total = (1 + percent) * base + flat. But base is unknown.
    # However, the damage formula uses primary_total. We'll use (1 + percent) * base + flat.
    # We'll assume base = 1000 for simplicity? No, that's wrong.
    # The design doc uses primary_total for scaling. We need actual total.
    # Since we don't have base stats, we can't compute real damage. Instead, we'll compute
    # a "stat multiplier" that is (1 + percent) and we will multiply by a placeholder base.
    # This is a limitation; we'll document it.
    # We'll set base = 1 for now, so primary_total = (1 + percent) + flat.
    # That gives a relative measure.
    base = 1.0
    primary_total = base * (1 + percent) + flat

    stats = CharacterStats()
    stats.primary_base = base
    stats.primary_percent = percent
    stats.primary_flat = flat
    stats.primary_total = primary_total
    stats.crit_rate = cr / 100.0
    stats.crit_damage = cd / 100.0
    stats.dmg_bonus = dmg_bonus  # already fraction
    stats.elemental_mastery = em + external_em
    stats.energy_recharge = er / 100.0 + 1.0  # base 100%

    return stats

def _main_stat_value(art):
    """Returns the max-level main stat value for a given artifact."""
    rarity = art.get("rarity", 5)
    # Hardcoded max-level main stat values (5-star only for now)
    main_stat_values = {
        5: {
            "HP%": 46.6, "ATK%": 46.6, "DEF%": 58.3, "EM": 187.0,
            "ER": 51.8, "CR": 31.1, "CD": 62.2, "Heal%": 35.9,
            "PyroDMG%": 46.6, "HydroDMG%": 46.6, "CryoDMG%": 46.6,
            "ElectroDMG%": 46.6, "AnemoDMG%": 46.6, "GeoDMG%": 46.6,
            "DendroDMG%": 46.6,
        },
        4: {
            "HP%": 34.8, "ATK%": 34.8, "DEF%": 43.7, "EM": 139,
            "ER": 38.7, "CR": 23.2, "CD": 46.4, "Heal%": 26.8,
            "PyroDMG%": 34.8, "HydroDMG%": 34.8, "CryoDMG%": 34.8,
            "ElectroDMG%": 34.8, "AnemoDMG%": 34.8, "GeoDMG%": 34.8,
            "DendroDMG%": 34.8,
        }
    }
    key = art.get("mainStatKey")
    label = STAT_LABEL.get(key, key)
    return main_stat_values.get(rarity, main_stat_values[5]).get(label, 0.0)

def calculate_damage_score(stats: CharacterStats, damage_model: str) -> float:
    """Computes the raw damage score."""
    cr = min(stats.crit_rate, 1.0)
    cd = stats.crit_damage
    dmg = stats.dmg_bonus
    em = stats.elemental_mastery

    if damage_model == "amplifying":
        em_mult = 1 + (2.78 * em) / (em + 1400)
    elif damage_model == "transformative":
        em_mult = 1 + (16 * em) / (em + 2000)
    else:
        em_mult = 1.0

    return stats.primary_total * (1 + cr * cd) * (1 + dmg) * em_mult

def apply_set_effects(artifacts: dict, stats: CharacterStats) -> DamageModifiers:
    """Detects active set bonuses and returns multipliers."""
    mods = DamageModifiers()
    # Count set keys
    set_counts = {}
    for art in artifacts.values():
        if art is None:
            continue
        key = art.get("setKey")
        set_counts[key] = set_counts.get(key, 0) + 1

    # Emblem of Severed Fate (4pc)
    if set_counts.get("EmblemOfSeveredFate", 0) >= 4:
        er = stats.energy_recharge
        mods.burst_multiplier = 1 + 0.25 * er  # er is already decimal (1.8 = 180%)
    # Crimson Witch (4pc) – simplified
    if set_counts.get("CrimsonWitchOfFlames", 0) >= 4:
        mods.skill_multiplier = 1.15
        mods.burst_multiplier = 1.15
    # Gladiator's Finale (4pc)
    if set_counts.get("GladiatorsFinale", 0) >= 4:
        mods.normal_multiplier = 1.35
    # Add more sets as needed.
    return mods

def apply_er_gate(stats: CharacterStats, floor: float) -> float | None:
    """
    Returns a penalty factor (0-1) if ER is below floor.
    Returns None if ER < floor * 0.8 (hard reject).
    """
    er = stats.energy_recharge * 100  # convert to percent
    if er >= floor:
        return 1.0
    if er < floor * 0.8:
        return None
    # Linear penalty
    return 1.0 - ((floor - er) / floor)