#!/usr/bin/env python3
"""Generate the language chart used by the GitHub profile README.

No third-party Python packages are required. Public mode uses GitHub's REST API
plus clones. Supplying PROFILE_TOKEN allows owned private repositories to be
included when the token has access to them.

The headline count is source lines ever committed (insertions across
history, excluding merge commits). The percentage split is the current
tree only.
"""

from __future__ import annotations

import argparse
import base64
import fnmatch
import json
import math
import os
import re
import subprocess
import tempfile
import urllib.error
import urllib.parse
import urllib.request
from collections import Counter
from dataclasses import dataclass
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
    "SCSS": "#c6538c",
    "SQL": "#e38c00",
    "Scala": "#c22d40",
    "Shell": "#89e051",
    "Swift": "#F05138",
    "SystemVerilog": "#DAE1C2",
    "Tcl": "#e4cc98",
    "TypeScript": "#3178c6",
    "VHDL": "#adb2cb",
    "Verilog": "#b2b7f8",
}

HDL_LANGUAGES = {"SystemVerilog", "Verilog", "VHDL"}
MARKUP_LANGUAGES = {"HTML", "CSS", "SCSS"}


@dataclass
class ProfileStats:
    current: Counter[str]
    lifetime_lines: int
    repo_count: int
    size_kb: int


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


def language_for_path(path: str) -> str | None:
    return EXTENSIONS.get(Path(path).suffix.lower())


def max_file_bytes(language: str) -> int:
    if language in HDL_LANGUAGES:
        return 400_000
    if language in MARKUP_LANGUAGES:
        return 100_000
    return 1_000_000


def source_line_count(path: Path, language: str) -> int:
    try:
        if path.stat().st_size > max_file_bytes(language):
            return 0
        text = path.read_text(encoding="utf-8", errors="ignore")
    except OSError:
        return 0
    line_markers = ("//", "#")
    if language in {"VHDL", "SQL"}:
        line_markers = ("--",)
    elif language in MARKUP_LANGUAGES:
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


def normalize_numstat_path(path: str) -> str:
    cleaned = path.strip().strip("{}")
    if " => " in cleaned:
        cleaned = cleaned.split(" => ", 1)[1]
    return cleaned.replace("\\", "/")


def count_current_tree(repo: Path, exclude_globs: list[str]) -> Counter[str]:
    tracked = subprocess.run(
        ["git", "-C", str(repo), "ls-files", "-z"],
        capture_output=True, check=True, timeout=60,
    ).stdout.decode("utf-8", errors="replace").split("\0")
    counts: Counter[str] = Counter()
    for relative in tracked:
        if not relative or is_excluded(relative, exclude_globs):
            continue
        language = language_for_path(relative)
        if language:
            counts[language] += source_line_count(repo / relative, language)
    return counts


def count_lifetime_insertions(repo: Path, exclude_globs: list[str]) -> int:
    result = subprocess.run(
        ["git", "-C", str(repo), "log", "--all", "--no-merges", "--numstat", "--pretty=tformat:"],
        capture_output=True, check=True, timeout=180,
    )
    total = 0
    for line in result.stdout.decode("utf-8", errors="replace").splitlines():
        if not line.strip():
            continue
        parts = line.split("\t", 2)
        if len(parts) != 3 or parts[0] == "-":
            continue
        path = normalize_numstat_path(parts[2])
        if is_excluded(path, exclude_globs) or language_for_path(path) is None:
            continue
        total += int(parts[0])
    return total


def git_env(token: str) -> dict[str, str]:
    env = os.environ.copy()
    env["GIT_TERMINAL_PROMPT"] = "0"
    if token:
        env["GCM_INTERACTIVE"] = "never"
    return env


def analyze_repositories(repos: list[dict[str, Any]], token: str, exclude_globs: list[str]) -> ProfileStats:
    current: Counter[str] = Counter()
    lifetime_lines = 0
    basic = base64.b64encode(f"x-access-token:{token}".encode()).decode() if token else ""
    with tempfile.TemporaryDirectory(prefix="profile-stats-") as temp:
        temp_root = Path(temp)
        for index, repo in enumerate(repos):
            destination = temp_root / f"repo-{index}"
            command = ["git"]
            if token:
                command += ["-c", f"http.extraHeader=AUTHORIZATION: basic {basic}"]
            command += ["clone", "--quiet", "--single-branch", repo["clone_url"], str(destination)]
            result = subprocess.run(command, capture_output=True, text=True, timeout=300, env=git_env(token))
            if result.returncode != 0:
                print(f"warning: could not clone {repo['full_name']}; skipping LOC analysis")
                continue
            head = count_current_tree(destination, exclude_globs)
            committed = count_lifetime_insertions(destination, exclude_globs)
            current.update(head)
            lifetime_lines += committed
            print(f"{repo['full_name']}: {sum(head.values()):,} current lines, {committed:,} committed")
    return ProfileStats(
        current=current,
        lifetime_lines=lifetime_lines,
        repo_count=len(repos),
        size_kb=sum(int(repo.get("size") or 0) for repo in repos),
    )


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


