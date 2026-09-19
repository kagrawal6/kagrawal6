#!/usr/bin/env python3
"""Generate the SVG dashboard used by the GitHub profile README.

No third-party Python packages are required. Public mode uses GitHub's REST and
GraphQL APIs plus shallow clones. Supplying PROFILE_TOKEN allows owned private
repositories to be included when the token has access to them.
"""

from __future__ import annotations

import argparse
import base64
import fnmatch
import json
import math
import os
import shutil
import subprocess
import tempfile
import urllib.error
import urllib.parse
import urllib.request
from collections import Counter
from datetime import datetime, timezone
from pathlib import Path
from typing import Any
from xml.sax.saxutils import escape


ROOT = Path(__file__).resolve().parents[1]
CONFIG_PATH = ROOT / "profile-config.json"
ASSETS = ROOT / "assets"
API = "https://api.github.com"

EXTENSIONS = {
    ".c": "C", ".h": "C", ".cc": "C++", ".cpp": "C++", ".cxx": "C++",
    ".hpp": "C++", ".hh": "C++", ".cu": "CUDA", ".cuh": "CUDA",
    ".sv": "SystemVerilog", ".svh": "SystemVerilog", ".v": "Verilog",
    ".vh": "Verilog", ".vhd": "VHDL", ".vhdl": "VHDL",
    ".py": "Python", ".pyi": "Python", ".ipynb": "Jupyter",
    ".ts": "TypeScript", ".tsx": "TypeScript", ".js": "JavaScript",
    ".jsx": "JavaScript", ".mjs": "JavaScript", ".java": "Java",
    ".rs": "Rust", ".go": "Go", ".cs": "C#", ".tcl": "Tcl",
    ".sh": "Shell", ".bash": "Shell", ".zsh": "Shell",
    ".s": "Assembly", ".asm": "Assembly", ".sql": "SQL",
    ".html": "HTML", ".htm": "HTML", ".css": "CSS", ".scss": "SCSS",
    ".rb": "Ruby", ".php": "PHP", ".swift": "Swift", ".kt": "Kotlin",
    ".scala": "Scala", ".lua": "Lua", ".r": "R", ".m": "MATLAB/Obj-C",
}

PALETTE = ["#6EE7F2", "#8B9FFF", "#B08CFF", "#5DC7A1", "#F0B86E", "#F17C8D", "#6F809B"]
BG = "#071019"
PANEL = "#0B1622"
PANEL_2 = "#0E1C2A"
LINE = "#233547"
TEXT = "#EAF2F8"
MUTED = "#8495A7"


def load_config() -> dict[str, Any]:
    return json.loads(CONFIG_PATH.read_text(encoding="utf-8"))


def request_json(url: str, token: str, data: dict[str, Any] | None = None) -> Any:
    body = json.dumps(data).encode() if data is not None else None
    headers = {
        "Accept": "application/vnd.github+json",
        "User-Agent": "profile-dashboard-generator",
        "X-GitHub-Api-Version": "2022-11-28",
    }
    if token:
        headers["Authorization"] = f"Bearer {token}"
    request = urllib.request.Request(url, data=body, headers=headers, method="POST" if body else "GET")
    try:
        with urllib.request.urlopen(request, timeout=45) as response:
            return json.load(response)
    except urllib.error.HTTPError as exc:
        detail = exc.read().decode("utf-8", errors="replace")
        raise RuntimeError(f"GitHub API returned {exc.code} for {url}: {detail[:400]}") from exc


def fetch_repositories(username: str, token: str, include_private: bool) -> list[dict[str, Any]]:
    repos: list[dict[str, Any]] = []
    page = 1
    while True:
        if include_private:
            query = urllib.parse.urlencode({
                "affiliation": "owner", "visibility": "all", "per_page": 100,
                "page": page, "sort": "full_name",
            })
            url = f"{API}/user/repos?{query}"
        else:
            query = urllib.parse.urlencode({"type": "owner", "per_page": 100, "page": page, "sort": "full_name"})
            url = f"{API}/users/{username}/repos?{query}"
        batch = request_json(url, token)
        if not batch:
            break
        repos.extend(repo for repo in batch if repo.get("owner", {}).get("login", "").lower() == username.lower())
        if len(batch) < 100:
            break
        page += 1
    return repos


