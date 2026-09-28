from __future__ import annotations

import argparse
import html
import json
import os
import re
import subprocess
import sys
import urllib.error
import urllib.parse
import urllib.request
from pathlib import Path

try:
    import tomllib
except ModuleNotFoundError:  # Python < 3.11 (local runs); CI has 3.11+
    tomllib = None

sys.path.insert(0, str(Path(__file__).resolve().parent))
from convert import CONTENT_DIR, LATEX_DIR, ROOT, TYPES, locate, parse_sidecar

API = "https://connect.mailerlite.com/api"
PUBLIC_DIR = ROOT / "public"
MATH_DELIMS = re.compile(r"\\[()\[\]]")
TITLE_RE = re.compile(r"<title>(.*?)</title>", re.S)
CRUMB_SEP = " › "

KIND_LABEL = {"proofs": "New proof", "summaries": "New summary"}
BUTTON = {"proofs": "Download PDF", "summaries": "Read the summary"}
READ_ONLINE = "Read on the site"

# Colours mirror static/css/style.css (light theme); email clients need them inline.
INK, MUTED, ACCENT, BORDER = "#1f1d1a", "#6b655c", "#b8323f", "#e4dcd0"
SERIF = "Georgia, 'Times New Roman', serif"
SANS = "Helvetica, Arial, sans-serif"


def read_config() -> dict:
    text = (ROOT / "hugo.toml").read_text(encoding="utf-8")
    if tomllib:
        cfg = tomllib.loads(text)
        return {"baseURL": cfg.get("baseURL", ""), "title": cfg.get("title", ""),
                "email": cfg.get("params", {}).get("email", "")}
    def top(key: str) -> str:
        m = re.search(rf'^{key}\s*=\s*"([^"]*)"', text, re.M)
        return m.group(1) if m else ""
    return {"baseURL": top("baseURL"), "title": top("title"), "email": top("  email").strip() or top("email")}


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
        if val[:1] in ('"', '['):
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


def button(label: str, href: str) -> str:
    return (f'<a href="{html.escape(href)}" style="display:inline-block;background:{ACCENT};color:#ffffff;'
            f'font-family:{SANS};font-weight:bold;font-size:14px;line-height:1;padding:12px 18px;'
            f'border-radius:6px;text-decoration:none;">{html.escape(label)}</a>')


def email_html(section: str, title: str, crumb: str, url: str, pdf: str, previews: list[str],
               summary: str, lang: str) -> str:
    rtl = ' dir="rtl"' if lang in ("he", "ar") else ""
    kicker = KIND_LABEL.get(section, "New post")
    if crumb:
        kicker += " · " + crumb
    parts = [
        f'<div style="max-width:600px;margin:0 auto;padding:12px 16px 16px;background:#ffffff;font-family:{SERIF};color:{INK};line-height:1.5;">',
        f'<p style="margin:0 0 6px;font-family:{SANS};font-size:12px;letter-spacing:.08em;text-transform:uppercase;color:{MUTED};">{html.escape(kicker)}</p>',
        f'<h1{rtl} style="margin:0 0 16px;font-size:26px;line-height:1.2;font-weight:600;"><a href="{html.escape(url)}" style="color:{INK};text-decoration:none;">{html.escape(title)}</a></h1>',
    ]
    if section == "proofs" and pdf:
        parts.append(f'<p style="margin:0 0 18px;">{button(BUTTON["proofs"], pdf)}'
                     f'&nbsp;&nbsp;&nbsp;<a href="{html.escape(url)}" style="color:{ACCENT};font-family:{SANS};font-size:14px;">{READ_ONLINE}</a></p>')
        for i, src in enumerate(previews, 1):
            alt = f"{title}, page {i}" if len(previews) > 1 else title
            parts.append(f'<a href="{html.escape(pdf)}"><img src="{html.escape(src)}" width="600" alt="{html.escape(alt)}" '
                         f'style="display:block;width:100%;max-width:600px;height:auto;border:1px solid {BORDER};border-radius:6px;margin:0 0 12px;"></a>')
        if not previews and summary:
            parts.append(f'<p style="margin:0 0 1em;">{html.escape(MATH_DELIMS.sub("", summary))}</p>')
    else:
        if summary:
            parts.append(f'<p{rtl} style="margin:0 0 1em;font-size:17px;">{html.escape(MATH_DELIMS.sub("", summary))}</p>')
        parts.append(f'<p style="margin:6px 0 18px;">{button(BUTTON.get(section, READ_ONLINE), url)}</p>')
    parts.append("</div>")
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
        content = CONTENT_DIR / section / subject / topic / slug / "_index.html"
    if not content.exists():
        return None
    fm = front_matter(content)
    if fm.get("draft") == "true":
        return None
    rel = page_path(section, subject, topic, slug)
    if rel is None:
        return None
    url = f"{base_url}/{rel}/"
    full_title = page_title(rel, from_name) or fm.get("title") or slug
    crumb, _, title = full_title.rpartition(CRUMB_SEP)
    pdf = f"{base_url}{fm['pdf']}" if fm.get("pdf") else ""
    previews = [f"{base_url}{p}" for p in (fm.get("previews") or []) if isinstance(p, str)]
    return {
        "mode": mode,
        "campaign": {
            "name": rel,
            "type": "regular",
            "emails": [{
                "subject": full_title,
                "from_name": from_name,
                "from": sender,
                "content": email_html(section, title, crumb, url, pdf, previews, fm.get("summary", ""), fm.get("lang", "")),
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
    ap.add_argument("--dry-run", action="store_true", help="print the campaigns as JSON instead of sending")
    ap.add_argument("--html", metavar="DIR", help="with --dry-run: also write each email body to DIR for previewing")
    ap.add_argument("files", nargs="*", help=".tex files to treat as newly added (default: git range BEFORE..AFTER)")
    args = ap.parse_args()

    cfg = read_config()
    base_url = (os.environ.get("BASE_URL") or cfg["baseURL"]).rstrip("/")
    sender = cfg["email"]
    from_name = cfg["title"]

    added = {ROOT / f for f in args.files} if args.files else added_files(os.environ.get("BEFORE"), os.environ.get("AFTER"))
    jobs = [j for j in (build(tex, mode, base_url, sender, from_name) for tex, mode in collect(added)) if j]

    if args.dry_run:
        if args.html:
            out = Path(args.html)
            out.mkdir(parents=True, exist_ok=True)
            for job in jobs:
                name = job["campaign"]["name"].replace("/", "__") + ".html"
                (out / name).write_text(job["campaign"]["emails"][0]["content"], encoding="utf-8")
                print(f"notify: wrote {out / name}", file=sys.stderr)
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
