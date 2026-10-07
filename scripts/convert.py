

from __future__ import annotations

import argparse
import datetime as dt
import json
import re
import shutil
import subprocess
import sys
import tempfile
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


WRAP_MARK = "::wrap-"
WRAP_SIDE = {"r": "right", "R": "right", "o": "right", "O": "right",
             "l": "left", "L": "left", "i": "left", "I": "left"}
WRAPFIG_RE = re.compile(
    r"\\begin\{(wrapfigure|wraptable|wrapfloat)\}"
    r"(?:\[[^\]]*\])?"
    r"(?:\{(?:figure|table)\})?"
    r"\{\s*([rRlLioIO])\s*\}"
    r"(?:\[[^\]]*\])?"
    r"\{[^{}]*\}"
    r"(.*?)\\end\{\1\}",
    re.S,
)
INCLUDEGRAPHICS_RE = re.compile(r"(\\includegraphics(?:\[[^\]]*\])?\{)([^{}]*?)(\})")


def dewrapfig(tex: str) -> str:
    """pandoc ignores wrapfigure. Drop the wrapper, tag the image with its side."""
    def repl(m: re.Match) -> str:
        side = WRAP_SIDE.get(m.group(2), "right")
        inner = m.group(3)
        inner = re.sub(r"\\(centering|small|footnotesize)\b", "", inner)
        inner = INCLUDEGRAPHICS_RE.sub(
            lambda g: g.group(1) + g.group(2) + WRAP_MARK + side + g.group(3), inner, count=1)
        return inner
    return WRAPFIG_RE.sub(repl, tex)


def unbox(tex: str) -> str:
    tex = wrap_macro(tex, "fbox", "boxed")
    tex = wrap_macro(tex, "centerline", "center")
    return tex


MARGIN_MARK = "marginnote"


def margin_notes(tex: str) -> str:
    """pandoc drops \\marginpar. Keep the note as a marked span; postprocess makes it a side note."""
    out = []
    i = 0
    for m in re.finditer(r"\\marginpar\s*(?:\[[^\]]*\])?\s*(?=\{)", tex):
        if m.start() < i:
            continue
        end = brace_arg(tex, m.end())
        if end < 0:
            break
        out.append(tex[i:m.start()])
        out.append("\\textcolor{%s}{%s}" % (MARGIN_MARK, tex[m.end() + 1:end]))
        i = end + 1
    out.append(tex[i:])
    return "".join(out)


def keep_layout(tex: str) -> str:
    """Layout pandoc gets wrong: abstracts (moved to metadata), a \\\\ that ends a paragraph (an empty line
    in LaTeX; pandoc swallows the paragraph break while looking for an optional [length]) and paragraphs
    pushed to the end with \\hfill."""
    head, sep, body = tex.partition("\\begin{document}")
    if not sep:
        return tex
    body = re.sub(r"\\(begin|end)\{abstract\}", r"\\\1{abstractbox}", body)
    body = re.sub(r"\\\\\*?[ \t]*\n(?:[ \t]*\n)+", "\n\n\\\\begin{blankline}\\\\end{blankline}\n\n", body)
    body = re.sub(r"(\n[ \t]*\n)[ \t]*\\(?:hfill|hspace\*?\{\\fill\})(?:\{\})?[ \t]*(\S.*?)(?=\n[ \t]*\n|\\end\{document\})",
                  r"\1\\begin{flushright}\n\2\n\\end{flushright}", body, flags=re.S)
    return head + sep + body


def abstract_name(tex: str) -> str:
    m = re.search(r"\\(?:renewcommand|providecommand)\*?\{?\\abstractname\}?\{([^{}]*)\}", tex)
    if m:
        return m.group(1).strip()
    return "תקציר" if is_hebrew(tex) else "Abstract"


def label_abstract(html: str, name: str) -> str:
    from html import escape
    head = f'<div class="abstract">\n<p class="abstract-title">{escape(name)}</p>'
    return html.replace('<div class="abstractbox">', head)


TABULAR_RE = re.compile(r"\\begin\{tabular\*?\}(?:\{[^{}]*\})?\s*(?:\[[^\]]*\])?\s*\{((?:[^{}]|\{(?:[^{}]|\{[^{}]*\})*\})*)\}(.*?)\\end\{tabular\*?\}", re.S)


