from models import CharacterStats

def get_em_bonus_amplifying(em: float) -> float:
    if em <= 0:
        return 0.0
    return (2.78 * em) / (em + 1400)

def get_em_bonus_transformative(em: float) -> float:
    if em <= 0:
        return 0.0
    return (16 * em) / (em + 2000)

def get_transformative_base_damage(level: int) -> float:
    # Level 90 base is roughly 1730 for transformative reactions.
    # You can use a linear interpolation or lookup table.
    base_at_90 = 1730.0
    # Simplified scaling: multiply by (level/90)
    return base_at_90 * (level / 90)

def get_modifier_bonus(mod: dict, stats: CharacterStats) -> float:
    """
    Evaluate one kit-specific modifier against the current stat block.

    Example:
        {
            "source_stat": "elemental_mastery",
            "target": "crit_rate",
            "coefficient": 0.0003,
            "threshold": 200,
            "cap": 0.24
        }
    """
    coefficient = mod.get("coefficient", 0.0)

    value = stats.get(mod["source_stat"], 0)

    threshold = mod.get("threshold", 0)
    if threshold:
        value = max(0, value - threshold)

    raw = coefficient * value

    cap = mod.get("cap")
    return min(raw, cap) if cap is not None else raw


def calculate_damage_score(stats: CharacterStats, damage_model: str, modifiers: list | None = None) -> float:
    cr = min(stats['crit_rate'], 1.0)
    cd = stats['crit_damage']
    dmg = stats['dmg_bonus']
    em = stats['elemental_mastery'] + stats.get('team_em', 0.0)
    base = stats['primary_total']

    # Apply kit-specific modifiers (e.g. Citlali's EM->flat damage burst bonus)
    # before the reaction/multiplier branches run. This is the single place
    # character-specific "weirdness" lives - as data from roster.yaml, not
    # new elif branches here.
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
        reaction_dmg_bonus = stats.get('reaction_dmg_bonus', 0.0)
        damage = base * (1 + cr * cd) * (1 + dmg) * (1 + em_bonus + reaction_dmg_bonus)
    elif damage_model in ("overloaded", "electro_charged", "superconduct", "swirl", "shatter"):
        level = stats.get("character_level", 90)  # you'll need this
        base_transformative = get_transformative_base_damage(level)  # implement this
        em_bonus = get_em_bonus_transformative(em)
        damage = base_transformative * (1 + em_bonus) * 1 # Assuming enemy resistance doesn't matter for artifact rankings
    elif damage_model == "em_max":
        damage = em
    else:
        damage = base * (1 + cr * cd) * (1 + dmg)

    return damage