def contribution_year(username: str, token: str, year: int, now: datetime) -> tuple[int, int]:
    start = datetime(year, 1, 1, tzinfo=timezone.utc)
    end = min(datetime(year + 1, 1, 1, tzinfo=timezone.utc), now)
    if end <= start:
        return 0, 0
    query = """
    query($login: String!, $from: DateTime!, $to: DateTime!) {
      user(login: $login) {
        contributionsCollection(from: $from, to: $to) {
          totalCommitContributions
          contributionCalendar { totalContributions }
        }
      }
    }
    """
    payload = {
        "query": query,
        "variables": {
            "login": username,
            "from": start.isoformat().replace("+00:00", "Z"),
            "to": end.isoformat().replace("+00:00", "Z"),
        },
    }
    result = request_json(f"{API}/graphql", token, payload)
    if result.get("errors"):
        raise RuntimeError(f"GitHub GraphQL error: {result['errors']}")
    collection = result["data"]["user"]["contributionsCollection"]
    return int(collection["totalCommitContributions"]), int(collection["contributionCalendar"]["totalContributions"])


def fetch_contributions(username: str, token: str, created_at: str) -> tuple[int, dict[int, int]]:
    now = datetime.now(timezone.utc)
    first_year = datetime.fromisoformat(created_at.replace("Z", "+00:00")).year
    lifetime_commits = 0
    yearly: dict[int, int] = {}
    for year in range(first_year, now.year + 1):
        commits, total = contribution_year(username, token, year, now)
        lifetime_commits += commits
        yearly[year] = total
    return lifetime_commits, yearly


def is_excluded(path: str, patterns: list[str]) -> bool:
    normalized = path.replace("\\", "/")
    padded = f"/{normalized}"
    return any(fnmatch.fnmatch(normalized, pattern) or fnmatch.fnmatch(padded, f"/{pattern}") for pattern in patterns)


def source_line_count(path: Path, language: str) -> int:
    try:
        if path.stat().st_size > 2_000_000:
            return 0
        text = path.read_text(encoding="utf-8", errors="ignore")
    except OSError:
        return 0
    line_markers = ("//", "#")
    if language in {"VHDL", "SQL"}:
        line_markers = ("--",)
    elif language in {"HTML", "CSS", "SCSS"}:
        line_markers = tuple()
    in_block = False
    count = 0
    for raw in text.splitlines():
        line = raw.strip()
        if not line:
            continue
        if in_block:
            if "*/" in line:
                in_block = False
                line = line.split("*/", 1)[1].strip()
                if not line:
                    continue
            else:
                continue
        if line.startswith("/*") or line.startswith("<!--"):
            closer = "-->" if line.startswith("<!--") else "*/"
            if closer not in line:
                in_block = True
            continue
        if any(line.startswith(marker) for marker in line_markers):
            continue
        count += 1
    return count


def clone_and_count(repos: list[dict[str, Any]], token: str, exclude_globs: list[str]) -> Counter[str]:
    counts: Counter[str] = Counter()
    basic = base64.b64encode(f"x-access-token:{token}".encode()).decode() if token else ""
    with tempfile.TemporaryDirectory(prefix="profile-stats-") as temp:
        temp_root = Path(temp)
        for index, repo in enumerate(repos):
            destination = temp_root / f"repo-{index}"
            command = ["git"]
            if token:
                command += ["-c", f"http.extraHeader=AUTHORIZATION: basic {basic}"]
            command += ["clone", "--depth=1", "--quiet", repo["clone_url"], str(destination)]
            result = subprocess.run(command, capture_output=True, text=True, timeout=180)
            if result.returncode != 0:
                print(f"warning: could not clone {repo['full_name']}; skipping LOC analysis")
                continue
            tracked = subprocess.run(
                ["git", "-C", str(destination), "ls-files", "-z"],
                capture_output=True, check=True,
            ).stdout.decode("utf-8", errors="replace").split("\0")
            for relative in tracked:
                if not relative or is_excluded(relative, exclude_globs):
                    continue
                language = EXTENSIONS.get(Path(relative).suffix.lower())
                if language:
                    counts[language] += source_line_count(destination / relative, language)
    return counts