def language_rows(counts: Counter[str]) -> list[tuple[str, int]]:
    return [(name, value) for name, value in counts.most_common() if value > 0]


def format_storage(size_kb: int) -> str:
    gigabytes = size_kb / (1024 * 1024)
    if gigabytes >= 10:
        return f"{gigabytes:.1f} GB"
    if gigabytes >= 1:
        return f"{gigabytes:.2f} GB"
    megabytes = size_kb / 1024
    if megabytes >= 10:
        return f"{megabytes:.0f} MB"
    if megabytes >= 1:
        return f"{megabytes:.1f} MB"
    return f"{size_kb} KB"


def format_repo_count(count: int) -> str:
    return "1 repo" if count == 1 else f"{count} repos"


def format_share(share: float) -> str:
    percent = share * 100
    if percent < 0.1:
        return "<0.1%"
    return f"{percent:.1f}%"


def render_languages(stats: ProfileStats | None) -> str:
    pending = stats is None or not stats.current
    values = [("Pending", 1)] if pending else language_rows(stats.current)
    total = sum(value for _, value in values)
    columns = 3
    rows = max(1, math.ceil(len(values) / columns))
    width, pad_x, bar_y, bar_h = 800, 28, 58, 8
    col_w = (width - pad_x * 2) / columns
    height = 88 + rows * 32
    bar_w = width - pad_x * 2
    segments = []
    legend = []
    cursor = float(pad_x)
    for index, (name, value) in enumerate(values):
        color = language_color(name)
        share = value / total
        seg_w = bar_w if pending else max(bar_w * share, 0)
        segments.append(
            f'<rect x="{cursor:.2f}" y="{bar_y}" width="{seg_w:.2f}" height="{bar_h}" fill="{color}"/>'
        )
        cursor += seg_w
        col, row = index % columns, index // columns
        lx = pad_x + col * col_w
        ly = 94 + row * 32
        pct = "—" if pending else format_share(share)
        legend.append(
            f'<circle cx="{lx + 5:.1f}" cy="{ly}" r="4" fill="{color}"/>'
            f'<text x="{lx + 16:.1f}" y="{ly + 4}" class="label">{escape(name)}</text>'
            f'<text x="{lx + col_w - 8:.1f}" y="{ly + 4}" class="muted" text-anchor="end">{escape(pct)}</text>'
        )
    if pending or stats is None:
        summary = "Updating"
    else:
        summary = (
            f"{compact_number(stats.lifetime_lines)} lines"
            f"  ·  {format_repo_count(stats.repo_count)}"
            f"  ·  {format_storage(stats.size_kb)}"
        )
    return f'''<svg xmlns="http://www.w3.org/2000/svg" width="{width}" height="{height}" viewBox="0 0 {width} {height}" role="img" aria-labelledby="title desc">
<title id="title">Languages</title>
<desc id="desc">Current source mix by language, with lifetime committed source lines.</desc>
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
<text x="{width - pad_x}" y="34" class="muted" text-anchor="end">{escape(summary)}</text>
<rect x="{pad_x}" y="{bar_y}" width="{bar_w}" height="{bar_h}" rx="4" fill="#21262d"/>
<g clip-path="url(#bar)">{''.join(segments)}</g>
{''.join(legend)}
</svg>'''


def bust_readme_cache() -> None:
    readme = ROOT / "README.md"
    text = readme.read_text(encoding="utf-8")
    stamp = datetime.now(timezone.utc).strftime("%Y%m%d%H%M%S")
    updated, count = re.subn(
        r'(src="\./assets/languages\.svg)(?:\?[^"]*)?(")',
        rf"\1?v={stamp}\2",
        text,
        count=1,
    )
    if count:
        readme.write_text(updated, encoding="utf-8")


def write_assets(stats: ProfileStats | None) -> None:
    ASSETS.mkdir(parents=True, exist_ok=True)
    (ASSETS / "languages.svg").write_text(render_languages(stats) + "\n", encoding="utf-8")
    bust_readme_cache()


def collect(config: dict[str, Any]) -> ProfileStats:
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
    return analyze_repositories(indexed, profile_token, config.get("exclude_globs", []))


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--placeholder", action="store_true", help="Generate clean placeholder assets without API access")
    args = parser.parse_args()
    config = load_config()
    stats = None if args.placeholder else collect(config)
    write_assets(stats)
    print("Generated: " + ", ".join(str(path.relative_to(ROOT)) for path in sorted(ASSETS.glob("*.svg"))))


if __name__ == "__main__":
    main()
