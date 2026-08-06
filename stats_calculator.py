from models import BuildContext, CharacterStats

# ---- Main-stat value table (unchanged from the original implementation) ----
_MAIN_STAT_VALUES_5STAR = {
    "hp": 4780, "hp_": 46.6,
    "atk": 311, "atk_": 46.6,
    "def": 203, "def_": 58.3,      # corrected from 85.3
    "enerRech_": 51.8, "enerRech": 51.8,
    "eleMas": 187, "eleMas_": 187,
    "critRate_": 31.1, "critRate": 31.1,
    "critDMG_": 62.2, "critDMG": 62.2,
    "pyro_dmg_": 46.6, "pyro_dmg": 46.6,
    "hydro_dmg_": 46.6, "hydro_dmg": 46.6,
    "cryo_dmg_": 46.6, "cryo_dmg": 46.6,
    "electro_dmg_": 46.6, "electro_dmg": 46.6,
    "anemo_dmg_": 46.6, "anemo_dmg": 46.6,
    "geo_dmg_": 46.6, "geo_dmg": 46.6,
    "dendro_dmg_": 46.6, "dendro_dmg": 46.6,
    "heal_": 35.9, "heal": 35.9,
}

_PERCENT_KEYWORDS = ("critRate", "critDMG", "enerRech", "pyro_dmg", "hydro_dmg",
                     "cryo_dmg", "electro_dmg", "anemo_dmg", "geo_dmg", "dendro_dmg",
                     "heal", "hp_", "atk_", "def_", "eleMas_")

_EMPTY_DELTA = {
    "primary_percent": 0.0,
    "primary_flat": 0.0,
    "crit_rate": 0.0,
    "crit_damage": 0.0,
    "dmg_bonus": 0.0,
    "elemental_mastery": 0.0,
    "energy_recharge": 0.0,
}


def _get_main_stat_value(main_key: str, rarity: int) -> float:
    multiplier = 0.8 if rarity == 4 else 1.0
    return _MAIN_STAT_VALUES_5STAR.get(main_key, 0.0) * multiplier


def compute_artifact_delta(artifact, primary_stat: str) -> dict:
    """
    Compute one artifact's contribution to the additive stat pool (main stat
    + all substats), independent of any other artifact in the build.

    This is exactly the math `add_main_stat`/`add_substat` used to perform
    inline inside calculate_build_stats's per-build loop - pulled out here
    because it doesn't depend on which other 4 artifacts are in the build.
    Callers evaluating many candidate builds (e.g. the Monte Carlo optimizer,
    which re-evaluates thousands of 5-piece combinations per simulation) can
    call this ONCE per candidate artifact per simulation, then reuse the
    result across every combo that candidate appears in, instead of
    re-deriving it from scratch inside calculate_build_stats for every combo.

    Returns a dict with the same delta keys as the internal accumulator in
    calculate_build_stats: primary_percent, primary_flat, crit_rate,
    crit_damage, dmg_bonus, elemental_mastery, energy_recharge.
    """
    d = dict(_EMPTY_DELTA)
    primary_lower = primary_stat.lower()

    if isinstance(artifact, dict):
        main_key = artifact.get("mainStatKey")
        main_val = artifact.get("mainStatValue", 0.0)
        substats = artifact.get("substats", [])
        rarity = artifact.get("rarity", 5)
    else:
        main_key = getattr(artifact, "mainStatKey", None)
        main_val = getattr(artifact, "mainStatValue", 0.0)
        substats = getattr(artifact, "substats", [])
        rarity = getattr(artifact, "rarity", 5)

    # ---- Main stat ----
    if main_key:
        if main_val is None or main_val == 0:
            main_val = _get_main_stat_value(main_key, rarity)

        if main_key.startswith("eleMas"):
            d["elemental_mastery"] += main_val
        else:
            is_percent = main_key.endswith("_") or any(
                main_key.startswith(k) for k in _PERCENT_KEYWORDS
            )
            if is_percent:
                if main_key.startswith("critRate"):
                    d["crit_rate"] += main_val / 100.0
                elif main_key.startswith("critDMG"):
                    d["crit_damage"] += main_val / 100.0
                elif main_key.startswith("enerRech"):
                    d["energy_recharge"] += main_val / 100.0
                elif "dmg" in main_key or main_key.endswith("_dmg_"):
                    d["dmg_bonus"] += main_val / 100.0
                elif main_key.startswith("heal"):
                    pass  # Heal% doesn't affect damage; ignore
                else:
                    # primary stat percent (ATK%, HP%, DEF%)
                    if main_key in (f"{primary_lower}_", primary_lower):
                        d["primary_percent"] += main_val / 100.0
            else:
                # Flat stats
                if main_key == primary_lower:
                    d["primary_flat"] += main_val

    # ---- Substats ----
    for sub in substats:
        if isinstance(sub, dict):
            sub_key = sub.get("key")
            sub_val = sub.get("value", 0.0)
        else:
            sub_key = getattr(sub, "key", None)
            sub_val = getattr(sub, "value", 0.0)
        if not sub_key:
            continue

        if sub_key.endswith("_"):
            if sub_key == f"{primary_lower}_":
                d["primary_percent"] += sub_val / 100.0
            elif sub_key.startswith("critRate") or sub_key.startswith("critDMG"):
                d["crit_rate" if "critRate" in sub_key else "crit_damage"] += sub_val / 100.0
            elif sub_key.startswith("enerRech"):
                d["energy_recharge"] += sub_val / 100.0
            elif "dmg" in sub_key:
                d["dmg_bonus"] += sub_val / 100.0
            elif sub_key.startswith("eleMas"):
                d["elemental_mastery"] += sub_val  # EM is not percent
            # heal_ ignored
        else:
            if sub_key == primary_lower:
                d["primary_flat"] += sub_val
            elif sub_key == "eleMas":
                d["elemental_mastery"] += sub_val

    return d


