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
import json
import sys
import argparse
import yaml
from pathlib import Path
from collections import defaultdict

HERE = Path(__file__).parent

SLOT_MAP = {
    "flower": "Flower", "plume": "Feather", "sands": "Sands",
    "goblet": "Goblet", "circlet": "Circlet",
}

STAT_LABEL = {
    "critRate_": "CR", "critDMG_": "CD", "atk_": "ATK%", "hp_": "HP%",
    "def_": "DEF%", "enerRech_": "ER", "eleMas": "EM",
    "hp": "HP", "atk": "ATK", "def": "DEF", "heal_": "Heal%",
}


def load_configs():
    roster = yaml.safe_load((HERE / "roster.yaml").read_text())
    rules = yaml.safe_load((HERE / "rules.yaml").read_text())
    roll_values = yaml.safe_load((HERE / "roll_values.yaml").read_text())
    return roster, rules, roll_values


def stat_pool_adjustment(rules, effective_pool):
    """Approximate-match against stat_pool_adjustment, like the sheet's VLOOKUP(...,TRUE)."""
    buckets = sorted(rules["stat_pool_adjustment"], key=lambda b: b["stat_count"])
    chosen = buckets[0]
    for b in buckets:
        if b["stat_count"] <= effective_pool:
            chosen = b
        else:
            break
    return chosen["good_adj"], chosen["excellent_adj"]


def compute_thresholds(rules, usage, role, slot, effective_pool, char_name):
    key = f"{usage}|{role}"
    base = rules["base_thresholds"][key]
    pool_good_adj, pool_exc_adj = stat_pool_adjustment(rules, effective_pool)
    slot_adj = rules["slot_adjustment"][slot]
    override = rules["character_overrides"].get(char_name, {})

    good = (base["good"] + pool_good_adj + slot_adj["good_adj"]
            + override.get("good_adj", 0))
    excellent = (base["excellent"] + pool_exc_adj + slot_adj["excellent_adj"]
                 + override.get("excellent_adj", 0))
    return good, excellent


def roll_count_for_artifact(artifact, useful_stats, roll_values, rarity):
    """Estimate total useful rolls on this artifact.

    Real substats only ever land on one of ~4 discrete per-roll values, so the
    true roll count for any single substat is always a whole number. Dividing
    by the average roll value gives a noisy estimate (e.g. 1.89 instead of 2)
    because a roll can land below or above that average - so each substat's
    estimate is rounded to the nearest whole roll before summing, rather than
    left as a raw fraction.
    """
    table = roll_values["five_star"] if rarity >= 5 else roll_values["four_star"]
    total = 0
    for sub in artifact.get("substats", []):
        key = sub.get("key")
        val = sub.get("value", 0)
        label = STAT_LABEL.get(key)
        if label and label in useful_stats and key in table and table[key] > 0:
            total += round(val / table[key])
    return total


def effective_useful_pool(main_stat_key, useful_stats):
    main_label = STAT_LABEL.get(main_stat_key)
    pool = len(useful_stats)
    if main_label and main_label in useful_stats:
        pool -= 1
    return pool


def score_character(char_name, cfg, artifacts_by_slot, rules, roll_values):
    usage, role = cfg["usage"], cfg["role"]
    useful_stats = [str(s) for s in cfg["useful_stats"]]
    slots_result = {}
    for slot in ["Flower", "Feather", "Sands", "Goblet", "Circlet"]:
        art = artifacts_by_slot.get(slot)
        if art is None:
            slots_result[slot] = {
                "status": "Missing", "roll_status": "Fail",
                "roll_count": 0, "good": None, "excellent": None,
            }
            continue
        rarity = art.get("rarity", 5)
        eff_pool = effective_useful_pool(art.get("mainStatKey"), useful_stats)
        good, excellent = compute_thresholds(rules, usage, role, slot, eff_pool, char_name)
        rc = roll_count_for_artifact(art, useful_stats, roll_values, rarity)
        roll_status = "Pass" if rc >= good else "Fail"
        if rc < good:
            status = "Needs Work"
        elif rc < excellent:
            status = "Good"
        else:
            status = "Excellent"
        slots_result[slot] = {
            "status": status, "roll_status": roll_status,
            "roll_count": round(rc, 2), "good": good, "excellent": excellent,
        }

    completion = sum(1 for s in slots_result.values() if s["status"] not in ("Needs Work", "Missing"))
    excellent_pieces = sum(1 for s in slots_result.values() if s["status"] == "Excellent")
    needs_work = [slot for slot, s in slots_result.items() if s["status"] in ("Needs Work", "Missing")]

    base = rules["base_thresholds"][f"{usage}|{role}"]
    if needs_work:
        char_status = "Farming"
    elif excellent_pieces >= base["luxury_excellent"]:
        char_status = "Luxury"
    elif excellent_pieces >= base["finished_excellent"]:
        char_status = "Finished"
    else:
        char_status = "Usable"

    if usage == "Active" and char_status == "Farming":
        tier = 1
    elif usage == "IT Only" and char_status == "Farming":
        tier = 2
    elif usage == "Active" and char_status == "Finished":
        tier = 3
    elif usage == "IT Only" and char_status == "Finished":
        tier = 4
    else:
        tier = 5
    score = 1000 - (tier * 100 + completion * 10 + excellent_pieces)

    return {
        "name": char_name, "usage": usage, "role": role, "domain": cfg.get("domain"),
        "status": char_status, "completion": completion, "excellent_pieces": excellent_pieces,
        "needs_work": needs_work, "score": score, "slots": slots_result,
        "luxury_target": base["luxury_excellent"],
    }


