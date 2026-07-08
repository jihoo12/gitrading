#!/usr/bin/env python3
"""
git score TCG — turns a GitHub profile into a trading-card-game style card.

Usage:
    python generate_card.py <username> [--out-dir cards] [--formats svg,png] [--token GH_TOKEN]

Environment variables (used as fallbacks so it's easy to wire into GitHub Actions):
    GITHUB_USERNAME   -> username to render, if not passed as a positional arg
    GH_TOKEN / GITHUB_TOKEN -> token used for GitHub API auth (raises the rate limit
                                from 60/hr to 5000/hr; also required for private stats)

Outputs (into --out-dir, default "cards/"):
    <username>.svg
    <username>.png   (if cairosvg is available and "png" is in --formats)
    <username>.jpg   (if cairosvg is available and "jpg" is in --formats)
"""

import argparse
import base64
import math
import os
import sys
import textwrap
import time
from datetime import datetime, timezone
from html import escape as _escape

import requests

API = "https://api.github.com"

# ----------------------------------------------------------------------------
# Flavor tables (purely cosmetic — mirrors the language color list from the
# original web tool, plus an "element" mapping used for the card's Type line).
# ----------------------------------------------------------------------------
LANG_COLORS = {
    "JavaScript": "#f1e05a", "TypeScript": "#3178c6", "Python": "#3572A5",
    "Java": "#b07219", "Go": "#00ADD8", "Rust": "#dea584", "C": "#555555",
    "C++": "#f34b7d", "C#": "#178600", "Ruby": "#701516", "PHP": "#4F5D95",
    "Swift": "#F05138", "Kotlin": "#A97BFF", "HTML": "#e34c26", "CSS": "#563d7c",
    "Shell": "#89e051", "Vue": "#41b883", "Dart": "#00B4AB", "Scala": "#c22d40",
}
LANG_TYPE = {
    "JavaScript": "Lightning", "TypeScript": "Lightning", "Python": "Nature",
    "Java": "Earth", "Go": "Wind", "Rust": "Fire", "C": "Metal", "C++": "Metal",
    "C#": "Arcane", "Ruby": "Fire", "PHP": "Water", "Swift": "Air",
    "Kotlin": "Arcane", "HTML": "Earth", "CSS": "Water", "Shell": "Dark",
    "Vue": "Nature", "Dart": "Water", "Scala": "Fire",
}

# Rarity tiers, keyed off the same 0-100 score used by the original tool.
RARITY_TIERS = [
    # threshold, letter, name,        gradient colors (light -> dark), text color
    (85, "S", "MYTHIC",   ("#FDE68A", "#B8860B"), "#4A3200"),
    (70, "A", "LEGENDARY", ("#D8B4FE", "#7C3AED"), "#2E1065"),
    (50, "B", "RARE",      ("#93C5FD", "#1D4ED8"), "#0B2A6B"),
    (30, "C", "UNCOMMON",  ("#A7D8B8", "#2F855A"), "#173B27"),
    (0,  "D", "COMMON",    ("#CBD5E1", "#64748B"), "#334155"),
]


def rarity_for(score):
    for threshold, letter, name, colors, text_color in RARITY_TIERS:
        if score >= threshold:
            return {"letter": letter, "name": name, "colors": colors, "text": text_color}
    return {"letter": "D", "name": "COMMON", "colors": ("#CBD5E1", "#64748B"), "text": "#334155"}


def color_for_lang(lang):
    return LANG_COLORS.get(lang, "#8891A0")


def type_for_lang(lang):
    return LANG_TYPE.get(lang, "Code")


