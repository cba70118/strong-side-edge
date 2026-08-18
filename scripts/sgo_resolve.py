"""
sgo_resolve.py - SportsGameOdds playerID -> nflverse gsis_id.

Shared by snapshot_sgo.py and sgo_history.py so both resolve identically.

Entity resolution is a first-class module here because the first naive version
failed on exactly the players that matter. Of 13 unresolved names, the list
included DeVonta Smith, Justin Jefferson and Lamar Jackson - not obscure depth
pieces. Two distinct causes:

  AMBIGUITY. raw.players holds more than one row for some names (duplicate or
  legacy entries). Requiring a unique candidate silently gave up on stars.
  SGO supplies teamID on every player object, which disambiguates them.

  NORMALISATION. Replacing '.' with a space turns "A.J. Dillon" into
  "a j dillon" while "AJ Dillon" becomes "aj dillon" - never equal. Likewise
  SGO writes "KeAndre LambertSmith" where nflverse has "KeAndre Lambert-Smith".
  So two keys are built per name: punctuation stripped, and a second with all
  whitespace removed as well.

Match order, most specific first:
    1. tight name + team
    2. tight name, unique
    3. squashed name + team
    4. squashed name, unique
Anything else stays NULL and surfaces in audit.unresolved_players.
"""

from __future__ import annotations

import re

_SUFFIX = re.compile(r"\b(jr|sr|ii|iii|iv|v)\b\.?", re.I)
_DROP = re.compile(r"[.'`]")          # removed outright, never spaced
_TOSPACE = re.compile(r"[-_/]")       # genuine separators
_NONALPHA = re.compile(r"[^a-z ]")


def tight(name: str) -> str:
    """Lowercase, suffix-stripped, punctuation removed (NOT spaced)."""
    s = (name or "").lower()
    s = _DROP.sub("", s)              # "a.j." -> "aj"
    s = _TOSPACE.sub(" ", s)          # "lambert-smith" -> "lambert smith"
    s = _SUFFIX.sub(" ", s)
    return " ".join(_NONALPHA.sub(" ", s).split())


def squashed(name: str) -> str:
    """tight() with all spaces removed: 'lambertsmith' == 'lambert smith'."""
    return tight(name).replace(" ", "")


def team_from_sgo(team_id: str) -> str | None:
    """'KANSAS_CITY_CHIEFS_NFL' -> 'kansas city chiefs' (matched to ref.team)."""
    if not team_id:
        return None
    s = team_id.replace("_NFL", "").replace("_", " ").lower().strip()
    return s or None


def load_overrides() -> dict[str, str]:
    """ref/overrides/player_overrides.json -> {sgo_player_id: gsis_id}."""
    import json
    from pathlib import Path
    p = (Path(__file__).resolve().parent.parent
         / "ref" / "overrides" / "player_overrides.json")
    if not p.exists():
        return {}
    data = json.loads(p.read_text(encoding="utf-8"))
    sec = data.get("sportsgameodds", {})
    return {k: v["gsis_id"] for k, v in sec.items()
            if isinstance(v, dict) and v.get("gsis_id")}


class PlayerResolver:
    def __init__(self, con, min_last_season: int = 2023):
        self.overrides = load_overrides()
        self.by_tight: dict[str, list] = {}
        self.by_tight_team: dict[tuple, list] = {}
        self.by_squash: dict[str, list] = {}
        self.by_squash_team: dict[tuple, list] = {}

        # nflverse team abbr -> normalized full name, for the team key
        self.team_name = {r[0]: (r[1] or "").lower()
                          for r in con.execute(
                              "SELECT team_id, full_name FROM ref.team").fetchall()}

        rows = con.execute("""
            SELECT gsis_id, display_name, latest_team
            FROM raw.players
            WHERE gsis_id IS NOT NULL AND display_name IS NOT NULL
              AND coalesce(last_season, 0) >= ?
        """, [min_last_season]).fetchall()
        for gid, disp, team in rows:
            t, q = tight(disp), squashed(disp)
            tn = self.team_name.get(team, "")
            self.by_tight.setdefault(t, []).append(gid)
            self.by_squash.setdefault(q, []).append(gid)
            if tn:
                self.by_tight_team.setdefault((t, tn), []).append(gid)
                self.by_squash_team.setdefault((q, tn), []).append(gid)
        self.n_candidates = len(rows)

    def resolve(self, name: str, sgo_team_id: str | None = None,
                sgo_player_id: str | None = None):
        """Returns (gsis_id | None, method). Manual overrides win outright."""
        if sgo_player_id and sgo_player_id in self.overrides:
            return self.overrides[sgo_player_id], "manual"
        t, q = tight(name), squashed(name)
        tn = team_from_sgo(sgo_team_id or "")

        if tn:
            c = self.by_tight_team.get((t, tn), [])
            if len(c) == 1:
                return c[0], "name+team"
        c = self.by_tight.get(t, [])
        if len(c) == 1:
            return c[0], "name"
        if tn:
            c = self.by_squash_team.get((q, tn), [])
            if len(c) == 1:
                return c[0], "squashed+team"
        c = self.by_squash.get(q, [])
        if len(c) == 1:
            return c[0], "squashed"
        if c:
            return None, "ambiguous"
        return None, "unresolved"
