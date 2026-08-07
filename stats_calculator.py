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

# Maps scaling stat names (from the scaling list) to the character_config
# base-key suffix.  Both "atk" (injected default) and "attack" (manual YAML)
# are supported.
_STAT_NAME_MAP = {
    "atk": "atk",
    "attack": "atk",
    "hp": "hp",
    "health": "hp",
    "hp_max": "hp",
    "def": "def",
    "defense": "def",
    "em": "em",
    "elemental_mastery": "em",
}

_EMPTY_DELTA = {
    # Existing (unchanged)
    "crit_rate": 0.0,
    "crit_damage": 0.0,
    "dmg_bonus": 0.0,
    "elemental_mastery": 0.0,
    "energy_recharge": 0.0,

    # NEW: Separate accumulators for every primary stat
    "atk_percent": 0.0,
    "hp_percent": 0.0,
    "def_percent": 0.0,
    "atk_flat": 0.0,
    "hp_flat": 0.0,
    "def_flat": 0.0,
}


def _get_main_stat_value(main_key: str, rarity: int) -> float:
    multiplier = 0.8 if rarity == 4 else 1.0
    return _MAIN_STAT_VALUES_5STAR.get(main_key, 0.0) * multiplier


def compute_artifact_delta(artifact, primary_stat: str = None) -> dict:
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
    calculate_build_stats: atk_percent, hp_percent, def_percent,
    atk_flat, hp_flat, def_flat, crit_rate, crit_damage, dmg_bonus,
    elemental_mastery, energy_recharge.

    The ``primary_stat`` argument is accepted for backward compatibility
    (optimizer.py still passes it) but is no longer used — all primary
    stat families are accumulated simultaneously.
    """
    d = dict(_EMPTY_DELTA)

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
                    if main_key.startswith("hp") or main_key.startswith("HP"):
                        d["hp_percent"] += main_val / 100.0
                    elif main_key.startswith("atk") or main_key.startswith("ATK"):
                        d["atk_percent"] += main_val / 100.0
                    elif main_key.startswith("def") or main_key.startswith("DEF"):
                        d["def_percent"] += main_val / 100.0
            else:
                # Flat stats
                if main_key.startswith("hp") or main_key.startswith("HP"):
                    d["hp_flat"] += main_val
                elif main_key.startswith("atk") or main_key.startswith("ATK"):
                    d["atk_flat"] += main_val
                elif main_key.startswith("def") or main_key.startswith("DEF"):
                    d["def_flat"] += main_val

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
            if sub_key.startswith("critRate"):
                d["crit_rate"] += sub_val / 100.0
            elif sub_key.startswith("critDMG"):
                d["crit_damage"] += sub_val / 100.0
            elif sub_key.startswith("enerRech"):
                d["energy_recharge"] += sub_val / 100.0
            elif "dmg" in sub_key:
                d["dmg_bonus"] += sub_val / 100.0
            elif sub_key.startswith("eleMas"):
                d["elemental_mastery"] += sub_val  # EM is not percent
            elif sub_key.startswith("hp") or sub_key.startswith("HP"):
                d["hp_percent"] += sub_val / 100.0
            elif sub_key.startswith("atk") or sub_key.startswith("ATK"):
                d["atk_percent"] += sub_val / 100.0
            elif sub_key.startswith("def") or sub_key.startswith("DEF"):
                d["def_percent"] += sub_val / 100.0
            # heal_ ignored
        else:
            if sub_key.startswith("eleMas"):
                d["elemental_mastery"] += sub_val
            elif sub_key.startswith("hp") or sub_key.startswith("HP"):
                d["hp_flat"] += sub_val
            elif sub_key.startswith("atk") or sub_key.startswith("ATK"):
                d["atk_flat"] += sub_val
            elif sub_key.startswith("def") or sub_key.startswith("DEF"):
                d["def_flat"] += sub_val

    return d


def combine_artifact_deltas(deltas, character_config: dict, team_context: dict = None) -> CharacterStats:
    """
    Combine precomputed per-artifact deltas (from compute_artifact_delta) plus
    character base stats and team context into a full CharacterStats block.

    This is the cheap, combo-specific part of the calculation - safe to call
    once per 5-piece combo since it's just summation, no per-artifact parsing.
    """
    team_context = team_context or {}

    stats = {
        "primary_base": 0.0,
        "primary_percent": 0.0,
        "primary_flat": 0.0,
        # Initialize percent accumulators from character base percent values
        # (e.g. base_def_percent, base_hp_percent) so that characters with
        # innate percent bonuses are handled correctly.
        "atk_percent": character_config.get("base_atk_percent", 0.0),
        "hp_percent": character_config.get("base_hp_percent", 0.0),
        "def_percent": character_config.get("base_def_percent", 0.0),
        "atk_flat": 0.0,
        "hp_flat": 0.0,
        "def_flat": 0.0,
        "crit_rate": character_config.get("base_crit_rate", 0.05),
        "crit_damage": character_config.get("base_crit_damage", 0.50),
        "dmg_bonus": character_config.get("base_dmg_bonus", 0.0),
        "elemental_mastery": character_config.get("base_em", 0.0),
        "energy_recharge": character_config.get("base_er", 1.0),
        "reaction_dmg_bonus": character_config.get("reaction_dmg_bonus", 0.0),
        "lunar_base_bonus": character_config.get("lunar_base_bonus", 0.0),
        "team_em": team_context.get("team_em", 0.0),
    }

    for d in deltas:
        stats["atk_percent"] += d["atk_percent"]
        stats["hp_percent"] += d["hp_percent"]
        stats["def_percent"] += d["def_percent"]
        stats["atk_flat"] += d["atk_flat"]
        stats["hp_flat"] += d["hp_flat"]
        stats["def_flat"] += d["def_flat"]
        stats["crit_rate"] += d["crit_rate"]
        stats["crit_damage"] += d["crit_damage"]
        stats["dmg_bonus"] += d["dmg_bonus"]
        stats["elemental_mastery"] += d["elemental_mastery"]
        stats["energy_recharge"] += d["energy_recharge"]

    # ---- Team context (unchanged from the original implementation) ----
    # Note: external_flat_stat is NOT added to primary_flat here - it's
    # folded into primary_total below and into primary_flat in the backward-
    # compat block at the end. Adding it here too would double-count it.
    stats["dmg_bonus"] += team_context.get("external_dmg_bonus", 0.0)
    stats["elemental_mastery"] += team_context.get("external_em", 0.0)

    # ---- Compute primary_total using scaling ----
    # Get scaling list (injected by config.py) or fallback to legacy primary_stat
    scaling = character_config.get("scaling")
    if not scaling:
        # Fallback: build scaling from primary_stat
        primary = character_config.get("primary_stat", "ATK").lower()
        scaling = [{"stat": primary, "weight": 1.0}]

    total = 0.0
    for item in scaling:
        stat_name = item.get("stat", "attack").lower()
        weight = item.get("weight", 1.0)

        # Normalize stat name to config key suffix
        normalized = _STAT_NAME_MAP.get(stat_name, stat_name)

        # EM is a flat stat, no base or percent
        if normalized == "em":
            total += stats["elemental_mastery"] * weight
        else:
            # ATK, HP, DEF
            base_key = f"base_{normalized}"
            base_val = character_config.get(base_key, 0.0)
            percent_key = f"{normalized}_percent"
            flat_key = f"{normalized}_flat"

            percent = stats.get(percent_key, 0.0)
            flat = stats.get(flat_key, 0.0)

            # Formula: base * (1 + percent) + flat
            stat_value = base_val * (1 + percent) + flat
            total += stat_value * weight

    # Add team external flat to total (was previously folded into primary_flat
    # before primary_total was computed in the old code).
    total += team_context.get("external_flat_stat", 0.0)

    stats["primary_total"] = total

    # ---- NEW: Add raw stat values for pipeline steps ----
    # These are the unweighted, actual stat values before applying scaling weights.
    # Useful for saturation caps and maximize_scaled_value steps.
    for stat_name in ["atk", "hp", "def"]:
        base_key = f"base_{stat_name}"
        base_val = character_config.get(base_key, 0.0)
        percent_key = f"{stat_name}_percent"
        flat_key = f"{stat_name}_flat"
        percent = stats.get(percent_key, 0.0)
        flat = stats.get(flat_key, 0.0)
        stats[f"raw_{stat_name}"] = base_val * (1 + percent) + flat

    stats["raw_em"] = stats.get("elemental_mastery", 0.0)

    # ---- NEW: Expose unbuffed base stats (no artifact %/flat applied) ----
    # For buff-strength characters (Bennett, Gorou, etc.) whose kit scales off
    # their own base ATK/HP/DEF rather than their fully-built stat. Distinct
    # from raw_atk/raw_hp/raw_def above, which include artifact contributions.
    stats["base_atk"] = character_config.get("base_atk", 0.0)
    stats["base_hp"] = character_config.get("base_hp", 0.0)
    stats["base_def"] = character_config.get("base_def", 0.0)

    # ---- Backward compatibility: set primary_percent/primary_flat for the
    # primary stat (highest weight) so downstream code that still reads these
    # keys gets sensible values. ----
    if not scaling:
        scaling = [{"stat": "attack", "weight": 1.0}]
    primary_item = max(scaling, key=lambda x: x.get("weight", 1.0))
    primary_name = _STAT_NAME_MAP.get(
        primary_item.get("stat", "attack").lower(),
        primary_item.get("stat", "attack").lower()
    )
    if primary_name == "em":
        stats["primary_percent"] = 0.0
        stats["primary_flat"] = stats["elemental_mastery"]
    else:
        stats["primary_percent"] = stats.get(f"{primary_name}_percent", 0.0)
        stats["primary_flat"] = stats.get(f"{primary_name}_flat", 0.0)
        # Add team external flat to primary_flat for backward compat
        stats["primary_flat"] += team_context.get("external_flat_stat", 0.0)

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

    deltas = [compute_artifact_delta(a) for a in artifacts_dict.values()]
    return combine_artifact_deltas(deltas, character_config, team_context)