# ----------------------------------------------------------------------------
# GitHub API helpers
# ----------------------------------------------------------------------------
def fetch_json(url, token=None, retries=3):
    headers = {"Accept": "application/vnd.github+json"}
    if token:
        headers["Authorization"] = f"Bearer {token}"
    last_err = None
    for attempt in range(retries):
        res = requests.get(url, headers=headers, timeout=20)
        if res.status_code == 200:
            return res.json()
        if res.status_code == 404:
            raise RuntimeError(f"User or resource not found: {url}")
        if res.status_code == 403:
            reset = res.headers.get("X-RateLimit-Reset")
            msg = "GitHub API rate limit exceeded."
            if reset:
                wait = max(0, int(reset) - int(time.time()))
                msg += f" Resets in ~{wait}s. Consider passing a token."
            last_err = RuntimeError(msg)
        else:
            last_err = RuntimeError(f"GitHub API error {res.status_code} for {url}")
        time.sleep(1.5 * (attempt + 1))
    raise last_err


def fetch_avatar_data_uri(url):
    try:
        res = requests.get(url, timeout=20)
        res.raise_for_status()
        content_type = res.headers.get("Content-Type", "image/png").split(";")[0]
        b64 = base64.b64encode(res.content).decode("ascii")
        return f"data:{content_type};base64,{b64}"
    except Exception:
        return None


def fetch_search_total(endpoint, query, token=None, accept="application/vnd.github+json"):
    """Returns the `total_count` from a GitHub Search API endpoint, or 0 on failure.

    Search results degrade gracefully (rather than raising) because the search
    API has a much stricter rate limit than the rest of the REST API, and a
    missed count shouldn't block the whole card from being generated.
    """
    headers = {"Accept": accept}
    if token:
        headers["Authorization"] = f"Bearer {token}"
    try:
        res = requests.get(
            f"{API}/search/{endpoint}",
            headers=headers,
            params={"q": query, "per_page": 1},
            timeout=20,
        )
        if res.status_code == 200:
            return res.json().get("total_count", 0)
        print(f"Warning: search/{endpoint} returned {res.status_code}, counting as 0", file=sys.stderr)
        return 0
    except Exception as e:
        print(f"Warning: search/{endpoint} failed ({e}), counting as 0", file=sys.stderr)
        return 0


