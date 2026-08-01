"""
Module: render_html

Purpose:
This module is responsible for generating an HTML report that visualizes artifact farming data. It provides a comprehensive dashboard to help users understand the status of their artifacts, domains, and recommended swaps.

Responsibilities:
1. **HTML Generation**: Create an HTML file that includes tables summarizing character artifact statuses, domain scores, and recommended swaps.
2. **Data Formatting**: Format various data points such as substats, badges, and verdicts into a user-friendly HTML format.
3. **Sorting and Filtering**: Implement sorting functionality for the tables based on different columns to help users prioritize their farming activities.

Architectural Role:
This module serves as part of the presentation layer in the application. It is expected to be used by higher-level modules that process artifact data and generate reports. The module does not contain business logic or orchestration; it focuses solely on transforming structured data into an HTML format for display.

Intended Dependencies:
- **Dependencies**: This module relies on Python's standard library, specifically the `pathlib` module for file path operations.
- **Project Modules**: It assumes the existence of other modules that provide the data structures (`char_results`, `domain_results`, `recommendations`) used in its functions. These modules are responsible for collecting and processing artifact information.

Boundaries:
- This module should not handle data collection or business logic. Its primary role is to format and present data.
- Logic related to artifact scoring, EV calculations, and recommendation generation should be handled by other modules.
- The module does not perform any file I/O operations beyond writing the generated HTML file; all data input should come from external sources.

Public API:
- `render_html(char_results, domain_results, recommendations, out_path)`: This is the main function that generates the HTML report. It takes in processed artifact data and an output path to write the HTML file.
"""

from pathlib import Path


STATUS_COLOR = {
    "Farming": "#e05a4e", "Usable": "#e0a94e", "Finished": "#4e8ee0",
    "Luxury": "#4ec97a", "Needs Work": "#e05a4e", "Good": "#e0a94e",
    "Excellent": "#4ec97a", "Missing": "#999999",
}

INVENTORY_COLOR = {
    "KEEP": "#4ec97a", "REVIEW": "#e0a94e",
    "SANCTIFY_ELIXIR": "#4e8ee0", "SAFE_STRONGBOX": "#999999",
}

def substat_display_for(artifact, useful_stats):
    """Render an artifact's active + hidden substats, bolding whatever
    counts as useful for the given stat list."""
    from artifact_utils import STAT_LABEL
    parts = []
    for sub in artifact.get("substats", []):
        label = STAT_LABEL.get(sub.get("key"), sub.get("key"))
        text = f"{label}+{sub.get('value')}"
        if label in useful_stats:
            text = f"<b>{text}</b>"
        parts.append(text)
    for sub in artifact.get("unactivatedSubstats", []):
        label = STAT_LABEL.get(sub.get("key"), sub.get("key"))
        text = f"{label}+{sub.get('value')}"
        if label in useful_stats:
            text = f"<b>{text}</b>"
        parts.append(f'{text} <span style="font-size:10px;color:#888;">(hidden)</span>')
    return ", ".join(parts) if parts else "—"


def format_substats_html(substats, rarity=5, level=0):
    """Formats substats list.
    - Bold if useful
    - Italics if unactivated / potential 4th line
    """
    formatted = []

    for label, val, is_useful in substats:
        text = f"{label}+{val}" if val else label
        if is_useful:
            text = f"<b>{text}</b>"
        formatted.append(text)

    if rarity == 5 and level < 4 and len(substats) == 3:
        formatted.append("<i>+1 Hidden Line</i>")

    return ", ".join(formatted)


