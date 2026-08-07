"""
Module: damage_calculator

Purpose:
Legacy damage-formula helpers. The evaluation pipeline (pipeline.py) is now
the single source of truth for damage scoring - the optimizer, dashboard
(character_scoring), and tests all route through run_pipeline. This module
retains only the shared EM/modifier helpers that pipeline.py re-exports for
backward compatibility; the old calculate_damage_score / legacy_damage_step
were removed once every roster character had an explicit evaluation_pipeline.
"""

from pipeline import (
    get_em_bonus_amplifying,
    get_em_bonus_transformative,
    get_em_bonus_lunar,
    get_transformative_base_damage,
    get_modifier_bonus,
)

__all__ = [
    "get_em_bonus_amplifying",
    "get_em_bonus_transformative",
    "get_em_bonus_lunar",
    "get_transformative_base_damage",
    "get_modifier_bonus",
]
