from __future__ import annotations

import argparse
import html
import json
import os
import re
import subprocess
import sys
import tomllib
import urllib.error
import urllib.parse
import urllib.request
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
from convert import CONTENT_DIR, LATEX_DIR, ROOT, TYPES, locate, parse_sidecar

API = "https://connect.mailerlite.com/api"
PUBLIC_DIR = ROOT / "public"
MATH_DELIMS = re.compile(r"\\[()\[\]]")
TITLE_RE = re.compile(r"<title>(.*?)</title>", re.S)


def api(method: str, path: str, token: str, body: dict | None = None, params: dict | None = None) -> dict:
    url = API + path + ("?" + urllib.parse.urlencode(params) if params else "")
    data = json.dumps(body).encode() if body is not None else None
    req = urllib.request.Request(
        url,
        data=data,
        method=method,
        headers={
            "Authorization": f"Bearer {token}",
            "Content-Type": "application/json",
            "Accept": "application/json",
        },
    )
    try:
        with urllib.request.urlopen(req, timeout=60) as r:
            raw = r.read()
    except urllib.error.HTTPError as e:
        raise RuntimeError(f"{method} {path}: {e.code} {e.read().decode(errors='replace')}")
    return json.loads(raw) if raw else {}


def added_files(before: str | None, after: str | None) -> set[Path]:
    if not before or not after or set(before) <= {"0"}:
        return set()
    proc = subprocess.run(
        ["git", "diff", "--name-only", "--diff-filter=A", "-z", before, after, "--", "latex"],
        capture_output=True, text=True, cwd=ROOT,
    )
    if proc.returncode != 0:
        return set()
    return {ROOT / p for p in proc.stdout.split("\0") if p.endswith(".tex")}


def front_matter(path: Path) -> dict:
    fm: dict = {}
    lines = path.read_text(encoding="utf-8").splitlines()
    if not lines or lines[0] != "---":
        return fm
    for line in lines[1:]:
        if line == "---":
            break
        key, _, val = line.partition(":")
        val = val.strip()
        if val.startswith('"'):
            try:
                val = json.loads(val)
            except ValueError:
                val = val.strip('"')
        fm[key.strip()] = val
    return fm


def urlize(s: str) -> str:
    return re.sub(r"\s+", "-", s.strip().lower())


def page_path(section: str, subject: str, topic: str, slug: str) -> str | None:
    want = "/".join(urlize(p) for p in [section, subject, *topic.split("/")] if p) + f"/{slug}"
    matches = [p.parent.relative_to(PUBLIC_DIR).as_posix() for p in (PUBLIC_DIR / section).glob(f"**/{slug}/index.html")]
    if want in matches:
        return want
    return matches[0] if len(matches) == 1 else None


def page_title(rel: str, site_title: str) -> str:
    m = TITLE_RE.search((PUBLIC_DIR / rel / "index.html").read_text(encoding="utf-8"))
    if not m:
        return ""
    title = html.unescape(m.group(1)).strip()
    suffix = f" · {site_title}"
    return title[: -len(suffix)] if site_title and title.endswith(suffix) else title


def email_html(title: str, url: str, summary: str) -> str:
    parts = [f'<p><a href="{html.escape(url)}">{html.escape(title)}</a></p>']
    if summary:
        parts.append(f"<p>{html.escape(MATH_DELIMS.sub('', summary))}</p>")
    return "\n".join(parts)


def collect(added: set[Path]) -> list[tuple[Path, str]]:
    jobs = []
    for folder in TYPES:
        base = LATEX_DIR / folder
        if not base.exists():
            continue
        for tex in sorted(base.rglob("*.tex")):
            mode = parse_sidecar(tex.with_suffix(".yaml")).get("email")
            if mode is False:
                continue
            if mode is True:
                mode = "send"
            if mode is None:
                if tex not in added:
                    continue
                mode = "send"
            if mode in ("send", "draft"):
                jobs.append((tex, mode))
    return jobs


def build(tex: Path, mode: str, base_url: str, sender: str, from_name: str) -> dict | None:
    loc = locate(tex)
    if loc is None:
        return None
    section, subject, topic, slug = loc
    content = CONTENT_DIR / section / subject / topic / f"{slug}.html"
    if not content.exists():
        return None
    fm = front_matter(content)
    if fm.get("draft") == "true":
        return None
    rel = page_path(section, subject, topic, slug)
    if rel is None:
        return None
    url = f"{base_url}/{rel}/"
    title = page_title(rel, from_name) or fm.get("title") or slug
    return {
        "mode": mode,
        "campaign": {
            "name": rel,
            "type": "regular",
            "emails": [{
                "subject": title,
                "from_name": from_name,
                "from": sender,
                "content": email_html(title, url, fm.get("summary", "")),
            }],
        },
    }


def existing_names(token: str) -> set[str]:
    names: set[str] = set()
    for status in ("sent", "draft", "ready"):
        page = 1
        while True:
            res = api("GET", "/campaigns", token, params={"filter[status]": status, "limit": 100, "page": page})
            data = res.get("data", [])
            names.update(c.get("name", "") for c in data)
            if len(data) < 100:
                break
            page += 1
    return names


def group_ids(token: str) -> list[str]:
    res = api("GET", "/groups", token, params={"limit": 100})
    return [g["id"] for g in res.get("data", [])]


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--dry-run", action="store_true")
    ap.add_argument("files", nargs="*")
    args = ap.parse_args()

    cfg = tomllib.loads((ROOT / "hugo.toml").read_text(encoding="utf-8"))
    base_url = (os.environ.get("BASE_URL") or cfg.get("baseURL", "")).rstrip("/")
    sender = cfg.get("params", {}).get("email", "")
    from_name = cfg.get("title", "")

    added = {ROOT / f for f in args.files} if args.files else added_files(os.environ.get("BEFORE"), os.environ.get("AFTER"))
    jobs = [j for j in (build(tex, mode, base_url, sender, from_name) for tex, mode in collect(added)) if j]

    if args.dry_run:
        print(json.dumps(jobs, indent=2, ensure_ascii=False))
        return 0
    if not jobs:
        print("notify: nothing to send")
        return 0

    token = os.environ.get("MAILERLITE_TOKEN")
    if not token:
        print("notify: MAILERLITE_TOKEN not set", file=sys.stderr)
        return 1
    if not sender:
        print("notify: params.email in hugo.toml is empty", file=sys.stderr)
        return 1

    names = existing_names(token)
    groups = group_ids(token)
    failed = 0
    for job in jobs:
        campaign = job["campaign"]
        if campaign["name"] in names:
            print(f"notify: exists {campaign['name']}")
            continue
        if groups:
            campaign["groups"] = groups
        try:
            res = api("POST", "/campaigns", token, body=campaign)
            cid = res["data"]["id"]
            if job["mode"] == "send":
                api("POST", f"/campaigns/{cid}/schedule", token, body={"delivery": "instant"})
            print(f"notify: {job['mode']} {campaign['name']} ({cid})")
        except Exception as e:
            failed += 1
            print(f"notify: failed {campaign['name']}: {e}", file=sys.stderr)
    return 1 if failed else 0


if __name__ == "__main__":
    sys.exit(main())