def column_rules(spec: str) -> set[int]:
    """Column boundaries with a vertical rule in a tabular spec: 0 is before the first column, n after the n-th."""
    rules: set[int] = set()
    cols = 0
    i = 0
    while i < len(spec):
        c = spec[i]
        if c == "|":
            rules.add(cols)
        elif c in "lcrX":
            cols += 1
        elif c in "pmb":
            cols += 1
            if spec.startswith("{", i + 1):
                i = brace_arg(spec, i + 1)
        elif c in "><@!" and spec.startswith("{", i + 1):
            i = brace_arg(spec, i + 1)
        if i < 0:
            break
        i += 1
    if cols in rules:
        rules.discard(cols)
        rules.add(-1)
    return rules


def row_lines(rows: str) -> list[str]:
    """Classes for the \\hline rules: above the table, between body rows (the one under the first row is
    the header line, always drawn) and below the table."""
    segs = [s.strip() for s in re.split(r"\\\\|\\tabularnewline", rows)]
    cls = []
    if segs[0].startswith("\\hline"):
        cls.append("hl-top")
    if any(s.startswith("\\hline") and s[len("\\hline"):].strip() for s in segs[2:]):
        cls.append("hl-rows")
    if len(segs) > 1 and segs[-1].startswith("\\hline"):
        cls.append("hl-bottom")
    return cls


def rule_tables(html: str, tex: str) -> str:
    """pandoc keeps no rules from a tabular. Tag each table with its vertical rules and, when it has
    \\hline between body rows, with row lines; the stylesheet draws them."""
    it = iter(TABULAR_RE.findall(tex))

    def repl(m: re.Match) -> str:
        spec = next(it, None)
        if spec is None:
            return m.group(0)
        cls = sorted("vr-end" if r < 0 else f"vr-{r}" for r in column_rules(spec[0])) + row_lines(spec[1])
        return f'<table class="{" ".join(cls)}"{m.group(1)}>' if cls else m.group(0)

    return re.sub(r"<table\b([^>]*)>", repl, html)


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
    tex = dewrapfig(tex)
    tex = unbox(tex)
    tex = margin_notes(tex)
    tex = keep_layout(tex)
    tex = bidi(tex)
    if is_hebrew(tex):
        tex = unmirror(tex)
    return tex


def run_pandoc(args: list[str], stdin: str, log: list[str] | None = None) -> str:
    proc = subprocess.run(
        ["pandoc", *args],
        input=stdin,
        text=True,
        capture_output=True,
    )
    if proc.returncode != 0:
        raise RuntimeError(proc.stderr.strip())
    if log is not None:
        log.append(proc.stderr)
    return proc.stdout


SKIPPED_RE = re.compile(r"\[INFO\] Skipped '(.*?)' at line (\d+) column \d+", re.S)
LAYOUT_RE = re.compile(
    r"\\(?:begin|end)\{|\\(?:medskip|bigskip|smallskip|noindent|indent|newpage|clearpage|pagebreak|nopagebreak"
    r"|columnwidth|linewidth|textwidth|textheight|maketitle|tableofcontents|listoffigures|listoftables"
    r"|tiny|scriptsize|footnotesize|small|normalsize|large|Large|LARGE|huge|Huge|vspace|hspace|quad|qquad"
    r"|hfill|vfill|newgeometry|restoregeometry|centering|raggedright|raggedleft|par|null|relax|thispagestyle"
    r"|pagestyle|enskip|addvspace)(?![A-Za-z])"
)


def warn_dropped(log: list[str], tex: str, name: str) -> None:
    """Report text pandoc dropped from the document body. Spacing and layout commands are expected."""
    start = tex[:tex.find("\\begin{document}")].count("\n") + 1
    for m in SKIPPED_RE.finditer("\n".join(log)):
        if int(m.group(2)) > start and not LAYOUT_RE.match(m.group(1)):
            snippet = re.sub(r"\s+", " ", m.group(1))[:80]
            print(f"  ! {name}: line {m.group(2)}, {snippet} is not on the page (pandoc dropped it)", file=sys.stderr)


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
    if t == "MetaList":
        return ", ".join(s for s in (meta_value_to_text(x) for x in v["c"]) if s)
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
    html = html.replace(f'<span style="color: {MARGIN_MARK}">', '<span class="marginnote">')
    html = re.sub(r'<div class="blankline">\s*</div>', '<div class="blank-line" aria-hidden="true"></div>', html)
    html = html.replace(" ◻", ' <span class="qed" aria-label="end of proof">∎</span>')
    html = html.replace("◻", '<span class="qed" aria-label="end of proof">∎</span>')
    return html


