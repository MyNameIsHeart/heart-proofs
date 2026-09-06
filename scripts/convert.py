

from __future__ import annotations

import argparse
import datetime as dt
import json
import re
import shutil
import subprocess
import sys
from html import unescape
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
LATEX_DIR = ROOT / "latex"
CONTENT_DIR = ROOT / "content"
FILES_DIR = ROOT / "static" / "files"

TYPES = {
    "proofs": "proofs",
    "summaries": "summaries",
}


DEFAULT_THEOREM_NAMES = {
    "theoremname": "Theorem",
    "lemmaname": "Lemma",
    "corollaryname": "Corollary",
    "propositionname": "Proposition",
    "conjecturename": "Conjecture",
    "definitionname": "Definition",
    "examplename": "Example",
    "problemname": "Problem",
    "exercisename": "Exercise",
    "solutionname": "Solution",
    "remarkname": "Remark",
    "claimname": "Claim",
    "factname": "Fact",
    "notationname": "Notation",
    "casename": "Case",
    "axiomname": "Axiom",
    "criterionname": "Criterion",
    "algorithmname": "Algorithm",
    "questionname": "Question",
    "summaryname": "Summary",
    "acknowledgementname": "Acknowledgement",
    "conclusionname": "Conclusion",
    "assumptionname": "Assumption",
    "propname": "Proposition",
}

SUMMARY_CHARS = 220


def read_tex(path: Path) -> str:
    raw = path.read_bytes()
    for enc in ("utf-8", "latin-1"):
        try:
            return raw.decode(enc)
        except UnicodeDecodeError:
            continue
    return raw.decode("utf-8", errors="replace")

HEBREW_RE = re.compile(r"\\documentclass\[[^\]]*hebrew[^\]]*\]|\\usepackage\[[^\]]*hebrew[^\]]*\]\{babel\}|\\setmainlanguage\{hebrew\}")
MATH_ENVS = ("align", "align*", "equation", "equation*", "gather", "gather*", "multline", "multline*", "eqnarray", "eqnarray*", "displaymath")


def brace_arg(s: str, i: int) -> int:
    depth = 0
    j = i
    while j < len(s):
        c = s[j]
        if c == "\\":
            j += 2
            continue
        if c == "{":
            depth += 1
        elif c == "}":
            depth -= 1
            if depth == 0:
                return j
        j += 1
    return -1


def rewrap(tex: str, macro: str, lang: str) -> str:
    out = []
    i = 0
    pat = "\\" + macro + "{"
    while True:
        k = tex.find(pat, i)
        if k < 0 or (k > 0 and tex[k - 1] == "\\"):
            if k < 0:
                out.append(tex[i:])
                break
            out.append(tex[i:k + 1])
            i = k + 1
            continue
        end = brace_arg(tex, k + len(pat) - 1)
        if end < 0:
            out.append(tex[i:])
            break
        out.append(tex[i:k])
        out.append("\\foreignlanguage{%s}{%s}" % (lang, tex[k + len(pat):end]))
        i = end + 1
    return "".join(out)


def wrap_macro(tex: str, macro: str, env: str) -> str:
    out = []
    i = 0
    pat = "\\" + macro + "{"
    while True:
        k = tex.find(pat, i)
        if k < 0:
            out.append(tex[i:])
            break
        end = brace_arg(tex, k + len(pat) - 1)
        if end < 0:
            out.append(tex[i:])
            break
        out.append(tex[i:k])
        out.append("\\begin{%s}%s\\end{%s}" % (env, tex[k + len(pat):end], env))
        i = end + 1
    return "".join(out)


def unbox(tex: str) -> str:
    tex = wrap_macro(tex, "fbox", "boxed")
    tex = wrap_macro(tex, "centerline", "center")
    return tex


def bidi(tex: str) -> str:
    tex = rewrap(tex, "L", "english")
    tex = rewrap(tex, "R", "hebrew")
    tex = re.sub(r"\\beginL\s*(.*?)\\endL", lambda m: "\\foreignlanguage{english}{%s}" % m.group(1), tex, flags=re.S)
    tex = re.sub(r"\\beginR\s*(.*?)\\endR", lambda m: "\\foreignlanguage{hebrew}{%s}" % m.group(1), tex, flags=re.S)
    tex = re.sub(r"\\detokenize\{([^}]*)\}", r"\1", tex)
    return tex


