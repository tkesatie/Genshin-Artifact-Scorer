"""
Module: pipeline

Purpose:
Defines the evaluation pipeline engine. The optimizer no longer calls
damage_calculator directly. Instead, it constructs a pipeline of
registered evaluator steps (e.g., standard_damage, spread_reaction,
saturate, maximize_scaled_value, legacy_damage) and runs them sequentially.

This is the heart of the Strangler Fig migration—new characters will use
composable pipeline steps, while existing characters fall back to the
legacy_damage step until they are manually migrated.
"""

from dataclasses import dataclass, field
from typing import Dict, Any, List, Optional, Callable


@dataclass
class EvaluationContext:
    """State container passed between pipeline steps."""
    stats: Dict[str, float]          # CharacterStats dict, now includes raw_* keys
    current_score: float = 0.0       # The running value being transformed
    metadata: Dict[str, Any] = field(default_factory=dict)  # damage_model, modifiers, character_config


# Registry: maps step type string to a callable evaluator
EvaluatorRegistry: Dict[str, Callable] = {}


def register_evaluator(name: str):
    """Decorator to register a pipeline step evaluator."""
    def decorator(func: Callable):
        EvaluatorRegistry[name] = func
        return func
    return decorator


def run_pipeline(
    pipeline_steps: List[Dict[str, Any]],
    stats: Dict[str, float],
    metadata: Optional[Dict[str, Any]] = None
) -> float:
    """
    Execute the evaluation pipeline sequentially.

    Args:
        pipeline_steps: List of step dicts, each with a "type" key and optional "config".
        stats: The CharacterStats dict to pass through the pipeline.
        metadata: Additional context (damage_model, modifiers, etc.) for the steps.

    Returns:
        The final current_score after all steps have been applied.
    """
    ctx = EvaluationContext(stats=stats, metadata=metadata or {})
    for step in pipeline_steps:
        step_type = step.get("type")
        if step_type not in EvaluatorRegistry:
            raise ValueError(f"Unknown pipeline step: '{step_type}'. Available: {list(EvaluatorRegistry.keys())}")
        config = step.get("config", {})
        evaluator = EvaluatorRegistry[step_type]
        ctx = evaluator(ctx, config)
    return ctx.current_score


# Maps scaling/stat names to the normalized key suffix used in raw_* stats.
# Both "atk" (injected default) and "attack" (manual YAML) are supported.
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


# ===== Shared reaction/modifier helpers =====
# These were originally defined in damage_calculator.py. They're moved here so
# pipeline steps can use them without a circular import (damage_calculator
# imports from pipeline to register the legacy_damage step).

def get_em_bonus_amplifying(em: float) -> float:
    if em <= 0:
        return 0.0
    return (2.78 * em) / (em + 1400)


def get_em_bonus_transformative(em: float) -> float:
    if em <= 0:
        return 0.0
    return (16 * em) / (em + 2000)


def get_em_bonus_lunar(em: float) -> float:
    """Lunar reaction EM bonus (Nod-Krai's Lunar Reaction system).

    Unique scaling curve distinct from amplifying (2.78*EM/(EM+1400)) and
    standard transformative (16*EM/(EM+2000)).
    """
    if em <= 0:
        return 0.0
    return (6 * em) / (em + 2000)


def get_transformative_base_damage(level: int) -> float:
    # Level 90 base is roughly 1730 for transformative reactions.
    base_at_90 = 1730.0
    return base_at_90 * (level / 90)


def get_modifier_bonus(mod: dict, stats: Dict[str, float]) -> float:
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


# ===== REAL EVALUATORS =====

@register_evaluator("standard_damage")
def step_standard_damage(ctx: EvaluationContext, config: dict) -> EvaluationContext:
    """
    Calculates base damage from primary_total * talent_multiplier.
    Expects 'talent_multiplier' in config (default 1.0).
    """
    talent_multiplier = config.get("talent_multiplier", 1.0)
    ctx.current_score = ctx.stats.get("primary_total", 0.0) * talent_multiplier
    return ctx