def gather_profile(login, token=None):
    user = fetch_json(f"{API}/users/{login}", token)

    repos = []
    for page in (1, 2):
        chunk = fetch_json(
            f"{API}/users/{login}/repos?per_page=100&page={page}&sort=updated", token
        )
        repos.extend(chunk)
        if len(chunk) < 100:
            break

    total_stars = sum(r.get("stargazers_count") or 0 for r in repos)
    total_forks = sum(r.get("forks_count") or 0 for r in repos)
    own_repos = [r for r in repos if not r.get("fork")]

    created_at = datetime.strptime(user["created_at"], "%Y-%m-%dT%H:%M:%SZ").replace(
        tzinfo=timezone.utc
    )
    account_age_years = (datetime.now(timezone.utc) - created_at).days / 365.0

    followers = user.get("followers") or 0
    public_repos = user.get("public_repos") or 0

    # Commit search technically only indexes default-branch commits and can
    # lag slightly, but it's the only way to get a commit count without
    # cloning every repo — good enough for a flavor stat.
    total_commits = fetch_search_total(
        "commits", f"author:{login}", token, accept="application/vnd.github.cloak-preview+json"
    )
    total_prs = fetch_search_total("issues", f"author:{login} type:pr", token)
    total_issues = fetch_search_total("issues", f"author:{login} type:issue", token)

    # Weights sum to 100. Each cap is reached at a similar "difficulty" level
    # to the original followers/stars curve (roughly: hundreds for
    # commits/PRs, tens for issues — see README for the full breakdown).
    s_followers = min(20, math.log2(followers + 1) * 2.667)
    s_stars = min(20, math.log2(total_stars + 1) * 2.667)
    s_repos = min(10, math.log2(public_repos + 1) * 2.0)
    s_age = min(10, account_age_years * 1.333)
    s_forks = min(8, math.log2(total_forks + 1) * 1.76)
    s_commits = min(15, math.log2(total_commits + 1) * 1.35)
    s_prs = min(10, math.log2(total_prs + 1) * 1.3)
    s_issues = min(7, math.log2(total_issues + 1) * 1.3)

    sub_scores = {
        "followers": (s_followers, 20),
        "stars": (s_stars, 20),
        "repos": (s_repos, 10),
        "age": (s_age, 10),
        "forks": (s_forks, 8),
        "commits": (s_commits, 15),
        "prs": (s_prs, 10),
        "issues": (s_issues, 7),
    }
    raw_score = sum(v for v, _ in sub_scores.values())
    score = max(1, min(100, round(raw_score)))

    # Blended stat-bar percentages: each bar folds in a related activity
    # metric alongside its original profile stat. This only changes what's
    # *displayed* in the bar fill — the score above still uses the original
    # 8 independent sub_scores untouched.
    #   POW = stars      + commits   (shipped code that got noticed)
    #   DEF = followers  + issues    (community standing/engagement)
    #   SPD = forks      + PRs       (velocity of collaborative work)
    bar_weights = {
        "pow": (s_stars + s_commits, 20 + 15),
        "def": (s_followers + s_issues, 20 + 7),
        "spd": (s_forks + s_prs, 8 + 10),
    }
    bar_pct = {k: round(v / cap * 100) for k, (v, cap) in bar_weights.items()}
    bar_value = {
        "pow": f"{fmt_num(total_stars)} / {fmt_num(total_commits)}",
        "def": f"{fmt_num(followers)} / {fmt_num(total_issues)}",
        "spd": f"{fmt_num(total_forks)} / {fmt_num(total_prs)}",
    }

    lang_totals = {}
    for r in own_repos:
        lang = r.get("language")
        if lang:
            lang_totals[lang] = lang_totals.get(lang, 0) + 1
    lang_entries = sorted(lang_totals.items(), key=lambda kv: -kv[1])[:5]
    lang_total_count = sum(c for _, c in lang_entries) or 1
    languages = [
        {"name": name, "pct": round(count / lang_total_count * 100), "color": color_for_lang(name)}
        for name, count in lang_entries
    ]
    top_lang = lang_entries[0][0] if lang_entries else None

    avatar_uri = fetch_avatar_data_uri(user["avatar_url"])

    # deterministic pseudo commit-hash, same recipe as the web version
    seed = f"{login}-{score}"
    h = 0
    for ch in seed:
        h = (h * 31 + ord(ch)) & 0xFFFFFFFF
    commit_hash = format(h, "x").rjust(7, "0")[:7]
    collector_no = (h % 998) + 1

    return {
        "login": user["login"],
        "name": user.get("name") or user["login"],
        "bio": user.get("bio") or "",
        "location": user.get("location"),
        "avatar_uri": avatar_uri,
        "followers": followers,
        "public_repos": public_repos,
        "total_stars": total_stars,
        "total_forks": total_forks,
        "total_commits": total_commits,
        "total_prs": total_prs,
        "total_issues": total_issues,
        "account_age_years": account_age_years,
        "score": score,
        "bar_pct": bar_pct,
        "bar_value": bar_value,
        "hash": commit_hash,
        "collector_no": collector_no,
        "languages": languages,
        "top_lang": top_lang,
    }


# ----------------------------------------------------------------------------
# SVG rendering
# ----------------------------------------------------------------------------
def esc(text):
    return _escape(str(text), quote=True)


def wrap(text, width, max_lines):
    if not text:
        return []
    lines = textwrap.wrap(text, width=width)
    if len(lines) > max_lines:
        lines = lines[:max_lines]
        lines[-1] = lines[-1].rstrip()[: width - 1].rstrip() + "…"
    return lines


def fmt_num(n):
    if n >= 1_000_000:
        return f"{n/1_000_000:.1f}M"
    if n >= 1_000:
        return f"{n/1_000:.1f}k"
    return str(n)