def combine_artifact_deltas(deltas, character_config: dict, team_context: dict = None) -> CharacterStats:
    """
    Combine precomputed per-artifact deltas (from compute_artifact_delta) plus
    character base stats and team context into a full CharacterStats block.

    This is the cheap, combo-specific part of the calculation - safe to call
    once per 5-piece combo since it's just summation, no per-artifact parsing.
    """
    team_context = team_context or {}
    primary_stat = character_config.get("primary_stat", "ATK")
    base_percent_key = f"base_{primary_stat.lower()}_percent"

    stats = {
        "primary_base": 0.0,
        "primary_percent": character_config.get(base_percent_key, 0.0),
        "primary_flat": 0.0,
        "crit_rate": character_config.get("base_crit_rate", 0.05),
        "crit_damage": character_config.get("base_crit_damage", 0.50),
        "dmg_bonus": character_config.get("base_dmg_bonus", 0.0),
        "elemental_mastery": character_config.get("base_em", 0.0),
        "energy_recharge": character_config.get("base_er", 1.0),
        "reaction_dmg_bonus": character_config.get("reaction_dmg_bonus", 0.0),
        "team_em": team_context.get("team_em", 0.0),
    }

    for d in deltas:
        stats["primary_percent"] += d["primary_percent"]
        stats["primary_flat"] += d["primary_flat"]
        stats["crit_rate"] += d["crit_rate"]
        stats["crit_damage"] += d["crit_damage"]
        stats["dmg_bonus"] += d["dmg_bonus"]
        stats["elemental_mastery"] += d["elemental_mastery"]
        stats["energy_recharge"] += d["energy_recharge"]

    # ---- Team context (unchanged from the original implementation) ----
    stats["primary_flat"] += team_context.get("external_flat_stat", 0.0)
    stats["dmg_bonus"] += team_context.get("external_dmg_bonus", 0.0)
    stats["elemental_mastery"] += team_context.get("external_em", 0.0)

    # ---- Compute primary total ----
    if primary_stat == "EM":
        primary_total = stats["elemental_mastery"]
    else:
        base_key = f"base_{primary_stat.lower()}"
        base_val = character_config.get(base_key, 0.0)
        primary_total = base_val * (1 + stats["primary_percent"]) + stats["primary_flat"]

    stats["primary_total"] = primary_total
    return stats


def calculate_build_stats(context: BuildContext) -> CharacterStats:
    """
    Unchanged public entry point - same signature, same output, for every
    existing caller. Internally now just computes each artifact's delta and
    combines them, so this one implementation is the single source of truth
    for both the simple (call-per-build) and optimizer (precompute-once,
    reuse-across-combos) usage patterns.
    """
    character_config = context['character_config']
    artifacts_dict = context['artifacts']
    team_context = context.get('team_context', {})
    primary_stat = character_config.get("primary_stat", "ATK")

    deltas = [compute_artifact_delta(a, primary_stat) for a in artifacts_dict.values()]
    return combine_artifact_deltas(deltas, character_config, team_context)