def unmirror(tex: str) -> str:
    head, sep, body = tex.partition("\\begin{document}")
    if not sep:
        return tex
    out = []
    i = 0
    n = len(body)
    math = 0
    ltr: list[int] = []
    depth = 0
    while i < n:
        c = body[i]
        if c == "\\":
            m = re.match(r"\\(begin|end)\{([^}]*)\}", body[i:])
            if m:
                if m.group(2) in MATH_ENVS:
                    math += 1 if m.group(1) == "begin" else -1
                out.append(m.group(0))
                i += len(m.group(0))
                continue
            if body.startswith("\\foreignlanguage{english}{", i):
                out.append("\\foreignlanguage{english}{")
                i += len("\\foreignlanguage{english}{")
                depth += 1
                ltr.append(depth)
                continue
            if body.startswith("\\[", i) or body.startswith("\\(", i):
                math += 1
                out.append(body[i:i + 2]); i += 2
                continue
            if body.startswith("\\]", i) or body.startswith("\\)", i):
                math = max(0, math - 1)
                out.append(body[i:i + 2]); i += 2
                continue
            out.append(body[i:i + 2]); i += 2
            continue
        if c == "$":
            if body.startswith("$$", i):
                out.append("$$"); i += 2
            else:
                out.append("$"); i += 1
            math = 0 if math else 1
            continue
        if c == "{":
            depth += 1
        elif c == "}":
            if ltr and ltr[-1] == depth:
                ltr.pop()
            depth -= 1
        if not math and not ltr:
            if c == "(":
                c = ")"
            elif c == ")":
                c = "("
            elif c == "[" and body[i - 1:i] == "{" and body[i + 1:i + 2] == "}":
                c = "]"
            elif c == "]" and body[i - 1:i] == "{" and body[i + 1:i + 2] == "}":
                c = "["
        out.append(c)
        i += 1
    return head + sep + "".join(out)


def is_hebrew(tex: str) -> bool:
    return HEBREW_RE.search(tex) is not None


def preprocess(tex: str) -> str:

    tex = re.sub(r"\\global\s*\\long\s*\\def", r"\\def", tex)
    tex = re.sub(r"\\global\s*\\def", r"\\def", tex)
    tex = re.sub(r"\\long\s*\\def", r"\\def", tex)


    names = dict(DEFAULT_THEOREM_NAMES)
    for m in re.finditer(r"\\providecommand\{\\(\w+name)\}\{([^}]*)\}", tex):
        names[m.group(1)] = m.group(2)

    def repl(m: re.Match) -> str:
        key = m.group(1)
        return names.get(key, key[:-4].capitalize())

    tex = re.sub(r"\\protect\s*\\(\w+name)\b", repl, tex)
    tex = re.sub(r"\\text\{\\ensuremath\{([^{}]*)\}\}", r"\1", tex)
    tex = re.sub(r"\\protect\s*", "", tex)


    tex = re.sub(r"\\inputencoding\{[^}]*\}", "", tex)
    tex = re.sub(r"\\textbar(\{\})?", "|", tex)
    tex = unbox(tex)
    tex = bidi(tex)
    if is_hebrew(tex):
        tex = unmirror(tex)
    return tex


def run_pandoc(args: list[str], stdin: str) -> str:
    proc = subprocess.run(
        ["pandoc", *args],
        input=stdin,
        text=True,
        capture_output=True,
    )
    if proc.returncode != 0:
        raise RuntimeError(proc.stderr.strip())
    return proc.stdout


def inlines_to_text(inlines) -> str:
    out: list[str] = []
    for el in inlines:
        t = el.get("t")
        c = el.get("c")
        if t == "Str":
            out.append(c)
        elif t in ("Space", "SoftBreak"):
            out.append(" ")
        elif t == "LineBreak":
            out.append(" ")
        elif t == "Math":
            kind = c[0]["t"]
            out.append(("\\[%s\\]" if kind == "DisplayMath" else "\\(%s\\)") % c[1])
        elif t in ("Emph", "Strong", "SmallCaps", "Underline", "Strikeout", "Span"):
            out.append(inlines_to_text(c if t != "Span" else c[1]))
        elif t == "Link":
            out.append(inlines_to_text(c[1]))
        elif t == "Quoted":
            out.append("“" + inlines_to_text(c[1]) + "”")
        elif t == "Code":
            out.append(c[1])
        elif t == "RawInline":
            pass
    return "".join(out)