def compact_number(value: int | None) -> str:
    if value is None:
        return "—"
    if value >= 1_000_000:
        number = value / 1_000_000
        return f"{number:.1f}M" if number < 10 else f"{number:.0f}M"
    if value >= 10_000:
        return f"{value / 1000:.0f}K"
    if value >= 1_000:
        number = value / 1000
        return f"{number:.1f}K" if number < 10 else f"{number:.0f}K"
    return f"{value:,}"


def svg_shell(width: int, height: int, body: str, accent: str, title: str) -> str:
    return f'''<svg xmlns="http://www.w3.org/2000/svg" width="{width}" height="{height}" viewBox="0 0 {width} {height}" role="img" aria-labelledby="title desc">
<title id="title">{escape(title)}</title>
<desc id="desc">Generated GitHub profile analytics for Kushal Agrawal.</desc>
<defs>
  <linearGradient id="bg" x1="0" y1="0" x2="1" y2="1"><stop stop-color="#071019"/><stop offset="1" stop-color="#0B1622"/></linearGradient>
  <linearGradient id="accent" x1="0" y1="0" x2="1" y2="0"><stop stop-color="{accent}"/><stop offset="1" stop-color="#8B9FFF"/></linearGradient>
  <pattern id="grid" width="32" height="32" patternUnits="userSpaceOnUse"><path d="M32 0H0V32" fill="none" stroke="#294055" stroke-opacity=".16"/></pattern>
  <filter id="glow" x="-30%" y="-30%" width="160%" height="160%"><feGaussianBlur stdDeviation="5" result="blur"/><feMerge><feMergeNode in="blur"/><feMergeNode in="SourceGraphic"/></feMerge></filter>
  <style>
    .sans{{font-family:Inter,ui-sans-serif,-apple-system,BlinkMacSystemFont,"Segoe UI",sans-serif}}
    .mono{{font-family:"SFMono-Regular",Consolas,"Liberation Mono",monospace}}
    .title{{fill:{TEXT};font-size:24px;font-weight:650;letter-spacing:1.2px}}
    .eyebrow{{fill:{accent};font-size:12px;font-weight:700;letter-spacing:3px}}
    .muted{{fill:{MUTED}}}
  </style>
</defs>
<rect width="{width}" height="{height}" rx="24" fill="url(#bg)"/>
<rect width="{width}" height="{height}" rx="24" fill="url(#grid)"/>
<rect x=".5" y=".5" width="{width-1}" height="{height-1}" rx="23.5" fill="none" stroke="#26394B"/>
{body}
</svg>'''