def render_html(char_results, domain_results, recommendations, out_path,
                 flex_results=None, inventory_results=None):
    flex_results = flex_results or []
    inventory_results = inventory_results or []

    char_results_sorted = sorted(char_results, key=lambda r: -r["score"])
    domain_sorted = sorted(domain_results.items(), key=lambda kv: -kv[1]["score"])

    def badge(text):
        color = STATUS_COLOR.get(text, "#999")
        return f'<span style="background:{color};color:#fff;padding:2px 8px;border-radius:10px;font-size:12px;">{text}</span>'

    rows = []
    for r in char_results_sorted:
        slot_html = " ".join(
            f'<span title="{slot}: {s["roll_count"]} rolls equipped (need {s["good"]}/{s["excellent"]}); best bench candidate: EV {s["bench_expected"]}, max {s["bench_ceiling"]}">{badge(s["status"])}{"↑" if s["upgradeable"] else ""}</span>'
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
        <td>{r['upgrades_good']} / {r['upgrades_excellent']}</td>
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

    VERDICT_COLOR = {
        "Major Breakthrough": "#9c27b0",
        "Patch / Fix": "#e05a4e",
        "Luxury Upgrade": "#4ec97a",
        "Minor Polish": "#e0a94e",
        "Dead end": "#999999"
    }

    def vbadge(text):
        color = VERDICT_COLOR.get(text, "#999")
        return f'<span style="background:{color};color:#fff;padding:2px 8px;border-radius:10px;font-size:12px;">{text}</span>'

    def ibadge(text):
        color = INVENTORY_COLOR.get(text, "#999")
        label = text.replace("_", " ").title()
        return f'<span style="background:{color};color:#fff;padding:2px 8px;border-radius:10px;font-size:12px;">{label}</span>'

    def substat_str(substats):
        parts = []
        for item in substats:
            if len(item) == 4:
                label, val, is_useful, is_unactivated = item
            else:
                label, val, is_useful = item
                is_unactivated = False

            text = f"{label} +{val}" if val else label
            if is_useful:
                text = f"<b>{text}</b>"
            if is_unactivated:
                text = f'{text} <span style="font-size:10px;color:#888;border:1px solid #555;padding:0 3px;border-radius:3px;">UNLOCKED</span>'

            parts.append(text)

        return ", ".join(parts)

    rec_rows = []
    for b in recommendations:
        gain = b["max_rolls"] - b["equipped_rolls"]
        slot_display = f"{b['slot']} <span style='font-size:11px;color:#88aaff;'>(Equipped)</span>" if b.get("is_self_equipped") else b['slot']

        rec_rows.append(f"""
        <tr>
          <td>{b['character']}</td>
          <td>{slot_display}</td>
          <td>{b['set']}</td>
          <td>Main: {b['main_stat']}<br>{substat_str(b['substats'])}</td>
          <td>{b['rarity']}★ Lv{b['level']} ({b['levels_needed']} levels to max)</td>
          <td>{b['equipped_rolls']}</td>
          <td>{b['current_rolls']} → EV {b['expected_rolls']}<br>(Max {b['max_rolls']})</td>
          <td>{vbadge(b['verdict'])}</td>
        </tr>""")

    flex_rows = []
    for f in flex_results:
        gain = round(f["expected_rolls"] - f["equipped_rolls"], 2)
        flex_rows.append(f"""
        <tr>
          <td>{f['character']}</td>
          <td>{f['slot']}</td>
          <td>{f['set']} <span style="font-size:11px;color:#888;">(off-set)</span></td>
          <td>{f['rarity']}★ Lv{f['level']}</td>
          <td>{f['equipped_rolls']}</td>
          <td>{f['expected_rolls']}</td>
          <td style="color:#4ec97a;">+{gain}</td>
        </tr>""")

    inventory_rows = []
    inventory_sorted = sorted(
        inventory_results,
        key=lambda i: ({"REVIEW": 0, "SANCTIFY_ELIXIR": 1, "SAFE_STRONGBOX": 2}.get(i["action"], 3), -i["ceiling"])
    )
    for i in inventory_sorted:
        art = i["artifact"]
        main_label = art.get("mainStatKey", "?")
        fits = i.get("fits", [])

        if fits:
            best = fits[0]
            best_fit_display = f"{best['character']}{'' if best['in_set'] else ' (off-set)'} — ceiling {best['ceiling']}"
            substats_display = substat_display_for(art, best["useful_stats"])
            alt_display = ", ".join(f"{f['character']} ({f['ceiling']})" for f in fits[1:]) or "—"
        else:
            best_fit_display = "No valid roster user"
            substats_display = substat_display_for(art, [])
            alt_display = "—"

        inventory_rows.append(f"""
        <tr>
        <td>{art.get('setKey', '?')}</td>
        <td>{i['slot']}</td>
        <td>{art.get('rarity', '?')}★ Lv{art.get('level', 0)}</td>
        <td>{main_label}</td>
        <td>{substats_display}</td>
        <td>{best_fit_display}</td>
        <td>{alt_display}</td>
        <td>{ibadge(i['action'])}</td>
        <td>{i['reason']}</td>
        </tr>""")

    strongbox_count = sum(1 for i in inventory_results if i["action"] == "SAFE_STRONGBOX")
    elixir_count = sum(1 for i in inventory_results if i["action"] == "SANCTIFY_ELIXIR")

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
<th>Excellent</th><th>Good / Exc Upgrades</th><th>Slots</th><th>Domain</th><th>Score</th></tr></thead>
<tbody>{''.join(rows)}</tbody>
</table>

<h2>Domains (sorted by farming priority)</h2>
<table id="domainTable">
<thead><tr><th>Domain</th><th>Characters</th><th>Score</th><th>Active</th><th>IT Only</th></tr></thead>
<tbody>{''.join(domain_rows)}</tbody>
</table>

<h2>Recommended Swaps (bench pieces that beat what's currently equipped)</h2>
<p style="color:#999;font-size:13px;max-width:750px;">"Ceiling" assumes every remaining upgrade lands on a useful
stat - not a guarantee, but tells you whether leveling this specific piece is worth the resin. <b>Bold</b> substats
are the ones that count for this character. Up to 3 candidates shown per slot when more than one qualifies.</p>
<table id="recTable">
<thead><tr><th>Character</th><th>Slot</th><th>Set</th><th>Artifact</th><th>Level</th>
<th>Equipped Now</th><th>Current → Ceiling</th><th>Verdict</th></tr></thead>
<tbody>{''.join(rec_rows)}</tbody>
</table>

<h2>Flex Slot Suggestions (4pc-locked characters only)</h2>
<p style="color:#999;font-size:13px;max-width:750px;">Off-set candidates for a single weak slot, evaluated without
breaking the character's 4pc bonus - the other four slots stay in-set. This does not price in the value of the 4pc
set bonus itself, so treat these as leads to sanity-check, not auto-swaps.</p>
<table id="flexTable">
<thead><tr><th>Character</th><th>Slot</th><th>Off-Set Candidate</th><th>Level</th>
<th>Equipped EV</th><th>Candidate EV</th><th>Gain</th></tr></thead>
<tbody>{''.join(flex_rows) if flex_rows else '<tr><td colspan="7" style="color:#999;">No flex candidates cleared the EV-gain threshold.</td></tr>'}</tbody>
</table>

<h2>Inventory Cleanup ({strongbox_count} strongbox, {elixir_count} elixir fodder)</h2>
<p style="color:#999;font-size:13px;max-width:750px;">"Ceiling" is this artifact's maximum possible useful-roll
count for its single best-fit roster character - any character whose main-stat rules allow this slot, in-set or
not. <b>Bold</b> substats are the ones that count for that character. "Alt Homes" lists other characters this piece
could also work for, ranked by ceiling. <b>Review</b>: beats that character's current equipped piece for the slot.
<b>Sanctify Elixir</b>: loses to what's equipped, but has EXP invested (level 1+), so route to Elixir. <b>Safe
Strongbox</b>: loses to what's equipped, level 0, nothing lost either way.</p>
<table id="inventoryTable">
<thead><tr><th>Set</th><th>Slot</th><th>Level</th><th>Main Stat</th><th>Substats</th>
<th>Best Fit</th><th>Alt Homes</th><th>Action</th><th>Reason</th></tr></thead>
<tbody>{''.join(inventory_rows) if inventory_rows else '<tr><td colspan="9" style="color:#999;">No unequipped artifacts found.</td></tr>'}</tbody>
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
    Path(out_path).write_text(html, encoding="utf-8")
    print(f"Wrote {out_path}")