from models import BuildContext, CharacterStats

def calculate_build_stats(context: BuildContext) -> CharacterStats:
    character_config = context['character_config']
    artifacts_dict = context['artifacts']          # slot -> artifact (dict)
    team_context = context.get('team_context', {})
    primary_stat = character_config.get("primary_stat", "ATK")  # ATK, HP, DEF, EM

    # Start with universal base stats
    stats = {
        "primary_base": 0.0,
        "primary_percent": 0.0,
        "primary_flat": 0.0,
        "crit_rate": character_config.get("base_crit_rate", 0.05),
        "crit_damage": character_config.get("base_crit_damage", 0.50),
        "dmg_bonus": character_config.get("base_dmg_bonus", 0.0),
        "elemental_mastery": character_config.get("base_em", 0.0),
        "energy_recharge": character_config.get("base_er", 0.0)
    }

    # ---- Helper: get main stat value from key and rarity ----
    def _get_main_stat_value(main_key: str, rarity: int) -> float:
        # 5-star max values (both underscore and non-underscore variants)
        values_5 = {
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
        multiplier = 0.8 if rarity == 4 else 1.0
        return values_5.get(main_key, 0.0) * multiplier

    # ---- Helper: add main stat ----
    def add_main_stat(main_key, main_val, rarity=5):
        if not main_key:
            return
        # If main_val is missing, compute it
        if main_val is None or main_val == 0:
            main_val = _get_main_stat_value(main_key, rarity)

        # EM main stat is flat regardless of naming (GOOD's key is "eleMas",
        # no trailing underscore) - handle it before the percent/flat split
        # below, since is_percent's "eleMas_" check doesn't match "eleMas"
        # and was silently dropping EM main stats (Goblet/Circlet) entirely.
        if main_key.startswith("eleMas"):
            stats["elemental_mastery"] += main_val
            return

        # Determine if it's a percent stat (ends with _ or known percent key)
        percent_keywords = ("critRate", "critDMG", "enerRech", "pyro_dmg", "hydro_dmg",
                            "cryo_dmg", "electro_dmg", "anemo_dmg", "geo_dmg", "dendro_dmg",
                            "heal", "hp_", "atk_", "def_", "eleMas_")
        is_percent = main_key.endswith("_") or any(main_key.startswith(k) for k in percent_keywords)

        if is_percent:
            # Percent main stats
            if main_key.startswith("critRate"):
                stats["crit_rate"] += main_val / 100.0
            elif main_key.startswith("critDMG"):
                stats["crit_damage"] += main_val / 100.0
            elif main_key.startswith("enerRech"):
                stats["energy_recharge"] += main_val / 100.0
            elif "dmg" in main_key or main_key.endswith("_dmg_"):
                stats["dmg_bonus"] += main_val / 100.0
            elif main_key.startswith("heal"):
                # Heal% doesn't affect damage; ignore
                pass
            elif main_key.startswith("eleMas"):
                stats["elemental_mastery"] += main_val  # EM is not percent
            else:
                # primary stat percent (ATK%, HP%, DEF%)
                if main_key in (f"{primary_stat.lower()}_", primary_stat.lower()):
                    stats["primary_percent"] += main_val / 100.0
        else:
            # Flat stats
            if main_key == primary_stat.lower():
                stats["primary_flat"] += main_val

    # ---- Helper: add substat ----
    def add_substat(sub_key, sub_val):
        if not sub_key:
            return

        # Percent stats end with "_"
        if sub_key.endswith("_"):
            # Primary stat percent
            if sub_key == f"{primary_stat.lower()}_":
                stats["primary_percent"] += sub_val / 100.0
            # Other percent stats (CR, CD, ER, DMG%, EM)
            elif sub_key.startswith("critRate") or sub_key.startswith("critDMG"):
                stats["crit_rate" if "critRate" in sub_key else "crit_damage"] += sub_val / 100.0
            elif sub_key.startswith("enerRech"):
                stats["energy_recharge"] += sub_val / 100.0
            elif "dmg" in sub_key:
                stats["dmg_bonus"] += sub_val / 100.0
            elif sub_key.startswith("eleMas"):
                stats["elemental_mastery"] += sub_val  # EM is not percent
            # heal_ ignored
        else:
            # Flat stats
            if sub_key == primary_stat.lower():
                stats["primary_flat"] += sub_val
            elif sub_key == "eleMas":
                stats["elemental_mastery"] += sub_val

    # ---- Process artifacts ----
    for artifact in artifacts_dict.values():
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

        if main_key is not None:
            add_main_stat(main_key, main_val, rarity)

        for sub in substats:
            if isinstance(sub, dict):
                sub_key = sub.get("key")
                sub_val = sub.get("value", 0.0)
            else:
                sub_key = getattr(sub, "key", None)
                sub_val = getattr(sub, "value", 0.0)
            if sub_key is not None:
                add_substat(sub_key, sub_val)

    # ---- Team context ----
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

    return {
        "primary_base": stats["primary_base"],
        "primary_percent": stats["primary_percent"],
        "primary_flat": stats["primary_flat"],
        "primary_total": primary_total,
        "crit_rate": stats["crit_rate"],
        "crit_damage": stats["crit_damage"],
        "dmg_bonus": stats["dmg_bonus"],
        "elemental_mastery": stats["elemental_mastery"],
        "energy_recharge": stats["energy_recharge"]
    }