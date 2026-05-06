#!/usr/bin/env python3
"""Fetch and classify GitHub starred repositories into Chinese categories.

Outputs:
- README.md
- data/stars.json
- data/classified-stars.json
- data/new-stars.json
"""
from __future__ import annotations

import argparse
import json
import os
import pathlib
import subprocess
import time
import urllib.request
from collections import Counter
from typing import Any

try:
    import yaml
except ImportError:  # pragma: no cover
    yaml = None

ROOT = pathlib.Path(__file__).resolve().parents[1]
DATA_DIR = ROOT / "data"


def load_yaml(path: pathlib.Path) -> Any:
    if not path.exists():
        return {}
    if yaml is None:
        raise RuntimeError("PyYAML is required. Install with: pip install pyyaml")
    return yaml.safe_load(path.read_text(encoding="utf-8")) or {}


def get_login(default: str = "") -> str:
    env_user = os.getenv("GITHUB_USER") or os.getenv("GH_USER")
    if env_user:
        return env_user.strip()
    try:
        return subprocess.check_output(["gh", "api", "user", "--jq", ".login"], text=True).strip()
    except Exception:
        return default or "tyler5685"


def get_token() -> str:
    for key in ("GH_TOKEN", "GITHUB_TOKEN"):
        if os.getenv(key):
            return os.getenv(key, "").strip()
    try:
        return subprocess.check_output(["gh", "auth", "token"], text=True).strip()
    except Exception:
        return ""


def fetch_stars(user: str, token: str = "") -> list[dict[str, Any]]:
    headers = {
        "Accept": "application/vnd.github+json",
        "User-Agent": "github-stars-catalog",
    }
    if token:
        headers["Authorization"] = f"Bearer {token}"
    repos: list[dict[str, Any]] = []
    for page in range(1, 101):
        url = f"https://api.github.com/users/{user}/starred?per_page=100&page={page}"
        req = urllib.request.Request(url, headers=headers)
        with urllib.request.urlopen(req, timeout=60) as response:
            data = json.loads(response.read().decode("utf-8"))
        if not data:
            break
        for repo in data:
            repos.append(
                {
                    "full_name": repo.get("full_name"),
                    "html_url": repo.get("html_url"),
                    "description": repo.get("description") or "",
                    "language": repo.get("language") or "",
                    "topics": repo.get("topics") or [],
                    "stargazers_count": repo.get("stargazers_count") or 0,
                    "forks_count": repo.get("forks_count") or 0,
                    "archived": repo.get("archived") or False,
                    "updated_at": repo.get("updated_at") or "",
                    "owner": {
                        "login": (repo.get("owner") or {}).get("login"),
                        "type": (repo.get("owner") or {}).get("type"),
                    },
                }
            )
    return repos


def repo_text(repo: dict[str, Any]) -> str:
    return " ".join(
        [
            str(repo.get("full_name") or ""),
            str(repo.get("description") or ""),
            str(repo.get("language") or ""),
            " ".join(repo.get("topics") or []),
        ]
    ).lower()


def classify(repos: list[dict[str, Any]], categories: dict[str, list[str]], overrides: dict[str, str]) -> dict[str, list[dict[str, Any]]]:
    result: dict[str, list[dict[str, Any]]] = {name: [] for name in categories}
    result.setdefault("其他 / 综合", [])
    for repo in repos:
        full_name = str(repo.get("full_name") or "")
        if full_name in overrides:
            cat = overrides[full_name]
        else:
            text = repo_text(repo)
            topics = {str(t).lower() for t in repo.get("topics") or []}
            scores: list[tuple[int, str]] = []
            for cat_name, keywords in categories.items():
                score = 0
                for kw in keywords or []:
                    k = str(kw).lower()
                    if k in text:
                        score += 1
                    if k in topics:
                        score += 2
                if score:
                    scores.append((score, cat_name))
            cat = sorted(scores, reverse=True)[0][1] if scores else "其他 / 综合"
        result.setdefault(cat, []).append(repo)
    for items in result.values():
        items.sort(key=lambda r: int(r.get("stargazers_count") or 0), reverse=True)
    return result