def score_domains(char_results, rules):
    weights = rules["domain_scoring"]
    domains = defaultdict(lambda: {"characters": [], "score": 0.0, "active": 0, "it_only": 0})
    for r in char_results:
        d = r["domain"]
        if d is None or d == "None":
            d = "None"
        entry = domains[d]
        entry["characters"].append(r["name"])
        w = weights["active_weight"] if r["usage"] == "Active" else weights["it_only_weight"]
        entry["score"] += (5 - r["completion"]) * w
        if r["status"] != "Luxury":
            entry["score"] += max(0, r["luxury_target"] - r["excellent_pieces"]) * weights["finished_polish_weight"] * w
        if r["usage"] == "Active":
            entry["active"] += 1
        else:
            entry["it_only"] += 1
    return dict(domains)


def parse_good_export(good_json, roster):
    """Group equipped artifacts by (character, slot). Only equipped artifacts are scored."""
    by_char = defaultdict(dict)
    for art in good_json.get("artifacts", []):
        loc = art.get("location")
        if not loc or loc not in roster:
            continue
        slot = SLOT_MAP.get(art.get("slotKey"))
        if slot is None:
            continue
        by_char[loc][slot] = art
    return by_char


STATUS_COLOR = {
    "Farming": "#e05a4e", "Usable": "#e0a94e", "Finished": "#4e8ee0",
    "Luxury": "#4ec97a", "Needs Work": "#e05a4e", "Good": "#e0a94e",
    "Excellent": "#4ec97a", "Missing": "#999999",
}


def render_html(char_results, domain_results, out_path):
    char_results_sorted = sorted(char_results, key=lambda r: -r["score"])
    domain_sorted = sorted(domain_results.items(), key=lambda kv: -kv[1]["score"])

    def badge(text):
        color = STATUS_COLOR.get(text, "#999")
        return f'<span style="background:{color};color:#fff;padding:2px 8px;border-radius:10px;font-size:12px;">{text}</span>'

    rows = []
    for r in char_results_sorted:
        slot_html = " ".join(
            f'<span title="{slot}: {s["roll_count"]} rolls (need {s["good"]}/{s["excellent"]})">{badge(s["status"])}</span>'
            for slot, s in r["slots"].items()
        )
        rows.append(f"""
        <tr>
          <td>{r['name']}</td>
          <td>{r['usage']}</td>
          <td>{r['role']}</td>
          <td>{badge(r['status'])}</td>
          <td>{r['completion']}/5</td>
          <td>{r['excellent_pieces']}</td>
          <td>{slot_html}</td>
          <td>{r['domain']}</td>
          <td>{round(r['score'],1)}</td>
        </tr>""")

    domain_rows = []
    for name, d in domain_sorted:
        domain_rows.append(f"""
        <tr>
          <td>{name}</td>
          <td>{', '.join(d['characters'])}</td>
          <td>{round(d['score'],1)}</td>
          <td>{d['active']}</td>
          <td>{d['it_only']}</td>
        </tr>""")

    html = f"""<!DOCTYPE html>
<html><head><meta charset="utf-8"><title>Artifact Farming Dashboard</title>
<style>
body {{ font-family: -apple-system, Segoe UI, sans-serif; background:#1a1a1a; color:#eee; padding:24px; }}
h1, h2 {{ font-weight: 600; }}
table {{ border-collapse: collapse; width: 100%; margin-bottom: 32px; }}
th, td {{ padding: 8px 12px; text-align: left; border-bottom: 1px solid #333; font-size: 14px; }}
th {{ cursor: pointer; background:#242424; position: sticky; top:0; }}
tr:hover {{ background: #242424; }}
</style></head>
<body>
<h1>Artifact Farming Dashboard</h1>
<h2>Characters (sorted by farming priority)</h2>
<table id="charTable">
<thead><tr><th>Character</th><th>Usage</th><th>Role</th><th>Status</th><th>Completion</th>
<th>Excellent</th><th>Slots</th><th>Domain</th><th>Score</th></tr></thead>
<tbody>{''.join(rows)}</tbody>
</table>

<h2>Domains (sorted by farming priority)</h2>
<table id="domainTable">
<thead><tr><th>Domain</th><th>Characters</th><th>Score</th><th>Active</th><th>IT Only</th></tr></thead>
<tbody>{''.join(domain_rows)}</tbody>
</table>

<script>
document.querySelectorAll('table').forEach(table => {{
  const headers = table.querySelectorAll('th');
  headers.forEach((th, idx) => {{
    th.addEventListener('click', () => {{
      const rows = Array.from(table.querySelectorAll('tbody tr'));
      const asc = th.dataset.asc !== 'true';
      headers.forEach(h => h.dataset.asc = '');
      th.dataset.asc = asc;
      rows.sort((a, b) => {{
        const av = a.children[idx].innerText, bv = b.children[idx].innerText;
        const an = parseFloat(av), bn = parseFloat(bv);
        if (!isNaN(an) && !isNaN(bn)) return asc ? an - bn : bn - an;
        return asc ? av.localeCompare(bv) : bv.localeCompare(av);
      }});
      rows.forEach(r => table.querySelector('tbody').appendChild(r));
    }});
  }});
}});
</script>
</body></html>"""
    Path(out_path).write_text(html)
    print(f"Wrote {out_path}")


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("good_export", help="Path to your Irminsul/GOOD JSON export")
    ap.add_argument("--out", default="dashboard.html")
    args = ap.parse_args()

    roster, rules, roll_values = load_configs()
    good_json = json.loads(Path(args.good_export).read_text())

    by_char = parse_good_export(good_json, roster)

    char_results = []
    for name, cfg in roster.items():
        artifacts = by_char.get(name, {})
        char_results.append(score_character(name, cfg, artifacts, rules, roll_values))

    domain_results = score_domains(char_results, rules)
    render_html(char_results, domain_results, args.out)


if __name__ == "__main__":
    main()