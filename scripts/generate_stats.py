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

# GitHub Linguist colors, so the chart reads like a repo language bar.
LANGUAGE_COLORS = {
    "Assembly": "#6E4C13",
    "C": "#555555",
    "C#": "#178600",
    "C++": "#f34b7d",
    "CSS": "#563d7c",
    "CUDA": "#3A4E3A",
    "Go": "#00ADD8",
    "HTML": "#e34c26",
    "Java": "#b07219",
    "JavaScript": "#f1e05a",
    "Jupyter": "#DA5B0B",
    "Kotlin": "#A97BFF",
    "Lua": "#000080",
    "MATLAB/Obj-C": "#e16737",
    "PHP": "#4F5D95",
    "Python": "#3572A5",
    "R": "#198CE7",
    "Ruby": "#701516",
    "Rust": "#dea584",
    "SQL": "#e38c00",
    "Scala": "#c22d40",
    "Shell": "#89e051",
    "Swift": "#F05138",
    "SystemVerilog": "#DAE1C2",
    "Tcl": "#e4cc98",
    "TypeScript": "#3178c6",
    "VHDL": "#adb2cb",
    "Verilog": "#b2b7f8",
    "Other": "#8b949e",
}


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


def language_color(name: str) -> str:
    return LANGUAGE_COLORS.get(name, "#8b949e")


def language_slices(counts: Counter[str], limit: int = 6) -> list[tuple[str, int]]:
    most = counts.most_common(limit)
    remainder = sum(counts.values()) - sum(value for _, value in most)
    if remainder:
        most.append(("Other", remainder))
    return most


def render_languages(_config: dict[str, Any], counts: Counter[str] | None) -> str:
    pending = not counts or sum(counts.values()) == 0
    values = [("Pending", 1)] if pending else language_slices(counts)
    total = sum(value for _, value in values)
    width, height = 800, 164
    pad_x, bar_y, bar_h = 28, 58, 8
    bar_w = width - pad_x * 2
    segments = []
    legend = []
    cursor = float(pad_x)
    for index, (name, value) in enumerate(values):
        color = language_color(name)
        share = value / total
        seg_w = bar_w if pending else bar_w * share
        segments.append(
            f'<rect x="{cursor:.2f}" y="{bar_y}" width="{seg_w:.2f}" height="{bar_h}" fill="{color}"/>'
        )
        cursor += seg_w
        col, row = index % 4, index // 4
        lx = pad_x + col * 188
        ly = 98 + row * 36
        pct = "—" if pending else f"{share * 100:.1f}%"
        legend.append(
            f'<circle cx="{lx + 5}" cy="{ly}" r="4" fill="{color}"/>'
            f'<text x="{lx + 16}" y="{ly + 4}" class="label">{escape(name)}</text>'
            f'<text x="{lx + 168}" y="{ly + 4}" class="muted" text-anchor="end">{pct}</text>'
        )
    lines = "Updating" if pending else f"{compact_number(total)} lines"
    return f'''<svg xmlns="http://www.w3.org/2000/svg" width="{width}" height="{height}" viewBox="0 0 {width} {height}" role="img" aria-labelledby="title desc">
<title id="title">Languages</title>
<desc id="desc">Source-line language mix across owned repositories.</desc>
<style>
  .title {{ font: 600 16px ui-sans-serif, -apple-system, BlinkMacSystemFont, "Segoe UI", Helvetica, Arial, sans-serif; fill: #e6edf3; }}
  .muted {{ font: 12px ui-sans-serif, -apple-system, BlinkMacSystemFont, "Segoe UI", Helvetica, Arial, sans-serif; fill: #8b949e; }}
  .label {{ font: 13px ui-sans-serif, -apple-system, BlinkMacSystemFont, "Segoe UI", Helvetica, Arial, sans-serif; fill: #e6edf3; }}
</style>
<defs>
  <clipPath id="bar"><rect x="{pad_x}" y="{bar_y}" width="{bar_w}" height="{bar_h}" rx="4"/></clipPath>
</defs>
<rect width="{width}" height="{height}" rx="6" fill="#0d1117"/>
<rect x=".5" y=".5" width="{width - 1}" height="{height - 1}" rx="5.5" fill="none" stroke="#30363d"/>
<text x="{pad_x}" y="34" class="title">Languages</text>
<text x="{width - pad_x}" y="34" class="muted" text-anchor="end">{escape(lines)}</text>
<rect x="{pad_x}" y="{bar_y}" width="{bar_w}" height="{bar_h}" rx="4" fill="#21262d"/>
<g clip-path="url(#bar)">{''.join(segments)}</g>
{''.join(legend)}
</svg>'''


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
