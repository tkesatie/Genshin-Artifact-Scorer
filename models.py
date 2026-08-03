# models.py
from typing import Dict, List, Optional

class CharacterStats:
    """Final stat totals used for damage calculations."""
    primary_base: float = 0.0      # e.g. ATK base (character + weapon)
    primary_percent: float = 0.0   # sum of all % bonuses (0.80 = 80%)
    primary_flat: float = 0.0      # sum of all flat bonuses
    primary_total: float = 0.0     # computed: base*(1+%) + flat

    crit_rate: float = 0.0         # 0.65 = 65%
    crit_damage: float = 0.0       # 1.80 = 180%
    dmg_bonus: float = 0.0         # 0.466 = 46.6%
    elemental_mastery: float = 0.0
    energy_recharge: float = 0.0   # 1.80 = 180%

class DamageModifiers:
    """Set‑effect multipliers that apply to specific attack types."""
    burst_multiplier: float = 1.0
    normal_multiplier: float = 1.0
    plunge_multiplier: float = 1.0
    skill_multiplier: float = 1.0

class BuildContext:
    """Input envelope for the stat calculator."""
    character_config: dict
    artifacts: Dict[str, dict]          # slotKey -> Artifact (5 pieces)
    team_context: dict                  # from rules.yaml / teams.yaml
    roll_values: dict                   # from roll_values.yaml
    damage_model: str                   # "amplifying" | "transformative" | "none"