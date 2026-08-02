"""
Module: snapshot

Purpose:
Implements the "run-to-run progress snapshot" feature from the README's
Planned Features list (#1). Saves a compact record of each character's
status/excellent-count/set-bonus/score after a run, and on later runs diffs
the current results against the last saved snapshot to surface what changed
(e.g. "Bennett: Needs Work -> Luxury, +2 excellent pieces").

Responsibilities:
1. Persist a small JSON snapshot file (timestamp + per-character summary).
2. Load and validate a previously saved snapshot.
3. Diff current char_results against a loaded snapshot into human-readable
   change strings.
4. Enforce a minimum real-time interval between snapshot saves/diffs, so
   repeated runs during testing/tweaking don't manufacture the appearance of
   meaningful progress every few minutes. If the interval hasn't elapsed,
   the on-disk snapshot is left untouched and no diff is produced.

Architectural Role:
Utility module used by score.py. Does not know about scoring internals
beyond the shape of char_results records already produced by
character_scoring.score_character() (name, status, excellent_pieces,
set_status, score).

Boundaries:
This module does not compute scores or drive the dashboard. It only reads
char_results after scoring is complete and manages the snapshot file.

Public API:
- load_snapshot(path)
- extract_snapshot_data(char_results)
- compute_progress(old_snapshot, char_results)
- maybe_update_snapshot(path, char_results, min_interval_hours=24, now=None)
"""

import json
from datetime import datetime, timezone
from pathlib import Path


def load_snapshot(path):
    """Load a previously saved snapshot JSON file. Returns None if the file
    doesn't exist or can't be parsed (treated as "no prior snapshot")."""
    p = Path(path)
    if not p.exists():
        return None
    try:
        return json.loads(p.read_text(encoding="utf-8"))
    except (json.JSONDecodeError, OSError):
        return None


def extract_snapshot_data(char_results):
    """Build the compact per-character record that gets persisted:
    status, excellent piece count, active set bonus, and score."""
    chars = {}
    for r in char_results:
        chars[r["name"]] = {
            "status": r.get("status"),
            "excellent_pieces": r.get("excellent_pieces"),
            "set_bonus": r.get("set_status", {}).get("active_bonus", "N/A"),
            "score": round(r.get("score", 0), 1),
        }
    return chars


def _fmt_change(name, old, new):
    """Build a single human-readable change line for one character, or
    return None if nothing tracked actually changed."""
    parts = []

    if old.get("status") != new.get("status"):
        parts.append(f"{old.get('status', '?')} \u2192 {new.get('status', '?')}")

    old_exc = old.get("excellent_pieces") or 0
    new_exc = new.get("excellent_pieces") or 0
    exc_delta = new_exc - old_exc
    if exc_delta != 0:
        sign = "+" if exc_delta > 0 else ""
        piece_word = "piece" if abs(exc_delta) == 1 else "pieces"
        parts.append(f"{sign}{exc_delta} excellent {piece_word}")

    if old.get("set_bonus") != new.get("set_bonus"):
        parts.append(f"set bonus {old.get('set_bonus', '?')} \u2192 {new.get('set_bonus', '?')}")

    if not parts:
        return None
    return f"{name}: " + ", ".join(parts)


def compute_progress(old_snapshot, char_results):
    """Compare current char_results against a previously loaded snapshot
    dict (raw JSON with a 'characters' key). Returns a list of
    human-readable change strings, one per character with a tracked change.
    Characters new to the roster since the last snapshot (not present in
    old_snapshot) are skipped rather than reported as a change, since there's
    nothing to diff against. Characters with no tracked change are omitted."""
    if not old_snapshot:
        return []

    old_chars = old_snapshot.get("characters", {})
    new_chars = extract_snapshot_data(char_results)

    changes = []
    for name, new in new_chars.items():
        old = old_chars.get(name)
        if old is None:
            continue
        line = _fmt_change(name, old, new)
        if line:
            changes.append(line)
    return changes


def maybe_update_snapshot(path, char_results, min_interval_hours=24, now=None):
    """Gate snapshot save+diff on a minimum real-time interval since the
    last saved snapshot, so repeated testing/tweaking runs don't manufacture
    fake "progress" entries every few minutes.

    Returns:
    - A list of progress-change strings (possibly empty) if the snapshot was
      refreshed this run.
    - None if the minimum interval hasn't elapsed yet since the last saved
      snapshot. In this case the on-disk snapshot file is left untouched and
      no diff is computed/shown.
    """
    now = now or datetime.now(timezone.utc)
    old_snapshot = load_snapshot(path)

    if old_snapshot is not None:
        last_ts = None
        try:
            last_ts = datetime.fromisoformat(old_snapshot["timestamp"])
        except (KeyError, ValueError, TypeError):
            last_ts = None

        if last_ts is not None:
            if last_ts.tzinfo is None:
                last_ts = last_ts.replace(tzinfo=timezone.utc)
            elapsed_hours = (now - last_ts).total_seconds() / 3600.0
            if elapsed_hours < min_interval_hours:
                return None  # too soon — leave snapshot untouched, no diff

    changes = compute_progress(old_snapshot, char_results)

    new_snapshot = {
        "timestamp": now.isoformat(),
        "characters": extract_snapshot_data(char_results),
    }
    Path(path).write_text(json.dumps(new_snapshot, indent=2), encoding="utf-8")

    return changes