@register_evaluator("personal_damage")
def step_personal_damage(ctx: EvaluationContext, config: dict) -> EvaluationContext:
    """
    Applies crit rate, crit damage, and damage bonus to the current score.
    Assumes ctx.current_score is the base damage (e.g., from standard_damage).
    """
    cr = min(ctx.stats.get("crit_rate", 0.05), 1.0)
    cd = ctx.stats.get("crit_damage", 0.5)
    dmg = ctx.stats.get("dmg_bonus", 0.0)
    ctx.current_score = ctx.current_score * (1 + cr * cd) * (1 + dmg)
    return ctx


@register_evaluator("spread_reaction")
def step_spread_reaction(ctx: EvaluationContext, config: dict) -> EvaluationContext:
    """
    Adds flat damage based on Elemental Mastery (Spread/Aggravate).
    Expects 'coefficient' in config (e.g., 1.0 for Spread, 0.8 for Aggravate).
    """
    coefficient = config.get("coefficient", 1.0)
    em = ctx.stats.get("raw_em", ctx.stats.get("elemental_mastery", 0.0))
    ctx.current_score = ctx.current_score + (em * coefficient)
    return ctx


@register_evaluator("saturate")
def step_saturate(ctx: EvaluationContext, config: dict) -> EvaluationContext:
    """
    Caps a raw stat at a given value.
    Expects 'stat' (e.g., "hp") and 'cap' (e.g., 55000) in config.
    Mutates ctx.stats so subsequent steps see the clamped value.
    """
    stat_key = config.get("stat")
    cap = config.get("cap")
    if stat_key and cap is not None:
        # Normalize stat name to match raw_* keys
        normalized = _STAT_NAME_MAP.get(stat_key.lower(), stat_key.lower())
        raw_key = f"raw_{normalized}"
        if raw_key in ctx.stats:
            ctx.stats[raw_key] = min(ctx.stats[raw_key], cap)
        elif stat_key in ctx.stats:
            ctx.stats[stat_key] = min(ctx.stats[stat_key], cap)
    return ctx


@register_evaluator("maximize_scaled_value")
def step_maximize_scaled_value(ctx: EvaluationContext, config: dict) -> EvaluationContext:
    """
    Computes the total scaled value from the character's scaling list.
    Reads 'scaling' from ctx.metadata['character_config'].
    Uses raw_* stats (raw_atk, raw_hp, raw_def, raw_em) to compute the weighted sum.
    This is used for characters like Nilou (HP buffer) or Bennett (base ATK buffer).
    """
    char_config = ctx.metadata.get("character_config", {})
    scaling = char_config.get("scaling", [{"stat": "attack", "weight": 1.0}])

    total = 0.0
    for item in scaling:
        stat_name = item.get("stat", "attack").lower()
        weight = item.get("weight", 1.0)

        # Normalize stat name to match raw_* keys
        normalized = _STAT_NAME_MAP.get(stat_name, stat_name)

        # Use raw_* stats if available
        raw_key = f"raw_{normalized}"
        if raw_key in ctx.stats:
            stat_value = ctx.stats[raw_key]
        elif normalized == "em":
            stat_value = ctx.stats.get("elemental_mastery", 0.0)
        elif normalized == "energy_recharge":
            # ER is stored as a fraction (e.g. 1.2 = 120%). Scale to a
            # percentage so it's comparable to flat ATK/HP/DEF values in
            # the weighted sum (e.g. Gorou's def + ER scaling).
            stat_value = ctx.stats.get("energy_recharge", 0.0) * 100.0
        else:
            # Fallback: try the stat directly
            stat_value = ctx.stats.get(normalized, 0.0)

        total += stat_value * weight

    ctx.current_score = total
    return ctx