def stat_bar(x, y, w, label, value_display, pct, color):
    pct = max(0, min(100, pct))
    bar_w = w - 150  # narrower bar, wider value column to fit combined "X / Y" values
    fill_w = bar_w * pct / 100
    return f"""
    <text x="{x}" y="{y+13}" font-family="'IBM Plex Mono',monospace" font-size="12" font-weight="700" fill="#4A5568">{esc(label)}</text>
    <rect x="{x+62}" y="{y+3}" width="{bar_w}" height="10" rx="5" fill="#DDE3E8"/>
    <rect x="{x+62}" y="{y+3}" width="{fill_w:.1f}" height="10" rx="5" fill="{color}"/>
    <text x="{x+62+bar_w+10}" y="{y+13}" font-family="'IBM Plex Mono',monospace" font-size="12" font-weight="700" fill="#14213D" text-anchor="start">{esc(value_display)}</text>
    """


def build_svg(data):
    W, H = 400, 560
    score = data["score"]
    rarity = rarity_for(score)
    c_light, c_dark = rarity["colors"]
    level = max(1, min(99, round(data["account_age_years"] * 10)))
    top_lang = data["top_lang"]
    card_type = type_for_lang(top_lang) if top_lang else "Unranked"

    name_line = data["name"]
    if len(name_line) > 22:
        name_line = name_line[:21].rstrip() + "…"

    bio_lines = wrap(data["bio"], width=34, max_lines=2)

    avatar_href = data["avatar_uri"] or ""
    ART_TOP, ART_H = 72, 162  # back up from 138 now that only 3 blended stat rows are needed below
    avatar_block = ""
    if avatar_href:
        avatar_block = f'<image href="{avatar_href}" x="28" y="{ART_TOP+4}" width="344" height="{ART_H-8}" preserveAspectRatio="xMidYMid slice" clip-path="url(#artClip)"/>'
    else:
        avatar_block = (
            f'<rect x="28" y="{ART_TOP+4}" width="344" height="{ART_H-8}" fill="#DDE3E8"/>'
            f'<text x="200" y="{ART_TOP+ART_H/2+5:.0f}" text-anchor="middle" font-family="\'IBM Plex Mono\',monospace" '
            'font-size="14" fill="#4A5568">no avatar</text>'
        )

    languages = data["languages"]
    pip_gap = 8
    pip_w = (352 - pip_gap * (max(len(languages), 1) - 1)) / max(len(languages), 1) if languages else 352
    pip_w = min(pip_w, 90)
    pips_svg = ""
    total_pip_w = len(languages) * pip_w + (len(languages) - 1) * pip_gap if languages else 0
    px = 200 - total_pip_w / 2
    PIPS_Y = 490
    for lang in languages:
        pips_svg += f"""
        <g transform="translate({px:.1f},0)">
          <circle cx="7" cy="{PIPS_Y}" r="5" fill="{lang['color']}"/>
          <text x="17" y="{PIPS_Y+4}" font-family="'IBM Plex Mono',monospace" font-size="10" fill="#4A5568">{esc(lang['name'][:8])} {lang['pct']}%</text>
        </g>"""
        px += pip_w + pip_gap

    # --- fixed layout slots (avoids any dynamic overlap between sections) ---
    TYPE_TOP = ART_TOP + ART_H + 6      # 216
    STATS_TOP = TYPE_TOP + 26 + 12       # 254
    STATS_H = 114                        # room for a label + 3 blended stat rows
    ABILITY_TOP = STATS_TOP + STATS_H + 8   # 400
    ABILITY_H = 70

    bar_pct = data["bar_pct"]
    bar_value = data["bar_value"]
    stats_svg = ""
    stat_rows = [
        ("POW", bar_value["pow"], bar_pct["pow"], "#2DA44E"),
        ("DEF", bar_value["def"], bar_pct["def"], "#1D4ED8"),
        ("SPD", bar_value["spd"], bar_pct["spd"], "#B8860B"),
    ]
    row_gap = (STATS_H - 18) / len(stat_rows)
    for i, (label, value, row_pct, color) in enumerate(stat_rows):
        row_y = STATS_TOP + 18 + i * row_gap
        stats_svg += stat_bar(28, row_y, 344, label, value, row_pct, color)

    bio_top = ABILITY_TOP + 16
    bio_svg = ""
    if bio_lines:
        for i, line in enumerate(bio_lines):
            bio_svg += f'<text x="40" y="{bio_top + i*16}" font-family="Inter, sans-serif" font-size="12.5" fill="#14213D">{esc(line)}</text>'
    else:
        bio_svg = f'<text x="40" y="{bio_top}" font-family="Inter, sans-serif" font-size="12.5" fill="#4A5568" font-style="italic">No bio provided.</text>'

    ability_line = "Passive: stat bars show profile stat / activity stat (e.g. stars / commits)."
    ability_line_y = ABILITY_TOP + ABILITY_H - 10

    generated = datetime.now(timezone.utc).strftime("%Y-%m-%d")

    svg = f"""<svg width="{W}" height="{H}" viewBox="0 0 {W} {H}" xmlns="http://www.w3.org/2000/svg" font-family="Inter, sans-serif">
  <defs>
    <linearGradient id="rarityGrad" x1="0" y1="0" x2="1" y2="1">
      <stop offset="0%" stop-color="{c_light}"/>
      <stop offset="100%" stop-color="{c_dark}"/>
    </linearGradient>
    <clipPath id="artClip">
      <rect x="28" y="{ART_TOP+4}" width="344" height="{ART_H-8}" rx="10"/>
    </clipPath>
  </defs>

  <!-- outer rarity border -->
  <rect x="4" y="4" width="{W-8}" height="{H-8}" rx="24" fill="url(#rarityGrad)"/>
  <!-- inner card body -->
  <rect x="13" y="13" width="{W-26}" height="{H-26}" rx="18" fill="#F3F1E9" stroke="#C7D0D8" stroke-width="1"/>

  <!-- header -->
  <text x="30" y="42" font-family="'IBM Plex Mono',monospace" font-size="18" font-weight="700" fill="#14213D">{esc(name_line)}</text>
  <text x="30" y="58" font-family="'IBM Plex Mono',monospace" font-size="11" fill="#4A5568">@{esc(data['login'])}</text>
  <circle cx="365" cy="40" r="22" fill="url(#rarityGrad)"/>
  <text x="365" y="45" text-anchor="middle" font-family="'IBM Plex Mono',monospace" font-size="18" font-weight="800" fill="{rarity['text']}">{score}</text>

  <!-- art frame -->
  <rect x="24" y="{ART_TOP}" width="352" height="{ART_H}" rx="14" fill="url(#rarityGrad)"/>
  {avatar_block}

  <!-- type line -->
  <rect x="24" y="{TYPE_TOP}" width="352" height="26" rx="6" fill="#E9EDF0" stroke="#C7D0D8" stroke-width="1"/>
  <text x="34" y="{TYPE_TOP+17}" font-family="'IBM Plex Mono',monospace" font-size="12" font-weight="600" fill="#14213D">GitHub Developer — {esc(card_type)} Type</text>
  <text x="366" y="{TYPE_TOP+17}" text-anchor="end" font-family="'IBM Plex Mono',monospace" font-size="12" font-weight="700" fill="#4A5568">Lv.{level}</text>

  <!-- stat block -->
  <rect x="24" y="{STATS_TOP}" width="352" height="{STATS_H}" rx="10" fill="#FFFFFF" stroke="#C7D0D8" stroke-width="1"/>
  <text x="34" y="{STATS_TOP+13}" font-family="'IBM Plex Mono',monospace" font-size="11" font-weight="700" fill="#4A5568">// STATS</text>
  {stats_svg}

  <!-- ability box -->
  <rect x="24" y="{ABILITY_TOP}" width="352" height="{ABILITY_H}" rx="10" fill="#FFFFFF" stroke="#C7D0D8" stroke-width="1"/>
  {bio_svg}
  <text x="40" y="{ability_line_y}" font-family="Inter, sans-serif" font-size="11" fill="#4A5568" font-style="italic">{esc(ability_line)}</text>

  <!-- language pips -->
  {pips_svg}

  <!-- footer -->
  <rect x="24" y="500" width="352" height="28" rx="8" fill="url(#rarityGrad)"/>
  <text x="36" y="518" font-family="'IBM Plex Mono',monospace" font-size="12" font-weight="800" fill="{rarity['text']}">{rarity['letter']} · {esc(rarity['name'])}</text>
  <text x="200" y="518" text-anchor="middle" font-family="'IBM Plex Mono',monospace" font-size="10" fill="{rarity['text']}">#{esc(data['hash'])}</text>
  <text x="364" y="518" text-anchor="end" font-family="'IBM Plex Mono',monospace" font-size="10" fill="{rarity['text']}">No. {data['collector_no']:03d}/999</text>

  <text x="200" y="540" text-anchor="middle" font-family="'IBM Plex Mono',monospace" font-size="8" fill="#4A5568">git score TCG · generated {generated}</text>
</svg>
"""
    return svg