def meta_value_to_text(v) -> str:
    if v is None:
        return ""
    t = v.get("t")
    if t == "MetaInlines":
        return inlines_to_text(v["c"])
    if t == "MetaString":
        return v["c"]
    if t == "MetaBlocks":
        parts = []
        for b in v["c"]:
            if b.get("t") in ("Para", "Plain"):
                parts.append(inlines_to_text(b["c"]))
        return " ".join(parts)
    return ""


def extract_meta(tex: str) -> dict:
    doc = json.loads(run_pandoc(["-f", "latex", "-t", "json"], tex))
    meta = doc.get("meta", {})
    return {k: meta_value_to_text(v) for k, v in meta.items()}


THEOREM_STYLES = {
    "plain": ["thm", "theorem", "lem", "lemma", "prop", "proposition", "cor", "corollary",
              "claim", "fact", "conjecture", "criterion", "algorithm", "lemma*"],
    "definition": ["defn", "definition", "example", "exercise", "problem", "solution",
                   "notation", "axiom", "condition", "assumption", "question"],
    "remark": ["rem", "remark", "note", "case", "summary", "conclusion", "acknowledgement"],
}
STYLE_OF = {name: style for style, names in THEOREM_STYLES.items() for name in names}
DIV_RE = re.compile(r'<div( id="[^"]*")? class="([A-Za-z]+)(\*?)">')


STAR_RE = re.compile(r"\\newtheorem\*\{([^}]+)\}")


def strip_star_numbers(html: str, tex: str) -> str:
    for env in STAR_RE.findall(tex):
        pat = re.compile(r'(<div(?: id="[^"]*")? class="' + re.escape(env) + r'">\s*<p>)(<(?:strong|em)>)([^<]*?) \d+(</(?:strong|em)>)')
        html = pat.sub(r"\1\2\3\4", html)
    return html


def postprocess(html: str) -> str:

    def add_classes(m: re.Match) -> str:
        idattr, name, star = m.group(1) or "", m.group(2), m.group(3)
        style = STYLE_OF.get(name.lower())
        if style is None:
            return m.group(0)
        return f'<div{idattr} class="theorem theorem-{style} env-{name.lower()}">'

    html = DIV_RE.sub(add_classes, html)
    html = html.replace(" ◻", ' <span class="qed" aria-label="end of proof">∎</span>')
    html = html.replace("◻", '<span class="qed" aria-label="end of proof">∎</span>')
    return html


def to_html(tex: str) -> str:
    return run_pandoc(
        [
            "-f", "latex",
            "-t", "html",
            "--mathjax",
            "--wrap=none",
            "--shift-heading-level-by=1",
        ],
        tex,
    )


DATE_FORMATS = ["%B %d, %Y", "%d %B %Y", "%Y-%m-%d", "%d/%m/%Y", "%d.%m.%Y", "%B %Y"]


def parse_date(s: str) -> dt.date | None:
    s = s.strip()
    for fmt in DATE_FORMATS:
        try:
            return dt.datetime.strptime(s, fmt).date()
        except ValueError:
            pass
    return None


def parse_sidecar(path: Path) -> dict:
    data: dict = {}
    if not path.exists():
        return data
    for line in path.read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if not line or line.startswith("#") or ":" not in line:
            continue
        key, _, val = line.partition(":")
        key, val = key.strip(), val.strip()
        if val.startswith("[") and val.endswith("]"):
            data[key] = [v.strip().strip("'\"") for v in val[1:-1].split(",") if v.strip()]
        elif val.lower() in ("true", "false"):
            data[key] = val.lower() == "true"
        else:
            data[key] = val.strip("'\"")
    return data


TAG_RE = re.compile(r"<[^>]+>")
MATH_RE = re.compile(r'<span class="math (inline|display)">(.*?)</span>', re.S)


