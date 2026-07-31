"""
Genshin artifact farming scorer.

Purpose:
    This module serves as the central orchestrator for scoring and analyzing Genshin Impact artifacts based on user exports. It processes character configurations, scoring rules, and roll values to generate a comprehensive dashboard of recommendations and insights.

Responsibilities:
    1. **Artifact Parsing**: Reads and parses JSON exports from Irminsul/GOOD.
    2. **Scoring Calculations**: Computes scores for individual characters and domains based on artifact attributes and configuration rules.
    3. **Benchmarking**: Identifies potential benchmark candidates and expected values for artifacts.
    4. **Recommendation Logic**: Generates actionable recommendations based on the scoring results.
    5. **HTML Rendering**: Outputs the final analysis as an interactive HTML dashboard.

Architectural Role:
    This module acts as the orchestration layer of the application, coordinating interactions between various components such as artifact parsing, scoring logic, and rendering utilities. It is expected to be used by higher-level modules or scripts that require a complete scoring workflow.

Intended Dependencies:
    - **Configuration Files**: Relies on `roster.yaml`, `rules.yaml`, and `roll_values.yaml` for character roles/builds, scoring rules, and substat roll-value references.
    - **Utility Modules**:
        - `artifact_utils`: For parsing artifact exports.
        - `bench`: For benchmarking calculations.
        - `character_scoring`: For character-specific scoring logic.
        - `config`: For loading configuration files.
        - `recommendations`: For generating recommendations.
        - `render_html`: For rendering the final HTML output.

Boundaries:
    - **Presentation Layer**: This module does not handle user interface elements directly. It focuses on data processing and analysis, leaving presentation to the `render_html` module.
    - **Utility Functions**: While this module uses utility functions from other modules, it should not contain low-level utility logic itself. Such logic belongs in dedicated utility modules.

Public API:
    - `main()`: The primary entry point for executing the scoring workflow. Accepts command-line arguments for input JSON export and output HTML file.
"""

#!/usr/bin/env python3
"""
Genshin artifact farming scorer.

Usage:
    python3 score.py <good_export.json> [--out dashboard.html]

Reads:
    roster.yaml        - character role/build config (edit this as your roster changes)
    rules.yaml          - thresholds, adjustments, domain weighting (rarely needs edits)
    roll_values.yaml     - substat roll-value reference table

Writes:
    dashboard.html (or whatever --out points to) - open it in any browser.
"""
import argparse
import json
from pathlib import Path

from artifact_utils import parse_good_export
from bench import bench_candidates_lookup, bench_expected_lookup, find_bench_potential
from character_scoring import score_character, score_domains
from config import load_configs
from recommendations import build_recommendations
from render_html import render_html


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("good_export", help="Path to your Irminsul/GOOD JSON export")
    ap.add_argument("--out", default="dashboard.html")
    args = ap.parse_args()

    roster, rules, roll_values = load_configs()
    good_json = json.loads(Path(args.good_export).read_text(encoding="utf-8"))

    by_char = parse_good_export(good_json, roster)
    bench_results = find_bench_potential(good_json, roster, rules, roll_values)
    bench_lookup = bench_expected_lookup(bench_results)
    bench_candidates = bench_candidates_lookup(bench_results)

    char_results = []
    for name, cfg in roster.items():
        artifacts = by_char.get(name, {})
        char_results.append(score_character(name, cfg, artifacts, rules, roll_values, bench_lookup, bench_candidates))

    domain_results = score_domains(char_results, rules)
    recommendations = build_recommendations(bench_results, char_results)
    render_html(char_results, domain_results, recommendations, args.out)


if __name__ == "__main__":
    main()
