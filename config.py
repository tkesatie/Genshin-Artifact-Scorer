"""
Module: config

Purpose:
This module is responsible for loading configuration data required by the Artifact Scorer Project. It reads YAML files containing roster, rules, and roll values, which are essential for artifact parsing, scoring calculations, EV (Expected Value) calculations, and recommendation logic.

Responsibilities:
1. Configuration Loading: Load and parse YAML configuration files.
2. Data Provisioning: Provide access to loaded configuration data to other modules in the project.
3. Default Injection: Fill missing optional fields with safe defaults to avoid downstream KeyError.

Architectural Role:
This module serves as a utility layer within the overall application. It is expected to be used by higher-level modules that require configuration settings for their operations, such as `score.py` and `test.py`.

Intended Dependencies:
- `pathlib`: For handling file paths.
- `yaml`: For parsing YAML files.

Boundaries:
This module should not contain any business logic or application-specific calculations. It is solely responsible for loading and providing configuration data. Any processing of the loaded data should be handled by other modules.

Public API:
- load_configs(): Function to load and return the roster, rules, and roll values from their respective YAML files.
"""

from pathlib import Path

import yaml


HERE = Path(__file__).parent


def load_configs():
    roster = yaml.safe_load((HERE / "roster.yaml").read_text(encoding="utf-8")) or {}
    rules = yaml.safe_load((HERE / "rules.yaml").read_text(encoding="utf-8")) or {}
    roll_values = yaml.safe_load((HERE / "roll_values.yaml").read_text(encoding="utf-8")) or {}
    bases = yaml.safe_load((HERE / "character_bases.yaml").read_text(encoding="utf-8")) or {}

    print(f"Number of chracters: {len(roster)}")

    for name, base_cfg in bases.items():
        if name in roster:
            roster[name].update(base_cfg)
        else:
            roster[name] = base_cfg

    # Inject defaults for optional fields so downstream modules don't have to check existence.
    # This is purely for convenience; the validation module will still warn about invalid values.
    for name, cfg in roster.items():
        if isinstance(cfg, dict):
            # --- Scaling injection ---
            # If 'scaling' is missing, build it from 'primary_stat' (weight 1.0).
            # 'primary_stat' remains the source of truth for a character's main
            # scaling stat when an explicit weighted 'scaling' list isn't given.
            if "scaling" not in cfg:
                primary = cfg.get("primary_stat", "ATK").lower()
                # Map 'em' properly; EM uses the base_em value and flat additions
                cfg["scaling"] = [{"stat": primary, "weight": 1.0}]

            # --- Legacy default ---
            # Default primary_stat to "ATK" if missing
            cfg.setdefault("primary_stat", "ATK")

    return roster, rules, roll_values
