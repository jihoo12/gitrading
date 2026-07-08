#!/usr/bin/env python3
"""
git score TCG — turns a GitHub profile into a trading-card-game style card.

Usage:
    python generate_card.py <username> [--out-dir cards] [--formats svg,png] [--token GH_TOKEN]
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
# Flavor tables
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

RARITY_TIERS = [
    (85, "S", "MYTHIC",   ("#F59E0B", "#EF4444", "#701A75"), "#FFFFFF"),
    (70, "A", "LEGENDARY", ("#A855F7", "#6366F1", "#1E1B4B"), "#FFFFFF"),
    (50, "B", "RARE",      ("#3B82F6", "#06B6D4", "#0F172A"), "#FFFFFF"),
    (30, "C", "UNCOMMON",  ("#10B981", "#059669", "#064E3B"), "#FFFFFF"),
    (0,  "D", "COMMON",    ("#94A3B8", "#475569", "#0F172A"), "#FFFFFF"),
]


def rarity_for(score):
    for threshold, letter, name, colors, text_color in RARITY_TIERS:
        if score >= threshold:
            return {"letter": letter, "name": name, "colors": colors, "text": text_color}
    return {"letter": "D", "name": "COMMON", "colors": ("#94A3B8", "#475569", "#0F172A"), "text": "#FFFFFF"}


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
        return 0
    except Exception:
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

    # Retained safely behind the scenes to keep mathematical logic accurate
    total_commits = fetch_search_total(
        "commits", f"author:{login}", token, accept="application/vnd.github.cloak-preview+json"
    )
    total_prs = fetch_search_total("issues", f"author:{login} type:pr", token)
    total_issues = fetch_search_total("issues", f"author:{login} type:issue", token)

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

    # Bar fills map cleanly onto core metrics directly
    bar_pct = {
        "pow": round(s_stars / 20 * 100),
        "def": round(s_followers / 20 * 100),
        "spd": round(s_repos / 10 * 100),
    }
    
    metrics_map = {
        "stars": fmt_num(total_stars),
        "followers": fmt_num(followers),
        "repos": fmt_num(public_repos)
    }

    lang_totals = {}
    for r in own_repos:
        lang = r.get("language")
        if lang:
            lang_totals[lang] = lang_totals.get(lang, 0) + 1
    lang_entries = sorted(lang_totals.items(), key=lambda kv: -kv[1])[:4]
    lang_total_count = sum(c for _, c in lang_entries) or 1
    languages = [
        {"name": name, "pct": round(count / lang_total_count * 100), "color": color_for_lang(name)}
        for name, count in lang_entries
    ]
    top_lang = lang_entries[0][0] if lang_entries else None

    avatar_uri = fetch_avatar_data_uri(user["avatar_url"])

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
        "score": score,
        "bar_pct": bar_pct,
        "metrics_map": metrics_map,
        "hash": commit_hash,
        "collector_no": collector_no,
        "languages": languages,
        "top_lang": top_lang,
        "account_age_years": account_age_years
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


def render_stat_group(x, y, group_label, metric_val, bar_pct, color):
    pct = max(0, min(100, bar_pct))
    bar_w = 190
    fill_w = bar_w * pct / 100
    
    return f"""
    <g transform="translate({x}, {y})">
      <text x="0" y="20" font-family="system-ui, sans-serif" font-size="12" font-weight="900" fill="#FFFFFF" letter-spacing="0.05em">{esc(group_label)}</text>
      
      <rect x="52" y="10" width="{bar_w}" height="10" rx="3" fill="#1E293B"/>
      <rect x="52" y="10" width="{fill_w:.1f}" height="10" rx="3" fill="{color}"/>
      
      <g transform="translate(262, 0)">
        <rect x="0" y="0" width="84" height="26" rx="5" fill="#0F172A" stroke="#334155" stroke-width="1"/>
        <text x="42" y="17" text-anchor="middle" font-family="system-ui, sans-serif" font-size="11" font-weight="900" fill="#38BDF8">{esc(metric_val)}</text>
      </g>
    </g>
    """


def build_svg(data):
    W, H = 400, 560
    score = data["score"]
    rarity = rarity_for(score)
    c_1, c_2, c_3 = rarity["colors"]
    level = max(1, min(99, round(data["account_age_years"] * 10)))
    top_lang = data["top_lang"]
    card_type = type_for_lang(top_lang) if top_lang else "Unranked"

    name_line = data["name"]
    if len(name_line) > 20:
        name_line = name_line[:19].rstrip() + "…"

    bio_lines = wrap(data["bio"], width=42, max_lines=2)
    avatar_href = data["avatar_uri"] or ""
    
    ART_TOP, ART_H = 76, 150  
    TYPE_TOP = ART_TOP + ART_H + 10       
    STATS_TOP = TYPE_TOP + 28 + 10        
    STATS_H = 126                         
    ABILITY_TOP = STATS_TOP + STATS_H + 10   
    ABILITY_H = 54

    if avatar_href:
        avatar_block = f'<image href="{avatar_href}" x="20" y="{ART_TOP}" width="360" height="{ART_H}" preserveAspectRatio="xMidYMid slice" clip-path="url(#artClip)"/>'
    else:
        avatar_block = (
            f'<rect x="20" y="{ART_TOP}" width="360" height="{ART_H}" fill="#1E293B" rx="12"/>'
            f'<text x="200" y="{ART_TOP + ART_H/2 + 5:.0f}" text-anchor="middle" font-family="system-ui, sans-serif" font-size="13" font-weight="700" fill="#64748B" letter-spacing="0.05em">AVATAR OFFLINE</text>'
        )

    # Dynamic auto-sizing language indicators
    languages = data["languages"]
    pips_svg = ""
    if languages:
        total_langs = len(languages)
        gap = 8
        total_gap = gap * (total_langs - 1)
        badge_w = min((360 - total_gap) / total_langs, 84)
        start_x = 200 - ((badge_w * total_langs + total_gap) / 2)
        PIPS_Y = 498
        
        for i, lang in enumerate(languages):
            bx = start_x + i * (badge_w + gap)
            pips_svg += f"""
            <g transform="translate({bx:.1f}, {PIPS_Y})">
              <rect x="0" y="0" width="{badge_w:.1f}" height="22" rx="6" fill="#0F172A" stroke="#1E293B" stroke-width="1"/>
              <circle cx="10" cy="11" r="3.5" fill="{lang['color']}"/>
              <text x="18" y="14" font-family="system-ui, sans-serif" font-size="9" font-weight="800" fill="#E2E8F0">{esc(lang['name'].upper())}</text>
            </g>"""

    mm = data["metrics_map"]
    bp = data["bar_pct"]
    
    stats_svg = ""
    stats_svg += render_stat_group(28, STATS_TOP + 22, "STR", mm["stars"], bp["pow"], "#F43F5E")
    stats_svg += render_stat_group(28, STATS_TOP + 54, "FOL", mm["followers"], bp["def"], "#3B82F6")
    stats_svg += render_stat_group(28, STATS_TOP + 86, "REP", mm["repos"], bp["spd"], "#10B981")

    bio_top = ABILITY_TOP + 18
    bio_svg = ""
    if bio_lines:
        for i, line in enumerate(bio_lines):
            bio_svg += f'<text x="36" y="{bio_top + i*14}" font-family="system-ui, sans-serif" font-size="11" font-weight="600" fill="#94A3B8">{esc(line)}</text>'
    else:
        bio_svg = f'<text x="36" y="{bio_top + 4}" font-family="system-ui, sans-serif" font-size="11" fill="#475569" font-style="italic">No developer manifesto specified.</text>'

    svg = f"""<svg width="{W}" height="{H}" viewBox="0 0 {W} {H}" xmlns="http://www.w3.org/2000/svg">
  <defs>
    <linearGradient id="cyberGrad" x1="0%" y1="0%" x2="100%" y2="100%">
      <stop offset="0%" stop-color="{c_1}"/>
      <stop offset="50%" stop-color="{c_2}"/>
      <stop offset="100%" stop-color="{c_3}"/>
    </linearGradient>
    <filter id="neonGlow" x="-20%" y="-20%" width="140%" height="140%">
      <feDropShadow dx="0" dy="4" stdDeviation="6" flood-color="#000000" flood-opacity="0.5"/>
    </filter>
    <clipPath id="artClip">
      <rect x="20" y="{ART_TOP}" width="360" height="{ART_H}" rx="12"/>
    </clipPath>
  </defs>

  <rect x="4" y="4" width="{W-8}" height="{H-8}" rx="24" fill="url(#cyberGrad)"/>
  <rect x="12" y="12" width="{W-24}" height="{H-24}" rx="18" fill="#0B0F19" stroke="#1E293B" stroke-width="1.5"/>

  <g transform="translate(24, 30)">
    <text x="0" y="16" font-family="system-ui, sans-serif" font-size="20" font-weight="900" fill="#FFFFFF" letter-spacing="-0.01em">{esc(name_line).upper()}</text>
    <text x="0" y="31" font-family="monospace" font-size="11" font-weight="700" fill="#38BDF8">@{esc(data['login']).upper()}</text>
  </g>
  
  <circle cx="360" cy="44" r="20" fill="url(#cyberGrad)" filter="url(#neonGlow)"/>
  <text x="360" y="50" text-anchor="middle" font-family="system-ui, sans-serif" font-size="16" font-weight="900" fill="{rarity['text']}">{score}</text>

  <g filter="url(#neonGlow)">
    <rect x="18" y="{ART_TOP-2}" width="364" height="{ART_H+4}" rx="14" fill="url(#cyberGrad)" opacity="0.4"/>
    {avatar_block}
  </g>

  <g filter="url(#neonGlow)">
    <rect x="20" y="{TYPE_TOP}" width="360" height="28" rx="6" fill="#0F172A" stroke="#1E293B" stroke-width="1"/>
    <text x="32" y="{TYPE_TOP+18}" font-family="system-ui, sans-serif" font-size="11" font-weight="900" fill="#F8FAFC" letter-spacing="0.05em">CLASS // {esc(card_type).upper()} TYPE</text>
    <rect x="324" y="{TYPE_TOP+5}" width="46" height="18" rx="4" fill="url(#cyberGrad)"/>
    <text x="347" y="{TYPE_TOP+17}" text-anchor="middle" font-family="system-ui, sans-serif" font-size="10" font-weight="900" fill="{rarity['text']}">LV.{level}</text>
  </g>

  <g filter="url(#neonGlow)">
    <rect x="20" y="{STATS_TOP}" width="360" height="{STATS_H}" rx="12" fill="#0F172A" opacity="0.85" stroke="#1E293B" stroke-width="1"/>
    <text x="32" y="{STATS_TOP+15}" font-family="system-ui, sans-serif" font-size="9" font-weight="900" fill="#64748B" letter-spacing="0.1em">CORE DECK METRICS</text>
    {stats_svg}
  </g>

  <g filter="url(#neonGlow)">
    <rect x="20" y="{ABILITY_TOP}" width="360" height="{ABILITY_H}" rx="12" fill="#0F172A" opacity="0.85" stroke="#1E293B" stroke-width="1"/>
    {bio_svg}
  </g>

  {pips_svg}

  <g transform="translate(24, 532)">
    <text x="0" y="0" font-family="system-ui, sans-serif" font-size="11" font-weight="900" fill="url(#cyberGrad)" letter-spacing="0.06em">{rarity['letter']} · {esc(rarity['name'])}</text>
    <text x="176" y="0" text-anchor="middle" font-family="monospace" font-size="10" font-weight="700" fill="#475569">#{esc(data['hash'])}</text>
    <text x="352" y="0" text-anchor="end" font-family="system-ui, sans-serif" font-size="10" font-weight="800" fill="#64748B">NO. {data['collector_no']:03d}/999</text>
  </g>
</svg>
"""
    return svg


# ----------------------------------------------------------------------------
# Main Implementation
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
                from io import BytesIO
                from PIL import Image

                png_bytes = cairosvg.svg2png(bytestring=svg.encode("utf-8"), output_width=800, output_height=1120)
                img = Image.open(BytesIO(png_bytes)).convert("RGBA")
                bg = Image.new("RGBA", img.size, (11, 15, 25, 255))
                bg.paste(img, mask=img)
                jpg_path = os.path.join(args.out_dir, f"{args.username}.jpg")
                bg.convert("RGB").save(jpg_path, "JPEG", quality=95)
                print(f"Wrote {jpg_path}")


if __name__ == "__main__":
    main()