def render_header(config: dict[str, Any]) -> str:
    accent = config["accent"]
    body = f'''
<path d="M70 66H270M70 74H190" stroke="{accent}" stroke-width="2" stroke-linecap="round" opacity=".75"/>
<text x="70" y="126" class="sans" fill="{TEXT}" font-size="54" font-weight="720" letter-spacing="-1">{escape(config['name'])}</text>
<text x="72" y="167" class="mono" fill="{accent}" font-size="14" font-weight="700" letter-spacing="1.7">{escape(config['headline'])}</text>
<text x="72" y="207" class="sans" fill="{MUTED}" font-size="17" letter-spacing=".5">{escape(config['institution'])}</text>
<text x="72" y="253" class="mono" fill="#50657A" font-size="12" letter-spacing="2">BUILDING BENEATH THE ABSTRACTION</text>

<g transform="translate(940 70)">
  <path d="M0 60L92 8l92 52-92 52z" fill="#111F2D" stroke="#395169"/>
  <path d="M0 60v48l92 52v-48z" fill="#09131E" stroke="#2B4054"/>
  <path d="M184 60v48l-92 52v-48z" fill="#0D1A27" stroke="#2B4054"/>
  <path d="M29 60L92 24l63 36-63 36z" fill="#132637" stroke="{accent}" stroke-width="1.5"/>
  <path d="M58 60l34-19 34 19-34 19z" fill="{accent}" opacity=".16" stroke="{accent}" filter="url(#glow)"/>
  <g stroke="{accent}" stroke-width="2" opacity=".8">
    <path d="M29 45L3 30H-14"/><path d="M29 75L3 90H-14"/>
    <path d="M155 45l26-15h17"/><path d="M155 75l26 15h17"/>
    <path d="M72 35V16L56 7"/><path d="M112 35V16l16-9"/>
  </g>
  <g fill="{accent}"><circle cx="-14" cy="30" r="3"/><circle cx="-14" cy="90" r="3"/><circle cx="198" cy="30" r="3"/><circle cx="198" cy="90" r="3"/></g>
</g>'''
    return svg_shell(1200, 300, body, accent, "Kushal Agrawal profile header")


def render_telemetry(config: dict[str, Any], metrics: list[tuple[str, str]]) -> str:
    accent = config["accent"]
    cards = []
    for index, (label, value) in enumerate(metrics):
        col, row = index % 3, index // 3
        x, y = 54 + col * 382, 102 + row * 112
        cards.append(f'''
<g transform="translate({x} {y})">
  <path d="M0 0H344L354 10V86H10L0 76Z" fill="#050B12" opacity=".68"/>
  <path d="M0 0H344V76H0Z" fill="{PANEL_2}" stroke="{LINE}"/>
  <path d="M0 0H5V76H0Z" fill="{accent}" opacity="{0.95-index*.08:.2f}"/>
  <text x="24" y="30" class="mono muted" font-size="11" font-weight="700" letter-spacing="1.6">{escape(label)}</text>
  <text x="24" y="60" class="sans" fill="{TEXT}" font-size="27" font-weight="700">{escape(value)}</text>
</g>''')
    body = f'''
<text x="54" y="49" class="eyebrow mono">ENGINEERING TELEMETRY</text>
<text x="54" y="78" class="title sans">A measurable view of the work</text>
<text x="1146" y="55" class="mono muted" font-size="11" text-anchor="end">AUTO-REFRESH / DAILY</text>
{''.join(cards)}'''
    return svg_shell(1200, 340, body, accent, "Engineering telemetry")


def polar(cx: float, cy: float, radius: float, angle: float) -> tuple[float, float]:
    radians = math.radians(angle - 90)
    return cx + radius * math.cos(radians), cy + radius * math.sin(radians)


def donut_path(cx: float, cy: float, outer: float, inner: float, start: float, end: float) -> str:
    x1, y1 = polar(cx, cy, outer, start)
    x2, y2 = polar(cx, cy, outer, end)
    x3, y3 = polar(cx, cy, inner, end)
    x4, y4 = polar(cx, cy, inner, start)
    large = 1 if end - start > 180 else 0
    return f"M{x1:.2f},{y1:.2f} A{outer},{outer} 0 {large},1 {x2:.2f},{y2:.2f} L{x3:.2f},{y3:.2f} A{inner},{inner} 0 {large},0 {x4:.2f},{y4:.2f} Z"


def language_slices(counts: Counter[str], limit: int = 6) -> list[tuple[str, int]]:
    most = counts.most_common(limit)
    remainder = sum(counts.values()) - sum(value for _, value in most)
    if remainder:
        most.append(("Other", remainder))
    return most