def new_stars(current: list[dict[str, Any]], previous_path: pathlib.Path) -> list[dict[str, Any]]:
    if not previous_path.exists():
        return []
    try:
        old = json.loads(previous_path.read_text(encoding="utf-8"))
        old_names = {r.get("full_name") for r in old.get("repos", [])}
        return [r for r in current if r.get("full_name") not in old_names]
    except Exception:
        return []


def suggest_keywords(repos: list[dict[str, Any]], categories: dict[str, list[str]]) -> list[str]:
    known = {str(k).lower() for values in categories.values() for k in (values or [])}
    counts: Counter[str] = Counter()
    for repo in repos:
        for topic in repo.get("topics") or []:
            t = str(topic).lower()
            if t and t not in known:
                counts[t] += 1
    return [topic for topic, count in counts.most_common(20) if count >= 2]


def render_readme(user: str, classified: dict[str, list[dict[str, Any]]], new_items: list[dict[str, Any]], suggestions: list[str]) -> str:
    total = sum(len(v) for v in classified.values())
    lines: list[str] = [
        "# GitHub Stars 中文分类目录",
        "",
        f"账号：[`{user}`](https://github.com/{user})",
        f"更新时间：{time.strftime('%Y-%m-%d %H:%M:%S %Z')}",
        f"总数：**{total}**",
        "",
        "> 自动生成：GitHub Actions 每天更新。分类规则见 `categories.yml`，手动纠错见 `overrides.yml`。",
        "",
        "## 分类总览",
        "",
    ]
    for cat, items in sorted(classified.items(), key=lambda kv: len(kv[1]), reverse=True):
        if items:
            lines.append(f"- **{cat}**：{len(items)}")
    if new_items:
        lines += ["", "## 最近新增 Star", ""]
        for repo in new_items[:20]:
            lines.append(f"- [{repo['full_name']}]({repo['html_url']})")
    if suggestions:
        lines += ["", "## 待确认的新关键词建议", ""]
        lines += [f"- `{topic}`" for topic in suggestions]
    lines.append("")
    for cat, items in sorted(classified.items(), key=lambda kv: len(kv[1]), reverse=True):
        if not items:
            continue
        lines += [f"## {cat}（{len(items)}）", ""]
        for repo in items:
            desc = str(repo.get("description") or "").replace("\n", " ").strip()
            if len(desc) > 180:
                desc = desc[:177] + "..."
            topics = ", ".join((repo.get("topics") or [])[:8])
            meta = []
            if repo.get("language"):
                meta.append(str(repo["language"]))
            meta.append(f"★{repo.get('stargazers_count') or 0}")
            if topics:
                meta.append(f"topics: {topics}")
            lines.append(f"- [{repo['full_name']}]({repo['html_url']}) — {'; '.join(meta)}")
            if desc:
                lines.append(f"  - {desc}")
        lines.append("")
    return "\n".join(lines)


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--user", default="")
    args = parser.parse_args()
    user = args.user or get_login()
    DATA_DIR.mkdir(exist_ok=True)
    categories = load_yaml(ROOT / "categories.yml")
    overrides = load_yaml(ROOT / "overrides.yml")
    old_path = DATA_DIR / "stars.json"
    repos = fetch_stars(user, get_token())
    new_items = new_stars(repos, old_path)
    classified = classify(repos, categories, overrides)
    suggestions = suggest_keywords(new_items or repos, categories)
    (DATA_DIR / "stars.json").write_text(json.dumps({"user": user, "count": len(repos), "repos": repos}, ensure_ascii=False, indent=2), encoding="utf-8")
    (DATA_DIR / "classified-stars.json").write_text(json.dumps({"user": user, "count": len(repos), "summary": {k: len(v) for k, v in classified.items() if v}, "categories": classified}, ensure_ascii=False, indent=2), encoding="utf-8")
    (DATA_DIR / "new-stars.json").write_text(json.dumps({"count": len(new_items), "repos": new_items}, ensure_ascii=False, indent=2), encoding="utf-8")
    (ROOT / "README.md").write_text(render_readme(user, classified, new_items, suggestions), encoding="utf-8")
    print(f"updated {len(repos)} stars for {user}")


if __name__ == "__main__":
    main()