def first_paragraph_summary(html: str) -> str:
    m = re.search(r"<p>(.*?)</p>", html, re.S)
    if not m:
        return ""
    para = m.group(1)

    para = re.sub(r"^\s*<(strong|em)>[^<]*</\1>\.?\s*", "", para)

    segments: list[tuple[bool, str]] = []
    pos = 0
    for mm in MATH_RE.finditer(para):
        if mm.start() > pos:
            segments.append((False, para[pos:mm.start()]))
        segments.append((True, mm.group(2)))
        pos = mm.end()
    if pos < len(para):
        segments.append((False, para[pos:]))

    out = ""
    for is_math, text in segments:
        piece = unescape(text if is_math else TAG_RE.sub("", text))
        if is_math:
            piece = piece.replace("\\[", "\\(").replace("\\]", "\\)")
        if len(out) + len(piece) > SUMMARY_CHARS:
            if not is_math:
                cut = piece[: max(0, SUMMARY_CHARS - len(out))]
                cut = cut.rsplit(" ", 1)[0]
                out += cut
            out = out.rstrip(" ,;:") + "…"
            break
        out += piece
    return re.sub(r"\s+", " ", out).strip()


def yaml_str(s: str) -> str:
    return json.dumps(s, ensure_ascii=False)


FILENAME_RE = re.compile(r"^(?:(\d{4}-\d{2}-\d{2})-)?(.+)$")


def split_stem(stem: str) -> tuple[str | None, str]:
    m = FILENAME_RE.match(stem)
    date_str, slug = m.group(1), m.group(2)
    return date_str, re.sub(r"[^a-z0-9-]+", "-", slug.lower()).strip("-")


def locate(tex_path: Path) -> tuple[str, str, str, str] | None:
    for folder, section in TYPES.items():
        base = LATEX_DIR / folder
        try:
            parts = tex_path.relative_to(base).parts[:-1]
        except ValueError:
            continue
        if not parts:
            return None
        return section, parts[0], "/".join(parts[1:]), split_stem(tex_path.stem)[1]
    return None


COLOR_RE = re.compile(r'<span style="color: ([a-zA-Z]+)">')
KNOWN_COLORS = {"blue", "violet", "teal", "magenta", "red", "green", "orange", "gray", "grey", "brown", "cyan", "purple", "olive", "lime", "pink"}
IMG_RE = re.compile(r'<img src="([^"]+)"([^>]*)/?>')
H2_RE = re.compile(r'<h2( id="[^"]*")?>(.*?)</h2>', re.S)
HEAD_RE = re.compile(r'<h([2-6]) id="([^"]*)">(.*?)</h\1>', re.S)


def colorize(html: str) -> str:
    def repl(m: re.Match) -> str:
        c = m.group(1).lower()
        return f'<span class="tc tc-{c}">' if c in KNOWN_COLORS else m.group(0)
    return COLOR_RE.sub(repl, html)


def copy_images(html: str, tex_dir: Path, rel: str) -> str:
    def repl(m: re.Match) -> str:
        src, attrs = m.group(1), m.group(2)
        if src.startswith(("http://", "https://", "/", "data:")):
            return m.group(0)
        p = (tex_dir / src).resolve()
        try:
            inner = p.relative_to(tex_dir.resolve())
        except ValueError:
            return m.group(0)
        if not p.is_file():
            return m.group(0)
        dst = FILES_DIR / rel / inner
        dst.parent.mkdir(parents=True, exist_ok=True)
        shutil.copyfile(p, dst)
        attrs = re.sub(r'\s*alt="image"', ' alt=""', attrs)
        return f'<img src="/files/{rel}/{inner.as_posix()}"{attrs}>'
    return IMG_RE.sub(repl, html)


def strip_tags(s: str) -> str:
    return re.sub(r"\s+", " ", unescape(TAG_RE.sub("", s))).strip()


def shift_headings(html: str, by: int) -> str:
    def repl(m: re.Match) -> str:
        lvl = max(2, min(6, int(m.group(1)) - by))
        return f'<h{lvl} id="{m.group(2)}">{m.group(3)}</h{lvl}>'
    return HEAD_RE.sub(repl, html)


def normalize_levels(html: str) -> str:
    levels = sorted({int(l) for l in re.findall(r"<h([2-6]) id=", html)})
    if not levels:
        return html
    mapping = {l: i + 2 for i, l in enumerate(levels)}
    def repl(m: re.Match) -> str:
        lvl = mapping[int(m.group(1))]
        return f'<h{lvl} id="{m.group(2)}">{m.group(3)}</h{lvl}>'
    return HEAD_RE.sub(repl, html)


