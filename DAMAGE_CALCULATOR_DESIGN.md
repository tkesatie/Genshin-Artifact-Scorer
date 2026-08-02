# Genshin Artifact Scorer - Future Damage Calculator Design

## Overview

The current artifact scorer evaluates artifacts based on useful stat rolls, expected value, and account progression.

A future damage calculator will extend the system by evaluating the actual impact of artifact upgrades within a configured team context.

The goal is not to build a complete combat simulator. The goal is to answer:

> "How much does this artifact improve this character in the team where I actually use them?"

The damage calculator should provide context for artifact decisions while keeping assumptions configurable and transparent.

---

# Design Philosophy

The damage calculator should use a hybrid approach:

- Character calculations determine personal damage contribution.
- Team context provides the external factors that materially affect value.

The system should avoid attempting to simulate every frame of a rotation, while still accounting for:

- Teammate buffs.
- Artifact set buffs.
- Weapon buffs.
- Energy requirements.
- Reaction assumptions.
- Rotation assumptions where necessary.

---

# Why Not Pure Character Calculation?

A character-only calculator is simpler but has major limitations.

Many character values depend heavily on their team:

- ER requirements.
- External buffs.
- Resistance shred.
- Elemental reactions.
- Damage bonuses.
- Attack buffs.

Example:

Furina's optimal artifact stats are different depending on whether she is:

- Solo Hydro.
- Double Hydro.
- Using Favonius support.
- Supporting a Freeze team.

A character-only calculator would require inaccurate assumptions.

---

# Why Not Full Team Simulation?

A complete team simulator is significantly more complex.

A true simulation would need:

- Exact rotations.
- Skill timing.
- Particle generation.
- Energy funneling.
- Enemy behavior.
- Buff uptime.
- Reaction timing.
- Character-specific mechanics.

This creates a large maintenance burden whenever new characters or mechanics are introduced.

The goal of this project is artifact optimization, not combat simulation.

---

# Hybrid Team Context Model

Characters are evaluated individually, but inside a configured team environment.

Example:

```yaml
teams:
  Skirk Freeze:
    members:
      - Skirk
      - Furina
      - Escoffier
      - Shenhe

    assumptions:
      rotation_length: 20

The team context provides information that affects the character calculation:

Active buffs.
Expected energy environment.
Reaction assumptions.
Damage bonuses.
Resistance effects.

The calculator does not simulate the rotation itself unless a future feature requires it.

Development Phases
Phase 1: Character Stat Targets

Before calculating damage, introduce configurable stat targets.

Examples:

ER requirements.
Crit targets.
HP/ATK/DEF goals.
EM targets.

This allows the scorer to understand when a useful stat is no longer valuable.

Example:

Furina

Current:
HP: 38,000
Crit: 72/210
ER: 224%

Targets:
HP: 40,000
Crit: 70/200
ER: 170%

Result:
ER exceeds target.
Future artifacts should prioritize HP/Crit.
Energy Recharge Handling
Problem

ER is fundamentally different from offensive stats.

Offensive stats follow:

Artifact stats
      ↓
Character stats
      ↓
Damage calculation

ER depends on the environment:

Artifact stats
      ↓
Energy generation
      ↓
Team particles
      ↓
Rotation assumptions
      ↓
Burst availability
      ↓
Actual damage

A character does not have a single universal ER requirement.

ER Implementation
Initial Approach: Manual Targets

ER targets should initially be manually configured.

Example:

Furina:
  targets:
    ER: 170

This matches the existing artifact scorer philosophy of character-specific configuration.

The user defines the intended team/playstyle.

Future: Team Overrides

Team contexts can override default character targets.

Example:

teams:
  Skirk Freeze:
    characters:
      Furina:
        ER_target: 150

This allows the same character to have different requirements in different teams.

Future: ER Estimation

A more advanced system could estimate ER requirements from:

Burst cost.
Particle generation.
Teammate energy contribution.
Favonius effects.
Rotation length.
Enemy particle assumptions.

This should remain an estimation tool, not a full energy simulator.

Damage Calculation Output

The goal is not only to show damage numbers.

The calculator should improve artifact recommendations.

Current:

Artifact A:
+1.2 expected useful rolls

Future:

Artifact A:
+1.2 expected useful rolls

Estimated impact:
+4.5% team damage

Recommendation:
High priority
Relationship With Artifact Scoring

The damage calculator should not replace artifact scoring.

Artifact scoring remains useful because it is:

Fast.
Easy to understand.
Character independent.
Useful even without detailed assumptions.

Damage calculation should be an additional layer that answers:

"How much does this upgrade actually matter?"

Long-Term Goal

The completed system should combine:

Artifact quality evaluation.
Character stat targets.
Team context.
Damage impact estimation.

The final question the tool should answer:

"Given my account, teams, and current artifacts, what upgrade gives me the largest improvement?"