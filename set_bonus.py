# File: set_bonus.py

from models import Artifact, CharacterStats, DamageModifiers

# Common set keys for now
SET_KEYS = {
    "EmblemOfSeveredFate": "EmblemOfSeveredFate",
    "CrimsonWitchOfFlames": "CrimsonWitchOfFlames",
    "GladiatorsFinale": "GladiatorsFinale",
    "ViridescentVenerer": "ViridescentVenerer"
}

def apply_set_effects(artifacts: dict[str, Artifact], stats: CharacterStats | None = None) -> DamageModifiers:
    mods = {
        "burst_multiplier": 1.0,
        "normal_multiplier": 1.0,
        "plunge_multiplier": 1.0,
        "skill_multiplier": 1.0
    }
    
    set_counts = {}
    for artifact in artifacts.values():
        if artifact.setKey in set_counts:
            set_counts[artifact.setKey] += 1
        else:
            set_counts[artifact.setKey] = 1
    
    for set_key, count in set_counts.items():
        if count >= 4:
            if set_key == "EmblemOfSeveredFate" and stats is not None:
                mods["burst_multiplier"] = 1 + 0.25 * stats.energy_recharge
            elif set_key == "CrimsonWitchOfFlames":
                mods["burst_multiplier"] = 1.15
                mods["skill_multiplier"] = 1.15
            elif set_key == "GladiatorsFinale":
                mods["normal_multiplier"] = 1.35
    
    return mods