def toc_of(html: str, max_level: int = 3) -> list[dict]:
    out = []
    for m in HEAD_RE.finditer(html):
        lvl = int(m.group(1))
        if lvl <= max_level:
            out.append({"level": lvl, "id": m.group(2), "text": strip_tags(m.group(3))})
    return out


def split_parts(html: str) -> tuple[str, list[tuple[str, str]]]:
    heads = list(H2_RE.finditer(html))
    if not heads:
        return html, []
    intro = html[: heads[0].start()]
    parts = []
    for i, m in enumerate(heads):
        end = heads[i + 1].start() if i + 1 < len(heads) else len(html)
        parts.append((strip_tags(m.group(2)), html[m.end():end]))
    return intro, parts


def split_trailing_box(chunk: str) -> tuple[str, str]:
    k = chunk.rfind('<div class="boxed">')
    if k < 0:
        return chunk, ""
    depth = 0
    for m in re.finditer(r"<div\b|</div>", chunk[k:]):
        depth += 1 if m.group(0) == "<div" else -1
        if depth == 0:
            end = k + m.end()
            if chunk[end:].strip():
                return chunk, ""
            return chunk[:k], chunk[k:end] + "\n"
    return chunk, ""


def with_notice(box: str, notice: str) -> str:
    if not box or not notice:
        return box
    k = box.rstrip().rfind("</div>")
    if k < 0:
        return box
    from html import escape
    return box[:k] + '<p class="notice">' + escape(notice) + "</p>" + box[k:]


def move_boxes(intro: str, parts: list[tuple[str, str]], notice: str = "") -> tuple[str, list[tuple[str, str]]]:
    if not parts:
        return intro, parts
    intro, box = split_trailing_box(intro)
    out = []
    for i, (title, chunk) in enumerate(parts):
        chunk = with_notice(box, notice) + chunk
        if i + 1 < len(parts):
            chunk, box = split_trailing_box(chunk)
        out.append((title, chunk))
    return intro, out


def should_split(side: dict, html: str) -> bool:
    if "split" in side:
        return side["split"] is True
    return len(html) > 80000 and len(H2_RE.findall(html)) >= 2



def convert_one(tex_path: Path, section: str, subject: str, topic: str, verbose: bool) -> Path:
    date_str, slug = split_stem(tex_path.stem)

    tex = preprocess(read_tex(tex_path))
    meta = extract_meta(tex)
    body = postprocess(strip_star_numbers(to_html(tex), tex))
    side = parse_sidecar(tex_path.with_suffix(".yaml"))

    title = side.get("title") or meta.get("title") or slug.replace("-", " ").title()

    date = None
    if "date" in side:
        date = parse_date(str(side["date"]))
    if date is None and date_str:
        date = parse_date(date_str)
    if date is None and meta.get("date"):
        date = parse_date(meta["date"])
    if date is None:
        date = dt.date.fromtimestamp(tex_path.stat().st_mtime)
        print(f"  ! no date for {tex_path.name}; using file mtime {date}", file=sys.stderr)

    summary = side.get("summary") or first_paragraph_summary(body)
    tags = side.get("tags", [])
    if isinstance(tags, str):
        tags = [tags]


    rel = f"{section}/{subject}" + (f"/{topic}" if topic else "")
    out_files = FILES_DIR / rel
    out_files.mkdir(parents=True, exist_ok=True)
    links = {}
    for ext in ("pdf", "tex"):
        src = tex_path.with_suffix("." + ext)
        if src.exists():
            dst = out_files / f"{slug}.{ext}"
            shutil.copyfile(src, dst)
            links[ext] = f"/files/{rel}/{slug}.{ext}"

    lang = side.get("lang") or ("he" if is_hebrew(tex) else "")
    fm = [
        "---",
        f"title: {yaml_str(title)}",
        f"date: {date.isoformat()}",
        f"subject: {yaml_str(subject)}",
        f"topic: {yaml_str(topic)}",
        f"summary: {yaml_str(summary)}",
        f"tags: [{', '.join(yaml_str(t) for t in tags)}]",
    ]
    for ext, url in links.items():
        fm.append(f"{ext}: {yaml_str(url)}")
    if lang:
        fm.append(f"lang: {yaml_str(lang)}")
    if side.get("draft") is True:
        fm.append("draft: true")
    if side.get("weight"):
        fm.append(f"weight: {side['weight']}")
    fm.append("---")

    out_dir = CONTENT_DIR / rel
    out_dir.mkdir(parents=True, exist_ok=True)
    body = colorize(copy_images(body, tex_path.parent, f"{rel}"))
    if should_split(side, body):
        intro, parts = move_boxes(*split_parts(body), notice=str(side.get("notice") or ""))
        part_dir = out_dir / slug
        part_dir.mkdir(parents=True, exist_ok=True)
        head = fm[:-1] + ["layout: summary", "---"]
        out_path = part_dir / "_index.html"
        out_path.write_text("\n".join(head) + "\n" + intro, encoding="utf-8")
        for i, (ptitle, chunk) in enumerate(parts, 1):
            chunk = normalize_levels(chunk)
            pfm = [
                "---",
                f"title: {yaml_str(ptitle)}",
                f"date: {date.isoformat()}",
                f"subject: {yaml_str(subject)}",
                f"topic: {yaml_str(topic)}",
                f"weight: {i}",
                "part: true",
                f"toc: {json.dumps(toc_of(chunk), ensure_ascii=False)}",
            ]
            if lang:
                pfm.append(f"lang: {yaml_str(lang)}")
            if side.get("draft") is True:
                pfm.append("draft: true")
            pfm.append("---")
            (part_dir / f"{i:02d}.html").write_text("\n".join(pfm) + "\n" + chunk, encoding="utf-8")
        if verbose:
            print(f"  {tex_path.relative_to(ROOT)}  ->  {out_path.relative_to(ROOT)} (+{len(parts)})")
        return out_path
    out_path = out_dir / f"{slug}.html"
    out_path.write_text("\n".join(fm) + "\n" + body, encoding="utf-8")
    if verbose:
        print(f"  {tex_path.relative_to(ROOT)}  ->  {out_path.relative_to(ROOT)}")
    return out_path