@register_evaluator("apply_modifiers")
def step_apply_modifiers(ctx: EvaluationContext, config: dict) -> EvaluationContext:
    """
    Applies kit-specific modifiers to ctx.stats so subsequent steps see them.
    Reads 'modifiers' from config, or falls back to ctx.metadata['modifiers'].

    Supported targets: dmg_bonus, crit_damage, crit_rate, flat_damage_add.
    flat_damage_add is added to primary_total (the base damage).
    """
    modifiers = config.get("modifiers") or ctx.metadata.get("modifiers", [])
    for mod in modifiers:
        bonus = get_modifier_bonus(mod, ctx.stats)
        target = mod.get("target")
        if target == "dmg_bonus":
            ctx.stats["dmg_bonus"] = ctx.stats.get("dmg_bonus", 0.0) + bonus
        elif target == "crit_damage":
            ctx.stats["crit_damage"] = ctx.stats.get("crit_damage", 0.0) + bonus
        elif target == "crit_rate":
            ctx.stats["crit_rate"] = min(ctx.stats.get("crit_rate", 0.05) + bonus, 1.0)
        elif target == "flat_damage_add":
            ctx.stats["primary_total"] = ctx.stats.get("primary_total", 0.0) + bonus
    return ctx


@register_evaluator("reaction_damage")
def step_reaction_damage(ctx: EvaluationContext, config: dict) -> EvaluationContext:
    """
    Applies amplifying (vaporize/melt), transformative, or lunar reaction damage.
    Expects 'reaction' in config: 'vaporize'|'melt'|'overloaded'|'electro_charged'|
    'superconduct'|'swirl'|'shatter'|'lunar_crystallize'.

    For amplifying reactions, multiplies ctx.current_score (the base damage from
    standard_damage/personal_damage) by (1 + EM bonus + reaction_dmg_bonus).
    For transformative reactions, overwrites ctx.current_score with the
    transformative base damage * (1 + EM bonus).
    For lunar reactions (lunar_crystallize), multiplies ctx.current_score by
    (1 + lunar_base_bonus) * multiplier * (1 + lunar EM bonus + reaction_dmg_bonus).
    Unlike transformative reactions, lunar reactions can crit, so the
    personal_damage step's crit/dmg multipliers apply on top of the base damage.
    Optional config: 'multiplier' (default 1.6 direct, use 0.96 for indirect).
    """
    reaction = config.get("reaction", "none")
    em = ctx.stats.get("elemental_mastery", 0.0) + ctx.stats.get("team_em", 0.0)

    if reaction in ("vaporize", "melt"):
        em_bonus = get_em_bonus_amplifying(em)
        reaction_dmg_bonus = ctx.stats.get("reaction_dmg_bonus", 0.0)
        ctx.current_score = ctx.current_score * (1 + em_bonus + reaction_dmg_bonus)
    elif reaction in ("overloaded", "electro_charged", "superconduct", "swirl", "shatter"):
        level = ctx.stats.get("character_level", 90)
        base_transformative = get_transformative_base_damage(level)
        em_bonus = get_em_bonus_transformative(em)
        ctx.current_score = base_transformative * (1 + em_bonus)
    elif reaction == "lunar_crystallize":
        em_bonus = get_em_bonus_lunar(em)
        reaction_dmg_bonus = ctx.stats.get("reaction_dmg_bonus", 0.0)
        lunar_base_bonus = ctx.stats.get("lunar_base_bonus", 0.0)
        multiplier = config.get("multiplier", 1.6)
        ctx.current_score = (
            ctx.current_score
            * (1 + lunar_base_bonus)
            * multiplier
            * (1 + em_bonus + reaction_dmg_bonus)
        )
    return ctx


@register_evaluator("em_max")
def step_em_max(ctx: EvaluationContext, config: dict) -> EvaluationContext:
    """
    Returns Elemental Mastery as the score (for EM-scaling supports like
    Sucrose/Citlali). Matches the legacy damage_model 'em_max' branch.
    """
    em = ctx.stats.get("elemental_mastery", 0.0) + ctx.stats.get("team_em", 0.0)
    ctx.current_score = em
    return ctx


@register_evaluator("hp_max")
def step_hp_max(ctx: EvaluationContext, config: dict) -> EvaluationContext:
    """
    Returns primary_total as the score (for HP-scaling characters like Kokomi).
    Matches the legacy damage_model 'hp_max' branch.
    """
    ctx.current_score = ctx.stats.get("primary_total", 0.0)
    return ctx