def render_languages(config: dict[str, Any], counts: Counter[str] | None) -> str:
    accent = config["accent"]
    pending = not counts or sum(counts.values()) == 0
    values = [("Pending", 1)] if pending else language_slices(counts)
    total = sum(value for _, value in values)
    angle = 0.0
    arcs = []
    legend = []
    bars = []
    max_value = max(value for _, value in values)
    for index, (name, value) in enumerate(values):
        color = PALETTE[index % len(PALETTE)]
        sweep = value / total * 360
        gap = min(1.5, sweep / 8)
        arcs.append(f'<path d="{donut_path(260, 294, 126, 82, angle + gap, angle + sweep - gap)}" fill="{color}"/>')
        angle += sweep
        pct = value / total * 100
        ly = 169 + index * 42
        legend.append(f'<rect x="430" y="{ly-11}" width="10" height="10" rx="2" fill="{color}"/><text x="452" y="{ly}" class="sans" fill="{TEXT}" font-size="14">{escape(name)}</text><text x="622" y="{ly}" class="mono muted" font-size="12" text-anchor="end">{"—" if pending else f"{pct:.1f}%"}</text>')
        if index < 6:
            height = 34 if pending else 34 + int(160 * value / max_value)
            bx = 755 + index * 62
            by = 405 - height
            bars.append(f'''
<g>
  <path d="M{bx} {by}l10-7h34l-10 7z" fill="{color}" opacity=".92"/>
  <path d="M{bx+34} {by}l10-7v{height}l-10 7z" fill="{color}" opacity=".42"/>
  <rect x="{bx}" y="{by}" width="34" height="{height}" fill="{color}" opacity=".72"/>
  <text x="{bx+17}" y="432" class="mono muted" font-size="10" text-anchor="middle">{escape(name[:5].upper())}</text>
</g>''')
    center_text = "SYNC" if pending else compact_number(total)
    center_sub = "PENDING" if pending else "SOURCE LINES"
    body = f'''
<text x="54" y="49" class="eyebrow mono">LANGUAGE DISTRIBUTION</text>
<text x="54" y="78" class="title sans">Codebase composition</text>
<text x="1146" y="55" class="mono muted" font-size="11" text-anchor="end">LOC / OWNED REPOSITORIES</text>
<line x1="678" y1="122" x2="678" y2="447" stroke="{LINE}"/>
<circle cx="260" cy="294" r="126" fill="#0B1622" stroke="{LINE}"/>
{''.join(arcs)}
<circle cx="260" cy="294" r="80" fill="{BG}" stroke="{LINE}"/>
<text x="260" y="290" class="sans" fill="{TEXT}" font-size="28" font-weight="700" text-anchor="middle">{center_text}</text>
<text x="260" y="316" class="mono muted" font-size="10" letter-spacing="1.5" text-anchor="middle">{center_sub}</text>
{''.join(legend)}
<text x="755" y="139" class="mono" fill="{accent}" font-size="11" font-weight="700" letter-spacing="1.6">RELATIVE SOURCE FOOTPRINT</text>
<path d="M735 405H1132" stroke="{LINE}"/><path d="M735 405l20-14h397" stroke="{LINE}" opacity=".55"/>
{''.join(bars)}'''
    return svg_shell(1200, 500, body, accent, "Language distribution")


def render_activity(config: dict[str, Any], yearly: dict[int, int] | None) -> str:
    accent = config["accent"]
    now_year = datetime.now(timezone.utc).year
    years = list(range(now_year - 5, now_year + 1))
    pending = not yearly
    values = [0 if pending else yearly.get(year, 0) for year in years]
    maximum = max(values) if max(values, default=0) else 1
    bars = []
    points = []
    for index, (year, value) in enumerate(zip(years, values)):
        x = 112 + index * 176
        height = 34 if pending else max(8, round(value / maximum * 190))
        y = 309 - height
        color = PALETTE[index % 4]
        bars.append(f'''
<g>
  <path d="M{x} {y}l14-10h70l-14 10z" fill="{color}" opacity=".9"/>
  <path d="M{x+70} {y}l14-10v{height}l-14 10z" fill="{color}" opacity=".36"/>
  <rect x="{x}" y="{y}" width="70" height="{height}" fill="{color}" opacity=".7"/>
  <text x="{x+35}" y="340" class="mono muted" font-size="11" text-anchor="middle">{year}</text>
  <text x="{x+35}" y="{max(116, y-18)}" class="mono" fill="{TEXT}" font-size="12" font-weight="700" text-anchor="middle">{"—" if pending else compact_number(value)}</text>
</g>''')
        points.append(f"{x+35},{y-10}")
    body = f'''
<text x="54" y="49" class="eyebrow mono">CONTRIBUTION SIGNAL</text>
<text x="54" y="78" class="title sans">Six-year activity</text>
<text x="1146" y="55" class="mono muted" font-size="11" text-anchor="end">ALL GITHUB CONTRIBUTION TYPES</text>
<path d="M82 309H1120" stroke="{LINE}"/><path d="M82 309l22-15h1038" stroke="{LINE}" opacity=".45"/>
{''.join(bars)}
<polyline points="{' '.join(points)}" fill="none" stroke="{accent}" stroke-width="2" stroke-linejoin="round" opacity=".8"/>
{''.join(f'<circle cx="{point.split(",")[0]}" cy="{point.split(",")[1]}" r="3.5" fill="{accent}"/>' for point in points)}'''
    return svg_shell(1200, 390, body, accent, "Six-year GitHub activity")


