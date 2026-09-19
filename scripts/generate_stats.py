#!/usr/bin/env python3
"""Generate the language chart used by the GitHub profile README.

No third-party Python packages are required. Public mode uses GitHub's REST API
plus shallow clones. Supplying PROFILE_TOKEN allows owned private repositories
to be included when the token has access to them.
"""

from __future__ import annotations

import argparse
import base64
import fnmatch
import json
import math
import os
import subprocess
import tempfile
import urllib.error
import urllib.parse
import urllib.request
from collections import Counter
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


def write_assets(config: dict[str, Any], counts: Counter[str] | None) -> None:
    ASSETS.mkdir(parents=True, exist_ok=True)
    (ASSETS / "languages.svg").write_text(render_languages(config, counts) + "\n", encoding="utf-8")


def collect(config: dict[str, Any]) -> Counter[str]:
    username = config["username"]
    profile_token = os.environ.get("PROFILE_TOKEN", "").strip()
    token = profile_token or os.environ.get("GITHUB_TOKEN", "").strip()
    if not token:
        raise RuntimeError("Set GITHUB_TOKEN or PROFILE_TOKEN before generating live statistics.")
    repos = fetch_repositories(username, token, include_private=bool(profile_token))
    excluded_names = set(config.get("exclude_repositories", []))
    indexed = [
        repo for repo in repos
        if repo["name"] not in excluded_names
        and not (config.get("exclude_forks", True) and repo.get("fork"))
        and not (config.get("exclude_archived", True) and repo.get("archived"))
        and not repo.get("disabled")
    ]
    return clone_and_count(indexed, profile_token, config.get("exclude_globs", []))


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--placeholder", action="store_true", help="Generate clean placeholder assets without API access")
    args = parser.parse_args()
    config = load_config()
    counts = None if args.placeholder else collect(config)
    write_assets(config, counts)
    print("Generated: " + ", ".join(str(path.relative_to(ROOT)) for path in sorted(ASSETS.glob("*.svg"))))


if __name__ == "__main__":
    main()
