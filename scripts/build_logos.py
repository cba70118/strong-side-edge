"""build_logos.py - turn the team logo pack into an embeddable asset module.

The report is a single self-contained file with a strict CSP, so logos cannot
be linked - they have to travel inside the page as data URIs. The pack ships
full-resolution PNGs (30-350 KB each, ~5 MB total) which would bloat the page
for marks rendered at 22 px. Downscaling to a 2x sprite size first takes the
whole set to well under 200 KB.

Usage:
    py -3 scripts/build_logos.py --zip "raiders-com-logo (2).zip"
"""

from __future__ import annotations

import argparse
import base64
import io
import json
import zipfile
from pathlib import Path

from PIL import Image

ROOT = Path(__file__).resolve().parent.parent

# The pack is named by club web domain; the warehouse speaks nflverse codes.
TEAM_BY_FILE = {
    "houstontexans": "HOU", "steelers": "PIT", "newyorkjets": "NYJ",
    "patriots": "NE", "miamidolphins": "MIA", "buffalobills": "BUF",
    "baltimoreravens": "BAL", "bengals": "CIN", "clevelandbrowns": "CLE",
    "titansonline": "TEN", "chiefs": "KC", "denverbroncos": "DEN",
    "philadelphiaeagles": "PHI", "dallascowboys": "DAL", "giants": "NYG",
    "jaguars": "JAX", "colts": "IND", "raiders": "LV", "commanders": "WAS",
    "chicagobears": "CHI", "detroitlions": "DET", "packers": "GB",
    "azcardinals": "ARI", "vikings": "MIN", "buccaneers": "TB",
    "neworleanssaints": "NO", "panthers": "CAR", "atlantafalcons": "ATL",
    "therams": "LA", "49ers": "SF", "seahawks": "SEA", "chargers": "LAC",
    # A cleaner crest supplied separately - the pack ships a wide
    # wordmark for New Orleans that is illegible at table size.
    "New_Orleans_Saints_logo.svg": "NO",
}


def main() -> int:
    ap = argparse.ArgumentParser()
    # ONE SOURCE FOR ALL 32. The supplied pack shipped five clubs - ARI, DET,
    # KC, NE, NYG - as a mark on a solid colored tile rather than a die cut, so
    # those five sat in the page as coloured squares while the other 27 floated.
    # raw.teams.team_logo_espn is transparent for every club, which is the whole
    # point of taking them all from one place.
    ap.add_argument("--zip", help="a local pack of logo files")
    ap.add_argument("--espn", action="store_true",
                    help="fetch all 32 die-cut logos from raw.teams.team_logo_espn")
    ap.add_argument("--size", type=int, default=56, help="max edge in px")
    ap.add_argument("-o", "--out", default="scripts/logo_assets.py")
    a = ap.parse_args()

    def add(team, raw):
        img = Image.open(io.BytesIO(raw)).convert("RGBA")
        # Trim transparent margins first - the pack's padding is inconsistent
        # between clubs, so cropping is what makes them comparable at all.
        bbox = img.getbbox()
        if bbox:
            img = img.crop(bbox)
        # THEN PAD BACK TO A SQUARE. Aspect ratios in the pack run from 0.73 to
        # 2.26: some clubs ship a crest, others a wide wordmark. Dropping a
        # 2.26:1 mark straight into a square icon box renders it as a 20x9
        # smear. Centring each mark on a square canvas keeps every crest the
        # same optical size and stops the wide ones collapsing.
        img.thumbnail((a.size, a.size), Image.LANCZOS)
        canvas = Image.new("RGBA", (a.size, a.size), (0, 0, 0, 0))
        canvas.paste(img, ((a.size - img.width) // 2,
                           (a.size - img.height) // 2), img)
        buf = io.BytesIO()
        canvas.save(buf, "PNG", optimize=True)
        b = buf.getvalue()
        out[team] = "data:image/png;base64," + base64.b64encode(b).decode()
        return len(b)

    out, missing, total = {}, [], 0
    if a.espn:
        # ONE SOURCE FOR ALL 32, fetched rather than unpacked. Every logo is
        # then guaranteed transparent and cropped by the same rule.
        import duckdb
        import urllib.request
        con = duckdb.connect(str(ROOT / "data" / "nfl.duckdb"), read_only=True)
        urls = con.execute("""
            SELECT team_abbr, team_logo_espn FROM raw.teams
            WHERE team_logo_espn IS NOT NULL
              -- relocated franchises share a logo with their current code and
              -- would overwrite it
              AND team_abbr NOT IN ('LAR', 'OAK', 'SD', 'STL')
            ORDER BY team_abbr
        """).fetchall()
        con.close()
        for abbr, url in urls:
            try:
                req = urllib.request.Request(
                    url, headers={"User-Agent": "Mozilla/5.0"})
                raw = urllib.request.urlopen(req, timeout=30).read()
            except Exception as exc:
                missing.append(f"{abbr}({type(exc).__name__})")
                continue
            total += add(abbr, raw)
    else:
        if not a.zip:
            print("  pass --zip <pack> or --espn")
            return 1
        # Loose PNGs sitting beside the pack are picked up too - the Chargers
        # arrived separately.
        for f in sorted(ROOT.glob("*.png")):
            stem = f.stem.replace("-com-logo", "").replace("-logo", "")
            if stem in TEAM_BY_FILE:
                total += add(TEAM_BY_FILE[stem], f.read_bytes())
        with zipfile.ZipFile(a.zip) as z:
            for name in z.namelist():
                if not name.lower().endswith(".png"):
                    continue
                stem = Path(name).stem.replace("-com-logo", "").replace("-logo", "")
                team = TEAM_BY_FILE.get(stem)
                if team is None:
                    if stem != "nfl":
                        missing.append(stem)
                    continue
                if team in out:
                    continue
                total += add(team, z.read(name))

    if missing:
        print(f"  unmapped files: {missing}")
    print(f"  {len(out)} logos, {total/1024:.0f} KB raw -> "
          f"{sum(len(v) for v in out.values())/1024:.0f} KB as data URIs")

    # Colors come from the warehouse, not the pack. Every team needs one: the
    # pack is missing the Chargers, and a club with no crest still has to
    # render as something deliberate rather than a broken image.
    import duckdb
    con = duckdb.connect(str(ROOT / "data" / "nfl.duckdb"), read_only=True)
    cols = {r[0]: [r[1], r[2]] for r in con.execute(
        "SELECT team_abbr, team_color, team_color2 FROM raw.teams").fetchall()}
    con.close()
    absent = sorted(set(cols) - set(out) - {"LAR", "OAK", "SD", "STL"})
    if absent:
        print(f"  NO CREST for {absent} - renders as a color badge instead")

    body = ('"""Auto-generated by build_logos.py. Team crests as data URIs."""\n\n'
            "LOGOS = " + json.dumps(out, indent=0, sort_keys=True) + "\n\n"
            "COLORS = " + json.dumps(cols, indent=0, sort_keys=True) + "\n")
    Path(a.out).write_text(body, encoding="utf-8")
    print(f"  wrote {a.out}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
