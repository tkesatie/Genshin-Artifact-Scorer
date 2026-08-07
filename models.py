from typing import List, Dict, Optional, Any, TypedDict

class Artifact(TypedDict):
    slotKey: str
    setKey: str
    level: int
    rarity: int
    mainStatKey: str
    location: Optional[str]
    substats: List[Dict[str, float]]
    unactivatedSubstats: List[Dict[str, float]]
    lock: bool
    totalRolls: int

class CharacterStats(TypedDict):
    primary_base: float
    primary_percent: float
    primary_flat: float
    primary_total: float
    crit_rate: float
    crit_damage: float
    dmg_bonus: float
    elemental_mastery: float
    energy_recharge: float
    reaction_dmg_bonus: float
    lunar_base_bonus: float
    team_em: float

class DamageModifiers(TypedDict):
    burst_multiplier: float
    normal_multiplier: float
    plunge_multiplier: float
    skill_multiplier: float

class BuildContext(TypedDict):
    character_config: Dict[str, Any]  # Contains scaling and evaluation_pipeline keys
    artifacts: Dict[str, Artifact]
    team_context: Dict[str, float]
    roll_values: Dict[str, float]


class CharacterConfig(TypedDict, total=False):
    """Enhanced character configuration dict.

    New fields (Strangler Fig migration):
        scaling: List[Dict[str, float]]
            Each entry: {"stat": <str>, "weight": <float>}
        evaluation_pipeline: List[Dict[str, str | Dict]]
            Each entry: {"type": <str>, ...}
    """
    scaling: List[Dict[str, float]]
    evaluation_pipeline: List[Dict[str, Any]]
    # ... other existing fields like useful_stats, main_stats, set, etc.