def to_html(tex: str, log: list[str] | None = None) -> str:
    return run_pandoc(
        [
            "-f", "latex",
            "-t", "html",
            "--mathjax",
            "--wrap=none",
            "--shift-heading-level-by=1",
            "--verbose",
        ],
        tex,
        log,
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


def url_part(s: str) -> str:
    """A path segment as Hugo writes it into the URL: lowercase, spaces to dashes, other punctuation dropped."""
    return re.sub(r"[^\w.~+#-]", "", re.sub(r"\s+", "-", s.strip().lower()))


def page_url(section: str, subject: str, topic: str, slug: str) -> str:
    parts = [section, subject, *topic.split("/"), slug]
    return "/" + "/".join(url_part(p) for p in parts if p) + "/"


def previous_locations(tex_path: Path) -> list[tuple[str, str, str, str]]:
    """Where a .tex lived before it was moved or renamed, from git history. Emails sent then link there."""
    rel = tex_path.relative_to(ROOT).as_posix()
    proc = subprocess.run(["git", "-c", "core.quotePath=false", "log", "--follow", "--name-status", "--format=", "--", rel],
                          cwd=ROOT, capture_output=True, text=True)
    if proc.returncode != 0:
        return []
    out: list[tuple[str, str, str, str]] = []
    for line in proc.stdout.splitlines():
        fields = line.split("\t")
        status = fields[0]
        if status.startswith(("A", "C")):
            break  # the file starts here; --follow would go on into the file it was copied from
        if status.startswith("R"):
            if int(status[1:] or 0) < 90:
                break
            loc = locate(ROOT / fields[1])
            if loc and loc not in out:
                out.append(loc)
    return out


CURRENT_URLS: set[str] = set()


def keep_old_addresses(tex_path: Path, here: tuple[str, str, str, str], files: list[str]) -> list[str]:
    """Redirect the page's old URLs to it and keep its files (PDF, page images, .tex) at their old paths too,
    so links in emails sent before a move still work. Returns the old page URLs for Hugo's aliases."""
    aliases = []
    for section, subject, topic, slug in previous_locations(tex_path):
        url = page_url(section, subject, topic, slug)
        if url == page_url(*here) or url in CURRENT_URLS:
            continue
        aliases.append(url)
        old_dir = FILES_DIR / section / subject / topic
        for name in files:
            src = FILES_DIR / here[0] / here[1] / here[2] / name
            if src.is_file():
                dst = old_dir / (slug + name[len(here[3]):])
                dst.parent.mkdir(parents=True, exist_ok=True)
                shutil.copyfile(src, dst)
    return aliases


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


IMG_EXTS = (".png", ".jpg", ".jpeg", ".svg", ".webp", ".gif")
WRAP_SUFFIX_RE = re.compile(re.escape(WRAP_MARK) + r"(right|left)$")


def resolve_image(p: Path) -> Path:
    """LaTeX lets \\includegraphics omit the extension; pandoc does not. Fill it in."""
    if p.is_file() or p.suffix:
        return p
    for ext in IMG_EXTS:
        cand = p.with_name(p.name + ext)
        if cand.is_file():
            return cand
    return p


def copy_images(html: str, tex_dir: Path, rel: str) -> str:
    def repl(m: re.Match) -> str:
        src, attrs = m.group(1), m.group(2)
        wrap = ""
        w = WRAP_SUFFIX_RE.search(src)
        if w:
            wrap, src = w.group(1), src[:w.start()]
        if src.startswith(("http://", "https://", "/", "data:")):
            return m.group(0)
        p = resolve_image((tex_dir / src).resolve())
        try:
            inner = p.relative_to(tex_dir.resolve())
        except ValueError:
            return m.group(0)
        if not p.is_file():
            print(f"  ! missing image: {src} (in {tex_dir.name})", file=sys.stderr)
            return m.group(0)
        dst = FILES_DIR / rel / inner
        dst.parent.mkdir(parents=True, exist_ok=True)
        shutil.copyfile(p, dst)
        scrub_image(dst)
        attrs = re.sub(r'\s*alt="image"', ' alt=""', attrs)
        cls = f' class="fig-wrap fig-{wrap}"' if wrap else ""
        return f'<img src="/files/{rel}/{inner.as_posix()}"{cls}{attrs}>'
    return IMG_RE.sub(repl, html)


def copy_asset(src: str, tex_dir: Path, rel: str) -> str | None:
    """Copy a file referenced relative to the .tex into static/files and return its site URL."""
    if src.startswith(("http://", "https://", "/")):
        return src
    p = resolve_image((tex_dir / src).resolve())
    try:
        inner = p.relative_to(tex_dir.resolve())
    except ValueError:
        return None
    if not p.is_file():
        print(f"  ! missing file: {src} (in {tex_dir.name})", file=sys.stderr)
        return None
    dst = FILES_DIR / rel / inner
    dst.parent.mkdir(parents=True, exist_ok=True)
    shutil.copyfile(p, dst)
    scrub_image(dst)
    return f"/files/{rel}/{inner.as_posix()}"


PROOF_DIV = '<div class="proof">'


def add_figure(html: str, url: str, alt: str, caption: str, side: str) -> str:
    """Float a sidecar figure beside the proof (after the statement), or at the top if there is no proof."""
    from html import escape
    fig = f'<figure class="fig-wrap fig-{side}"><img src="{escape(url)}" alt="{escape(alt)}" loading="lazy">'
    if caption:
        fig += f"<figcaption>{escape(caption)}</figcaption>"
    fig += "</figure>\n"
    k = html.find(PROOF_DIV)
    return fig + html if k < 0 else html[:k] + fig + html[k:]


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



# ---- metadata scrubbing: nothing the site publishes may carry document, tool or camera metadata ----

PDF_INFO_KEYS = ("Title", "Subject", "Keywords", "Author", "Creator", "Producer", "CreationDate", "ModDate")


def pdf_metadata(path: Path) -> list[str]:
    """Names of metadata still present in a PDF (empty list = clean). Unverifiable counts as dirty."""
    if shutil.which("pdfinfo") is None:
        return ["pdfinfo not available to verify"]
    proc = subprocess.run(["pdfinfo", str(path)], capture_output=True, text=True, errors="replace")
    found = []
    for line in proc.stdout.splitlines():
        key, _, val = line.partition(":")
        if key.strip() in PDF_INFO_KEYS and val.strip():
            found.append(key.strip())
    raw = path.read_bytes()
    if b"/Metadata" in raw or b"xmpmeta" in raw:
        found.append("XMP")
    if PTEX_ENTRY_RE.search(raw):
        found.append("pdfTeX source entries")
    return found


PTEX_ENTRY_RE = re.compile(rb"/PTEX\.(?:FileName|Fullbanner)\s*\([^)]*\)|/PTEX\.PageNumber\s*\d+|/PTEX\.InfoDict\s*\d+\s+\d+\s+R")


def blank_pdf_metadata(path: Path) -> None:
    """Strip metadata in place without external tools: blank the Info dictionary, detach and blank XMP
    streams, and blank pdfTeX's source-file entries. Byte lengths are preserved so cross-references stay valid."""
    data = bytearray(path.read_bytes())

    def blank(a: int, b: int) -> None:
        data[a:b] = b" " * (b - a)

    def obj_start(num: bytes, gen: bytes) -> int:
        m = re.search(rb"(?<![0-9])" + num + rb"\s+" + gen + rb"\s+obj\b", data)
        return m.end() if m else -1

    for m in list(re.finditer(rb"/Info\s+(\d+)\s+(\d+)\s+R", data)):
        k = obj_start(m.group(1), m.group(2))
        if k < 0:
            continue
        a = data.find(b"<<", k)
        b = data.find(b">>", a)
        if 0 <= a < b:
            blank(a + 2, b)
    for m in list(re.finditer(rb"/Metadata\s+(\d+)\s+(\d+)\s+R", data)):
        k = obj_start(m.group(1), m.group(2))
        blank(m.start(), m.end())
        if k < 0:
            continue
        a = data.find(b"stream", k)
        b = data.find(b"endstream", a)
        if 0 <= a < b:
            blank(a + len(b"stream"), b)
    for m in list(PTEX_ENTRY_RE.finditer(data)):
        blank(m.start(), m.end())
    path.write_bytes(bytes(data))


def publish_pdf(src: Path, dst: Path) -> bool:
    """Copy a PDF into the site with its metadata stripped. qpdf rebuilds the file when available; the
    built-in stripper runs in every case. Only a file that is still dirty afterwards is left unpublished."""
    dst.parent.mkdir(parents=True, exist_ok=True)
    done = False
    if shutil.which("qpdf"):
        proc = subprocess.run(["qpdf", "--empty", "--deterministic-id", "--pages", str(src), "--", str(dst)],
                              capture_output=True, text=True, errors="replace")
        done = proc.returncode in (0, 3) and dst.is_file()  # 3 = warnings only
    if not done:
        shutil.copyfile(src, dst)
    blank_pdf_metadata(dst)
    left = pdf_metadata(dst)
    if left:
        dst.unlink(missing_ok=True)
        print(f"  ! not publishing {src.name}: metadata survived stripping ({', '.join(left)})", file=sys.stderr)
        return False
    return True


COMMENT_LINE_RE = re.compile(r"^[ \t]*%.*\n?", re.M)
COMMENT_ENV_RE = re.compile(r"\\begin\{comment\}.*?\\end\{comment\}[ \t]*\n?", re.S)


def scrub_tex(tex: str) -> str:
    """The published .tex: no editor banner, no comment lines, no comment environments."""
    tex = COMMENT_ENV_RE.sub("", tex)
    tex = COMMENT_LINE_RE.sub("", tex)
    return re.sub(r"\n{3,}", "\n\n", tex).strip() + "\n"


JPEG_DROP = {0xE1, 0xED, 0xFE}  # APP1 (EXIF/XMP), APP13 (Photoshop/IPTC), COM
PNG_DROP = {b"tEXt", b"zTXt", b"iTXt", b"eXIf", b"tIME"}


def scrub_image(path: Path) -> bool:
    """Strip EXIF/XMP/IPTC/comment segments from a JPEG or text/EXIF/time chunks from a PNG, in place."""
    data = path.read_bytes()
    if data[:2] == b"\xff\xd8":
        out = bytearray(b"\xff\xd8")
        i = 2
        while i + 4 <= len(data) and data[i] == 0xFF:
            marker = data[i + 1]
            if marker == 0xDA:
                out += data[i:]
                break
            seglen = int.from_bytes(data[i + 2:i + 4], "big")
            if marker not in JPEG_DROP:
                out += data[i:i + 2 + seglen]
            i += 2 + seglen
        else:
            return False
    elif data[:8] == b"\x89PNG\r\n\x1a\n":
        out = bytearray(data[:8])
        i = 8
        while i + 8 <= len(data):
            length = int.from_bytes(data[i:i + 4], "big")
            ctype = data[i + 4:i + 8]
            chunk = data[i:i + 12 + length]
            if ctype not in PNG_DROP:
                out += chunk
            i += 12 + length
    else:
        return False
    if bytes(out) != data:
        path.write_bytes(bytes(out))
        return True
    return False


def scrub_images_under(root: Path) -> int:
    n = 0
    if root.is_dir():
        for p in root.rglob("*"):
            if p.suffix.lower() in (".jpg", ".jpeg", ".png") and scrub_image(p):
                n += 1
    return n


LATEX_ERR_RE = re.compile(r"^(?:!.*|.*:\d+: .*)$", re.M)


def build_pdf(tex_path: Path, out_pdf: Path) -> bool:
    """Compile a .tex with pdflatex (cwd = its folder, aux files in a temp dir) into out_pdf."""
    if shutil.which("pdflatex") is None:
        print("  ! pdflatex not found; skipping PDF for " + tex_path.name, file=sys.stderr)
        return False
    with tempfile.TemporaryDirectory() as tmp:
        # Load pdfprivacy before the document's own preamble so every PDF comes out
        # without producer, dates, TeX banner, trailer ID or XMP, whatever the source says.
        cmd = ["pdflatex", "-interaction=nonstopmode", "-halt-on-error", "-file-line-error",
               f"-output-directory={tmp}", f"-jobname={tex_path.stem}",
               "\\RequirePackage[all]{pdfprivacy}\\input{" + tex_path.name + "}"]
        for _ in range(2):
            proc = subprocess.run(cmd, cwd=tex_path.parent, capture_output=True, text=True, errors="replace")
            if proc.returncode != 0:
                errs = LATEX_ERR_RE.findall(proc.stdout)
                print(f"  ! pdflatex failed for {tex_path.name}: {errs[0] if errs else 'see log'}", file=sys.stderr)
                return False
            log = Path(tmp, tex_path.stem + ".log")
            if not log.exists() or "Rerun" not in log.read_text(errors="replace"):
                break
        pdf = Path(tmp, tex_path.stem + ".pdf")
        if not pdf.is_file():
            return False
        return publish_pdf(pdf, out_pdf)


PGM_HEAD_RE = re.compile(rb"P5\s+(\d+)\s+(\d+)\s+(\d+)\s")
INK = 250  # gray level below which a pixel counts as ink


def pgm_pages(pdf: Path, dpi: int) -> list[tuple[int, int, bytes]]:
    out = subprocess.run(["pdftoppm", "-r", str(dpi), "-gray", str(pdf)], capture_output=True, check=True).stdout
    pages, i = [], 0
    while i < len(out):
        m = PGM_HEAD_RE.match(out, i)
        if not m:
            break
        w, h = int(m.group(1)), int(m.group(2))
        start = m.end()
        pages.append((w, h, out[start:start + w * h]))
        i = start + w * h
    return pages


def ink_box(w: int, h: int, px: bytes, dpi: int) -> tuple[int, int, int, int] | None:
    """Bounding box of the page's ink, ignoring a lone page number at the bottom."""
    first = re.compile(rb"[\x00-\xf9]")
    last = re.compile(rb"[\x00-\xf9][\xfa-\xff]*$")
    rows = [k for k in range(h) if min(px[k * w:(k + 1) * w]) < INK]
    if not rows:
        return None
    blocks, start, prev = [], rows[0], rows[0]
    for r in rows[1:]:
        if r - prev > dpi // 15:
            blocks.append((start, prev))
            start = r
        prev = r
    blocks.append((start, prev))

    def hspan(r0: int, r1: int) -> tuple[int, int]:
        lo, hi = w, 0
        for k in range(r0, r1 + 1):
            row = px[k * w:(k + 1) * w]
            m = first.search(row)
            if m:
                lo, hi = min(lo, m.start()), max(hi, last.search(row).start() + 1)
        return lo, hi
    if len(blocks) > 1:
        b0, b1 = blocks[-1]
        lo, hi = hspan(b0, b1)
        if b1 - b0 < dpi * 0.3 and hi - lo < dpi * 0.6 and b0 > h * 0.8:
            blocks.pop()  # centred page number
    top, bot = blocks[0][0], blocks[-1][1]
    lo, hi = hspan(top, bot)
    return lo, top, hi, bot + 1


def render_previews(pdf: Path, out_dir: Path, slug: str, dpi: int = 150) -> list[Path]:
    """One cropped PNG per page, for the email."""
    if shutil.which("pdftoppm") is None:
        print("  ! pdftoppm not found; skipping previews for " + pdf.name, file=sys.stderr)
        return []
    out = []
    pad = dpi // 5
    for n, (w, h, px) in enumerate(pgm_pages(pdf, dpi), 1):
        box = ink_box(w, h, px, dpi)
        if box is None:
            continue
        x, y = max(0, box[0] - pad), max(0, box[1] - pad)
        W, H = min(w, box[2] + pad) - x, min(h, box[3] + pad) - y
        dst = out_dir / f"{slug}-{n}"
        subprocess.run(["pdftoppm", "-r", str(dpi), "-png", "-f", str(n), "-l", str(n), "-singlefile",
                        "-x", str(x), "-y", str(y), "-W", str(W), "-H", str(H), str(pdf), str(dst)],
                       check=True, capture_output=True)
        scrub_image(dst.with_suffix(".png"))
        out.append(dst.with_suffix(".png"))
    return out


def convert_one(tex_path: Path, section: str, subject: str, topic: str, verbose: bool, pdf: bool = False) -> Path:
    date_str, slug = split_stem(tex_path.stem)

    tex = preprocess(read_tex(tex_path))
    meta = extract_meta(tex)
    log: list[str] = []
    body = postprocess(strip_star_numbers(to_html(tex, log), tex))
    warn_dropped(log, tex, tex_path.name)
    body = rule_tables(label_abstract(body, abstract_name(tex)), tex)
    side = parse_sidecar(tex_path.with_suffix(".yaml"))

    title = side.get("title") or meta.get("title") or slug.replace("-", " ").title()
    author = side.get("author") or meta.get("author", "")

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
    (out_files / f"{slug}.tex").write_text(scrub_tex(read_tex(tex_path)), encoding="utf-8")
    links["tex"] = f"/files/{rel}/{slug}.tex"
    ready_pdf = tex_path.with_suffix(".pdf")
    if ready_pdf.exists() and publish_pdf(ready_pdf, out_files / f"{slug}.pdf"):
        links["pdf"] = f"/files/{rel}/{slug}.pdf"
    previews: list[str] = []
    if pdf and section == "proofs" and side.get("pdf") is not False:
        dst = out_files / f"{slug}.pdf"
        if "pdf" in links or build_pdf(tex_path, dst):
            links["pdf"] = f"/files/{rel}/{slug}.pdf"
            previews = [f"/files/{rel}/{p.name}" for p in render_previews(dst, out_files, slug)]

    files = [url.rsplit("/", 1)[1] for url in [*links.values(), *previews]]
    aliases = keep_old_addresses(tex_path, (section, subject, topic, slug), files)

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
    if author:
        fm.append(f"author: {yaml_str(author)}")
    for ext, url in links.items():
        fm.append(f"{ext}: {yaml_str(url)}")
    if previews:
        fm.append(f"previews: {json.dumps(previews)}")
    if lang:
        fm.append(f"lang: {yaml_str(lang)}")
    if side.get("draft") is True:
        fm.append("draft: true")
    if side.get("weight"):
        fm.append(f"weight: {side['weight']}")
    if aliases:
        fm.append(f"aliases: {json.dumps(aliases)}")
    fm.append("---")

    out_dir = CONTENT_DIR / rel
    out_dir.mkdir(parents=True, exist_ok=True)
    body = colorize(copy_images(body, tex_path.parent, f"{rel}"))
    if side.get("figure"):
        fig_url = copy_asset(str(side["figure"]), tex_path.parent, rel)
        if fig_url:
            fig_side = "left" if str(side.get("figure_side", "")).lower() == "left" else "right"
            body = add_figure(body, fig_url, str(side.get("figure_alt", "")), str(side.get("figure_caption", "")), fig_side)
            fm.insert(-1, f"figure: {yaml_str(fig_url)}")
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


SMALL_WORDS = {"a", "an", "and", "as", "at", "but", "by", "for", "in", "nor", "of", "on", "or", "the", "to", "vs", "with"}


def folder_title(folder: str) -> str:
    """Title for a folder page. A name with capitals is kept as written; an all-lowercase one is
    title-cased, leaving short words like "of" and "and" lowercase after the first word.
    chapter-<n>-<name> becomes "Chapter <n>: <Name>"."""
    m = re.fullmatch(r"chapter-(\d+)-(.+)", folder, re.I)
    if m:
        return f"Chapter {m.group(1)}: {folder_title(m.group(2))}"
    words = folder.replace("-", " ").replace("_", " ").split()
    if any(c.isupper() for c in folder):
        return " ".join(words)
    return " ".join(w if i and w in SMALL_WORDS else w[:1].upper() + w[1:] for i, w in enumerate(words))


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
        title = folder_title(folder)
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
    ap.add_argument("--pdf", action="store_true", help="compile proofs to PDF and render page previews (needs pdflatex and pdftoppm)")
    args = ap.parse_args()

    if shutil.which("pandoc") is None:
        print("pandoc not found on PATH. Install it: https://pandoc.org/installing.html", file=sys.stderr)
        return 1


    clean()
    scrubbed = scrub_images_under(ROOT / "static" / "images") + scrub_images_under(LATEX_DIR)
    if scrubbed and not args.quiet:
        print(f"  stripped metadata from {scrubbed} image(s) in static/images and latex")

    for folder in TYPES:
        for tex_path in (LATEX_DIR / folder).rglob("*.tex"):
            loc = locate(tex_path)
            if loc:
                CURRENT_URLS.add(page_url(*loc))

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
                convert_one(tex_path, section, subject, topic, verbose=not args.quiet, pdf=args.pdf)
                n_ok += 1
            except Exception as e:
                n_err += 1
                print(f"  ✗ {tex_path.relative_to(ROOT)}: {e}", file=sys.stderr)
    print(f"converted {n_ok} file(s), {n_err} error(s)")
    return 1 if n_err else 0


if __name__ == "__main__":
    sys.exit(main())