# ----------------------------------------------------------------------------
# Main
# ----------------------------------------------------------------------------
def main():
    parser = argparse.ArgumentParser(description="Generate a git-score TCG card for a GitHub user.")
    parser.add_argument("username", nargs="?", default=os.environ.get("GITHUB_USERNAME"))
    parser.add_argument("--out-dir", default="cards")
    parser.add_argument("--formats", default="svg,png", help="comma separated: svg,png,jpg")
    parser.add_argument("--token", default=os.environ.get("GH_TOKEN") or os.environ.get("GITHUB_TOKEN"))
    args = parser.parse_args()

    if not args.username:
        print("Error: no username given (pass it as an argument or set GITHUB_USERNAME).", file=sys.stderr)
        sys.exit(1)

    formats = [f.strip().lower() for f in args.formats.split(",") if f.strip()]
    os.makedirs(args.out_dir, exist_ok=True)

    print(f"Fetching GitHub data for '{args.username}'...")
    data = gather_profile(args.username, token=args.token)
    print(f"Score: {data['score']}  Rarity: {rarity_for(data['score'])['name']}")

    svg = build_svg(data)
    svg_path = os.path.join(args.out_dir, f"{args.username}.svg")
    with open(svg_path, "w", encoding="utf-8") as f:
        f.write(svg)
    print(f"Wrote {svg_path}")

    if "png" in formats or "jpg" in formats:
        try:
            import cairosvg
        except ImportError:
            print(
                "cairosvg is not installed, skipping raster export. "
                "Install it with: pip install cairosvg",
                file=sys.stderr,
            )
            cairosvg = None
        if cairosvg:
            if "png" in formats:
                png_path = os.path.join(args.out_dir, f"{args.username}.png")
                cairosvg.svg2png(bytestring=svg.encode("utf-8"), write_to=png_path, output_width=800, output_height=1120)
                print(f"Wrote {png_path}")
            if "jpg" in formats:
                # cairosvg writes PNG bytes; flatten onto a white background then save as JPEG.
                from io import BytesIO
                from PIL import Image

                png_bytes = cairosvg.svg2png(bytestring=svg.encode("utf-8"), output_width=800, output_height=1120)
                img = Image.open(BytesIO(png_bytes)).convert("RGBA")
                bg = Image.new("RGBA", img.size, (255, 255, 255, 255))
                bg.paste(img, mask=img)
                jpg_path = os.path.join(args.out_dir, f"{args.username}.jpg")
                bg.convert("RGB").save(jpg_path, "JPEG", quality=92)
                print(f"Wrote {jpg_path}")


if __name__ == "__main__":
    main()