def write_assets(config: dict[str, Any], data: dict[str, Any] | None) -> None:
    ASSETS.mkdir(parents=True, exist_ok=True)
    if data is None:
        metrics = [(label, "—") for label in ["LIFETIME COMMITS", "SOURCE LINES", "PUBLIC REPOS", "STARS EARNED", "THIS YEAR", "LANGUAGES"]]
        counts = None
        yearly = None
    else:
        counts = data["language_counts"]
        yearly = data["yearly"]
        current_year = datetime.now(timezone.utc).year
        metrics = [
            ("LIFETIME COMMITS", compact_number(data["lifetime_commits"])),
            ("SOURCE LINES", compact_number(sum(counts.values()))),
            ("PUBLIC REPOS", compact_number(data["public_repos"])),
            ("STARS EARNED", compact_number(data["stars"])),
            ("THIS YEAR", compact_number(yearly.get(current_year, 0))),
            ("LANGUAGES", compact_number(len(counts))),
        ]
    files = {
        "header.svg": render_header(config),
        "telemetry.svg": render_telemetry(config, metrics),
        "languages.svg": render_languages(config, counts),
        "activity.svg": render_activity(config, yearly),
    }
    for filename, content in files.items():
        (ASSETS / filename).write_text(content + "\n", encoding="utf-8")


def collect(config: dict[str, Any]) -> dict[str, Any]:
    username = config["username"]
    profile_token = os.environ.get("PROFILE_TOKEN", "").strip()
    token = profile_token or os.environ.get("GITHUB_TOKEN", "").strip()
    if not token:
        raise RuntimeError("Set GITHUB_TOKEN or PROFILE_TOKEN before generating live statistics.")
    user = request_json(f"{API}/users/{username}", token)
    repos = fetch_repositories(username, token, include_private=bool(profile_token))
    excluded_names = set(config.get("exclude_repositories", []))
    indexed = [
        repo for repo in repos
        if repo["name"] not in excluded_names
        and not (config.get("exclude_forks", True) and repo.get("fork"))
        and not (config.get("exclude_archived", True) and repo.get("archived"))
        and not repo.get("disabled")
    ]
    counts = clone_and_count(indexed, profile_token, config.get("exclude_globs", []))
    lifetime_commits, yearly = fetch_contributions(username, token, user["created_at"])
    return {
        "lifetime_commits": lifetime_commits,
        "yearly": yearly,
        "language_counts": counts,
        "public_repos": int(user["public_repos"]),
        "stars": sum(int(repo.get("stargazers_count", 0)) for repo in indexed),
    }


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--placeholder", action="store_true", help="Generate clean placeholder assets without API access")
    args = parser.parse_args()
    config = load_config()
    data = None if args.placeholder else collect(config)
    write_assets(config, data)
    print("Generated: " + ", ".join(str(path.relative_to(ROOT)) for path in sorted(ASSETS.glob("*.svg"))))


if __name__ == "__main__":
    main()
