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

class DamageModifiers(TypedDict):
    burst_multiplier: float
    normal_multiplier: float
    plunge_multiplier: float
    skill_multiplier: float

class BuildContext(TypedDict):
    character_config: Dict[str, Any]
    artifacts: Dict[str, Artifact]
    team_context: Dict[str, float]
    roll_values: Dict[str, float]
    damage_model: str