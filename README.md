# git score TCG

Turns a GitHub profile into a trading-card-game style card (SVG / PNG / JPG),
sized like a standard TCG card (400×560, same 5:7 ratio as Magic/Pokémon cards).

- **Rarity border** — driven by a 0–100 score (Mythic gold / Legendary purple /
  Rare blue / Uncommon green / Common gray).
- **Type line** — top language mapped to a flavor "element" (e.g. Rust → Fire).
- **Three stat bars** — each blends a profile stat with a related activity
  stat: POW (stars / commits), DEF (followers / issues), SPD (forks / PRs).
  The bar fill is a weighted blend of both numbers' contribution to the
  score (see Scoring below); the score itself is unaffected.
- **Ability box** — bio + a generated flavor line about commits/PRs/issues.
- **Language pips** — top languages as colored dots with percentages.
- **Footer** — rarity stamp, deterministic pseudo commit-hash, collector number.

### Scoring

The score is a weighted sum out of 100:

| Factor            | Weight | Source                                   |
|-------------------|-------:|-------------------------------------------|
| Followers         |     20 | user profile                              |
| Stars received    |     20 | sum across up to 200 recently-updated repos |
| Public repos      |     10 | user profile                              |
| Account age       |     10 | linear, capped at ~7.5 years               |
| Forks received    |      8 | sum across repos                          |
| Commits           |     15 | `search/commits?q=author:<login>`          |
| Pull requests     |     10 | `search/issues?q=author:<login>+type:pr`   |
| Issues opened     |      7 | `search/issues?q=author:<login>+type:issue`|

Each of the log-scaled factors (everything but age) uses `log2(count+1) * k`,
capped at its weight, so early activity moves the needle quickly and it
levels off rather than requiring literally maxing out any one metric.

Note: commit search only indexes each repo's default branch and can lag by a
few minutes for very recent pushes — it's a good flavor stat, not an exact
audit trail. It also counts commits authored under any repo (not just the
user's own), same as GitHub's own commit-search semantics.

## 1. Run it locally

```bash
pip install -r requirements.txt

# needs libcairo installed system-wide for the PNG/JPG export step:
#   macOS:  brew install cairo
#   Ubuntu: sudo apt-get install libcairo2

python generate_card.py octocat --out-dir cards --formats svg,png
```

This writes `cards/octocat.svg` and `cards/octocat.png`. Pass a token to avoid
GitHub's unauthenticated 60-requests/hour rate limit:

```bash
python generate_card.py octocat --token ghp_xxx
```

A token matters even more here than before: the commit/PR/issue counts use
GitHub's **Search API**, which is capped at 10 requests/minute unauthenticated
but 30/minute with a token. Without one you'll likely get rate-limited after
generating just one or two cards in quick succession.

## 2. Automate it with GitHub Actions

Copy `generate_card.py`, `requirements.txt`, and
`.github/workflows/git-score-card.yml` into your repo. The workflow:

- runs once a day to keep stats fresh,
- can be triggered manually from the **Actions** tab (with an optional
  `username` input — defaults to the repo owner),
- commits the regenerated `cards/*` files back to the repo automatically.

No extra secrets are needed — it uses the built-in `GITHUB_TOKEN`.

## 3. Embed it in your README

Once the workflow has run once, reference the file directly:

```markdown
![git score card](./cards/octocat.svg)
```

Or, if you want it to always point at the latest committed version regardless
of branch, use the raw URL:

```markdown
![git score card](https://raw.githubusercontent.com/<owner>/<repo>/main/cards/octocat.svg)
```

SVG is recommended for READMEs (crisp at any size, tiny file size). PNG/JPG
are there in case you need a raster format for a platform that doesn't render
SVG.