def write_section_indexes(base: Path, section: str) -> None:
    dirs = {tex.parent for tex in base.rglob("*.tex")}
    all_dirs = set()
    for d in dirs:
        while d != base:
            all_dirs.add(d)
            d = d.parent
    for d in sorted(all_dirs):
        rel = d.relative_to(base)
        out = CONTENT_DIR / section / rel / "_index.md"
        out.parent.mkdir(parents=True, exist_ok=True)
        folder = d.name
        title = folder.replace("-", " ").replace("_", " ").title()
        out.write_text(
            "---\n"
            f"title: {yaml_str(title)}\n"
            "layout: bysubject\n"
            f"folder: {yaml_str(folder)}\n"
            f"subject: {yaml_str(rel.parts[0])}\n"
            "---\n"
        )


def clean() -> None:
    for section in TYPES.values():
        base = CONTENT_DIR / section
        if base.exists():
            for p in base.rglob("*.html"):
                p.unlink()
            for p in list(base.rglob("_index.md")) + list(base.rglob("_index.html")):
                if p.parent != base:
                    p.unlink()
            for d in sorted((d for d in base.rglob("*") if d.is_dir()), reverse=True):
                if not any(d.iterdir()):
                    d.rmdir()
    if FILES_DIR.exists():
        shutil.rmtree(FILES_DIR)


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--clean", action="store_true")
    ap.add_argument("-q", "--quiet", action="store_true")
    args = ap.parse_args()

    if shutil.which("pandoc") is None:
        print("pandoc not found on PATH. Install it: https://pandoc.org/installing.html", file=sys.stderr)
        return 1


    clean()

    n_ok = n_err = 0
    for folder, section in TYPES.items():
        base = LATEX_DIR / folder
        if not base.exists():
            continue
        write_section_indexes(base, section)
        for tex_path in sorted(base.rglob("*.tex")):
            parts = tex_path.relative_to(base).parts[:-1]
            if len(parts) == 0:
                print(f"  ! skipping {tex_path.name}: put it inside a subject folder, e.g. latex/{folder}/calculus-1/", file=sys.stderr)
                continue
            subject = parts[0]
            topic = "/".join(parts[1:])
            try:
                convert_one(tex_path, section, subject, topic, verbose=not args.quiet)
                n_ok += 1
            except Exception as e:
                n_err += 1
                print(f"  ✗ {tex_path.relative_to(ROOT)}: {e}", file=sys.stderr)
    print(f"converted {n_ok} file(s), {n_err} error(s)")
    return 1 if n_err else 0


if __name__ == "__main__":
    sys.exit(main())
