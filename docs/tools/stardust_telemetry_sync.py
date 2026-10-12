#!/usr/bin/env python3
"""
stardust_telemetry_sync.py

Keeps the Stardust Telemetry Specification (sub-document) in sync with the
master AIforE Sustainable Ecosystems SDK Product Specification.

Sections are matched by their section NUMBER (e.g. "20.2", "21.3"). A synced
unit runs from its heading to the next heading of the same or higher level.

Commands
  extract  MASTER.docx  OUT_SUB.docx  [--version T1.1] [--note "§21.7|Added ..."]...
      Build a fresh telemetry sub-document from the master. Optional version
      label and change-log notes ("section|change"; repeatable).

  export   MASTER.docx  OUT_DIR
      Write machine-readable files for the code repository: spec-version.json,
      field-codes.json (every table with a Code column) and mcp-tools.json
      (every table with a Tool column, grouped by section).

  merge    MASTER.docx  SUB.docx  OUT_MASTER.docx  [--no-bump] [--dry-run] [--keep-formatting]
      Copy every synced unit from the sub-document back into the master,
      replacing the master's version of that unit. New numbered subsections
      added in the sub-document are carried along automatically, as are any
      styles they use that the master doesn't define. Content written outside
      the master (a section whose headings carry none of the master's direct
      formatting, or any paragraph with no formatting at all) is given the
      formatting of the master's copy of that section: headings, text, bullets,
      tables and spacing. --keep-formatting turns this off. Bumps the master's
      "vX.Y (Draft)" version unless --no-bump. Prints a change report.

Requires: Python 3.8+, lxml  (pip install lxml)
"""
import sys, re, copy, zipfile, os, datetime

from lxml import etree

W = "http://schemas.openxmlformats.org/wordprocessingml/2006/main"
R = "http://schemas.openxmlformats.org/officeDocument/2006/relationships"
W14 = "http://schemas.microsoft.com/office/word/2010/wordml"
PR = "http://schemas.openxmlformats.org/package/2006/relationships"
def q(ns, t): return "{%s}%s" % (ns, t)

# Synced units, in master order. Edit this list to change the sub-document's scope.
UNITS = [
    ("4",     "Core usage, cost and environmental metrics"),
    ("5.3",   "Stardust Job Identifier and Energy-Source Codes"),
    ("7",     "Core data model"),
    ("8.8.1", "Lifecycle emission component fields"),
    ("8.8.2", "Lifecycle amortization inputs and derived totals"),
    ("8.8.8", "Component-level embodied carbon and refresh cycles"),
    ("10.5",  "Remediation order data model"),
    ("11",    "OpenTelemetry harmonization and mapping rules"),
    ("20",    "Carbon capture Provider telemetry"),
    ("21.2",  "Right-sizing signal fields (raw)"),
    ("21.3",  "Right-sizing derived scores and indexes"),
    ("21.4",  "Right-sizing scoring method"),
    ("21.7",  "Time-of-use dynamics and carbon-aware load shifting"),
    ("21.8",  "Energy-aware routing telemetry and method"),
    ("21.9",  "Accelerator, node and facility runtime telemetry"),
    ("22.3",  "Multi-attribute Provider categories"),
    ("22.4",  "Multi-attribute Provider telemetry fields"),
    ("22.8",  "Hardware lifecycle record: reuse and recycling"),
]
SUB_VERSION = "T1.0"
NUM_RE = re.compile(r"^\s*(\d+(?:\.\d+)*)\.?\s+\S")

# ---------------------------------------------------------------- docx io
def read_docx(path):
    with zipfile.ZipFile(path) as z:
        return {n: z.read(n) for n in z.namelist()}

def write_docx(parts, path):
    with zipfile.ZipFile(path, "w", zipfile.ZIP_DEFLATED) as z:
        order = ["[Content_Types].xml"] + [n for n in parts if n != "[Content_Types].xml"]
        for n in order:
            z.writestr(n, parts[n])

def parse(b): return etree.fromstring(b, etree.XMLParser(remove_blank_text=False, huge_tree=True))
def ser(el): return etree.tostring(el, xml_declaration=True, encoding="UTF-8", standalone=True)

# ---------------------------------------------------------------- structure
def text_of(el): return "".join(el.itertext()) if el is not None else ""
def ptext(p): return "".join(t.text or "" for t in p.iter(q(W, "t")))

def heading_info(el):
    """Return (style_level, number_or_None, text) for heading paragraphs, else None."""
    if el.tag != q(W, "p"): return None
    ps = el.find("./w:pPr/w:pStyle", {"w": W})
    if ps is None: return None
    m = re.match(r"Heading(\d)", ps.get(q(W, "val"), ""))
    if not m: return None
    t = ptext(el)
    n = NUM_RE.match(t)
    return int(m.group(1)), (n.group(1) if n else None), t.strip()

def body_children(root):
    body = root.find(q(W, "body"))
    return body, list(body)

def unit_range(children, key, stop_keys=()):
    for i, el in enumerate(children):
        h = heading_info(el)
        if h and h[1] == key:
            lvl = h[0]
            for j in range(i + 1, len(children)):
                if children[j].tag == q(W, "sectPr"): return i, j
                hj = heading_info(children[j])
                if hj and (hj[0] <= lvl or (hj[1] in stop_keys and hj[1] != key)): return i, j
            return i, len(children)
    return None

def numkey(n): return tuple(int(p) for p in n.split("."))

# ---------------------------------------------------------------- fragments
def frag(root_tag_src, xml):
    doc = parse((root_tag_src + "<w:body>" + xml + "</w:body></w:document>").encode("utf-8"))
    return list(doc.find(q(W, "body")))

def esc(s): return s.replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;")
def runs(t, sz=21, color=None, italic=False):
    out = ""
    for i, part in enumerate(re.split(r"\*\*", t)):
        if not part: continue
        b = '<w:b w:val="1"/><w:bCs w:val="1"/>' if i % 2 else ""
        it = '<w:i w:val="1"/><w:iCs w:val="1"/>' if italic else ""
        c = f'<w:color w:val="{color}"/>' if color else ""
        out += f'<w:r><w:rPr>{b}{it}{c}<w:sz w:val="{sz}"/><w:szCs w:val="{sz}"/></w:rPr><w:t xml:space="preserve">{esc(part)}</w:t></w:r>'
    return out
def P(t, sz=21, jc=None, after=140, before=0, color=None, italic=False, keep=False):
    j = f'<w:jc w:val="{jc}"/>' if jc else ""
    k = '<w:keepNext/>' if keep else ""
    return f'<w:p><w:pPr>{k}<w:spacing w:before="{before}" w:after="{after}"/>{j}</w:pPr>{runs(t, sz, color, italic)}</w:p>'
def H1(t): return f'<w:p><w:pPr><w:pStyle w:val="Heading1"/><w:spacing w:after="160" w:before="360"/></w:pPr><w:r><w:rPr><w:b w:val="1"/><w:bCs w:val="1"/><w:color w:val="0f4a2e"/><w:sz w:val="30"/><w:szCs w:val="30"/></w:rPr><w:t xml:space="preserve">{esc(t)}</w:t></w:r></w:p>'
def BUL(t): return f'<w:p><w:pPr><w:numPr><w:ilvl w:val="0"/><w:numId w:val="1"/></w:numPr><w:spacing w:after="60" w:before="0" w:line="240" w:lineRule="auto"/><w:ind w:left="720" w:hanging="360"/></w:pPr>{runs(t)}</w:p>'
def TABLE(cols, widths, rows):
    mar = '<w:tcMar><w:top w:w="60" w:type="dxa"/><w:left w:w="100" w:type="dxa"/><w:bottom w:w="60" w:type="dxa"/><w:right w:w="100" w:type="dxa"/></w:tcMar>'
    def cell(t, w, head):
        shd = '<w:shd w:val="clear" w:color="auto" w:fill="1e7a4c"/>' if head else ""
        rp = ('<w:b w:val="1"/><w:bCs w:val="1"/><w:color w:val="ffffff"/>' if head else "") + '<w:sz w:val="19"/><w:szCs w:val="19"/>'
        return f'<w:tc><w:tcPr><w:tcW w:w="{w}" w:type="dxa"/>{shd}{mar}</w:tcPr><w:p><w:r><w:rPr>{rp}</w:rPr><w:t xml:space="preserve">{esc(t)}</w:t></w:r></w:p></w:tc>'
    b = "".join(f'<w:{s} w:val="single" w:sz="4" w:space="0" w:color="000000"/>' for s in ["top", "left", "bottom", "right", "insideH", "insideV"])
    out = f'<w:tbl><w:tblPr><w:tblW w:w="{sum(widths)}" w:type="dxa"/><w:tblBorders>{b}</w:tblBorders><w:tblLayout w:type="fixed"/><w:tblLook w:val="0000"/></w:tblPr><w:tblGrid>' + "".join(f'<w:gridCol w:w="{w}"/>' for w in widths) + "</w:tblGrid>"
    out += '<w:tr><w:trPr><w:tblHeader/></w:trPr>' + "".join(cell(c, w, True) for c, w in zip(cols, widths)) + "</w:tr>"
    for r in rows: out += '<w:tr><w:trPr><w:cantSplit/></w:trPr>' + "".join(cell(c, w, False) for c, w in zip(r, widths)) + "</w:tr>"
    return out + "</w:tbl>" + P("", after=120)

# ---------------------------------------------------------------- helpers
def find_version(root):
    for t in root.iter(q(W, "t")):
        m = re.search(r"v(\d+)\.(\d+) \(Draft\)", t.text or "")
        if m: return t, m
    return None, None

def code_index(elements):
    """Collect (code, name, section) from tables whose first header cell is 'Code'."""
    out, section = [], ""
    for el in elements:
        h = heading_info(el)
        if h and h[1]: section = h[1]
        for tbl in ([el] if el.tag == q(W, "tbl") else []):
            rows = tbl.findall(q(W, "tr"))
            if not rows: continue
            head = [text_of(c).strip() for c in rows[0].findall(q(W, "tc"))]
            if not head or head[0] != "Code": continue
            for r in rows[1:]:
                cells = [text_of(c).strip() for c in r.findall(q(W, "tc"))]
                if cells and cells[0]:
                    out.append((cells[0], cells[1] if len(cells) > 1 else "", section))
    return out

def fresh_para_ids(elements, used):
    n = 0x6A000000
    for el in elements:
        for p in el.iter(q(W, "p")):
            pid = p.get(q(W14, "paraId"))
            if pid is None: continue
            if pid in used:
                while "%08X" % n in used: n += 1
                p.set(q(W14, "paraId"), "%08X" % n); pid = "%08X" % n
            used.add(pid)

# ---------------------------------------------------------------- extract
def extract(master_path, out_path, version=None, notes=()):
    sub_version = version or SUB_VERSION
    parts = read_docx(master_path)
    src = parts["word/document.xml"].decode("utf-8")
    root_tag = re.search(r"<w:document[^>]*>", src).group(0)
    root = parse(parts["word/document.xml"])
    body, kids = body_children(root)
    vt, vm = find_version(root)
    master_ver = f"v{vm.group(1)}.{vm.group(2)}" if vm else "unknown"

    units, missing = [], []
    for key, _ in UNITS:
        rg = unit_range(kids, key)
        if not rg: missing.append(key); continue
        units.extend(copy.deepcopy(kids[rg[0]:rg[1]]))
    if missing: print("WARNING: not found in master:", ", ".join(missing))

    # drop comment anchors (comments live in the master only)
    for el in units:
        for tag in ("commentRangeStart", "commentRangeEnd"):
            for c in list(el.iter(q(W, tag))): c.getparent().remove(c)
        for c in list(el.iter(q(W, "commentReference"))):
            r = c.getparent(); r.getparent().remove(r)

    today = datetime.date.today().strftime("%B %Y")
    idx = code_index(units)
    manifest = [(k, d, "Synced") for k, d in UNITS if k not in missing]
    fm = (
        P("", after=0, before=1200) +
        P("Stardust Telemetry Specification", sz=44, jc="center", color="0f4a2e") +
        P("Code Name: STARDUST", sz=28, jc="center", color="1e7a4c", italic=True, after=240) +
        P(f"Telemetry Spec {sub_version} (Draft)", sz=24, jc="center") +
        P(f"Derived from: AIForE Sustainable Ecosystems SDK with Telemetry for Big Data, Product Specification {master_ver} (Draft)", sz=20, jc="center", color="555555") +
        P("AI for Ecology (AIforE.org)", sz=20, jc="center") +
        P(f"Prepared for Charles Finkelstein, President & Chairman · {today}", sz=20, jc="center", after=600) +
        H1("T1. About This Document") +
        P(f"This document contains only the telemetry portions of the Stardust product specification: field definitions and codes, identifiers, the core data model, lifecycle and right-sizing telemetry, Provider telemetry for carbon capture and multi-attribute credits, and the OpenTelemetry mapping. It is derived from master specification {master_ver} and is designed to be edited on its own and merged back.") +
        P("Every section keeps its master section number, so a reference such as §20.2 means the same thing in both documents. References to sections not included here (for example methodology in §8.1–§8.7, architecture in §9–§10, platform porting in §12, and credit connectors in §22.5) point to the master specification.") +
        H1("T2. Sync Manifest") +
        P("These sections are synced with the master. Each runs from its heading to the next heading of the same or higher level, and the whole section replaces the master’s copy on merge.") +
        TABLE(["Section", "Contents", "Status"], [1400, 6400, 1800], manifest) +
        H1("T3. Editing Rules for Merge-Back") +
        BUL("**Section numbers are the sync keys.** Keep each synced heading’s number and the “N.N Title” pattern. Do not renumber existing sections.") +
        BUL("**Edit freely inside a synced section.** Text, tables, bullets and fields can all change; the whole section replaces the master’s version on merge.") +
        BUL("**New subsections** can be added inside a synced section with the next unused number (for example 20.8 or 22.4.1). They merge automatically.") +
        BUL("**New sibling sections** (for example a new 21.7) are inserted into the master after the nearest lower-numbered sibling. Choose a number the master does not already use.") +
        BUL("**Field codes must stay unique** across the whole master specification. The merge report lists any duplicate codes it finds.") +
        BUL("**Keep cross-references** to master-only sections as § numbers; do not delete them.") +
        BUL("**T-sections (T1–T6) belong to this document only** and are never merged. Record every change in the T5 Change Log.") +
        BUL("**Comments and new list styles do not transfer.** Resolve comments before merging, and use the existing bullet style.") +
        BUL(f"**Versioning.** Increment this document’s version (T1.0 → T1.1) with each draft. Each merge increments the master’s version, after which a fresh sub-document is extracted at the new baseline.") +
        H1("T4. How to Merge Back") +
        BUL("Save the edited sub-document and the current master specification in the same folder as stardust_telemetry_sync.py (requires Python 3 and lxml).") +
        BUL("Preview: python stardust_telemetry_sync.py merge MASTER.docx SUB.docx OUT.docx --dry-run") +
        BUL("Merge: python stardust_telemetry_sync.py merge MASTER.docx SUB.docx OUT.docx — this writes the merged master, bumps its version, and prints a change report.") +
        BUL("Open the merged master in Word, update the table of contents (F9), and review the changed sections listed in the report.") +
        BUL("Re-extract a fresh sub-document: python stardust_telemetry_sync.py extract OUT.docx NEW_SUB.docx") +
        P("If the master has also changed since this sub-document was extracted, merge only after reconciling those sections by hand; the merge replaces the master’s copy of each synced section.", italic=True, color="555555") +
        H1("T5. Change Log") +
        TABLE(["Date", "Section", "Change", "Author"], [1400, 1200, 5400, 1600],
              [(datetime.date.today().isoformat(), "All", f"Extracted from master {master_ver}", "AIforE")] +
              [(datetime.date.today().isoformat(), n.split("|")[0], n.split("|")[1], "AIforE") for n in notes]) +
        H1("T6. Telemetry Field Code Index") +
        P("Generated at extraction from every table with a Code column. Regenerated on each extract; not merged.") +
        TABLE(["Code", "Field Name", "Section"], [1500, 6300, 1800], idx)
    )
    front = frag(root_tag, fm)
    sect = body.find(q(W, "sectPr"))
    for el in list(body): body.remove(el)
    for el in front + units: body.append(el)
    if sect is not None: body.append(sect)
    fresh_para_ids(front + units, set())
    parts["word/document.xml"] = ser(root)
    # core properties title
    core = parts.get("docProps/core.xml")
    if core:
        c = parse(core); DC = "http://purl.org/dc/elements/1.1/"
        t = c.find(q(DC, "title"))
        if t is None: t = etree.SubElement(c, q(DC, "title"))
        t.text = f"Stardust Telemetry Specification {sub_version} (from master {master_ver})"
        parts["docProps/core.xml"] = ser(c)
    write_docx(parts, out_path)
    print(f"Extracted {len(manifest)} units, {len(idx)} field codes -> {out_path}")

# ---------------------------------------------------------------- formatting
# The master carries its formatting directly on paragraphs and runs (spacing, size, colour)
# rather than in styles, so content written outside it renders unlike the rest of the spec.
# These helpers re-dress such content with formatting copied from the master itself.
NS = {"w": W}
KEEP_RUN = ("rStyle", "b", "bCs", "i", "iCs", "u", "strike", "vertAlign")   # meaning, not look
RPR_ORDER = ["rStyle", "rFonts", "b", "bCs", "i", "iCs", "caps", "smallCaps", "strike", "dstrike", "outline",
             "shadow", "emboss", "imprint", "noProof", "snapToGrid", "vanish", "webHidden", "color", "spacing",
             "w", "kern", "position", "sz", "szCs", "highlight", "u", "effect", "bdr", "shd", "fitText",
             "vertAlign", "rtl", "cs", "em", "lang", "eastAsianLayout", "specVanish", "oMath"]

def local(el): return etree.QName(el).localname
def is_on(el): return el.get(q(W, "val")) not in ("0", "false")
def runs_of(p): return [r for r in p.iter(q(W, "r")) if r.find(q(W, "t")) is not None]
def ncols(tbl): return len(tbl.find(q(W, "tr")).findall(q(W, "tc"))) if tbl.find(q(W, "tr")) is not None else 0

def set_first(parent, tag, new):
    old = parent.find(q(W, tag))
    if old is not None: parent.remove(old)
    if new is not None: parent.insert(0, copy.deepcopy(new))

def formatted(p):
    """True if a paragraph carries direct formatting beyond its style and bold/italic."""
    ppr = p.find(q(W, "pPr"))
    if ppr is not None and any(local(c) not in ("pStyle", "rPr") for c in ppr): return True
    for r in p.iter(q(W, "r")):
        rpr = r.find(q(W, "rPr"))
        if rpr is not None and any(local(c) not in KEEP_RUN for c in rpr): return True
    return False

def plain_rpr(p):
    """rPr of the paragraph's first non-bold text run (its body text, not a bold run-in)."""
    rs = runs_of(p)
    for r in rs:
        b = r.find("./w:rPr/w:b", NS)
        if b is None or not is_on(b): return r.find(q(W, "rPr"))
    return rs[0].find(q(W, "rPr")) if rs else None

def dressed_rpr(tmpl, run):
    """Template run formatting, keeping the run's own bold, italic, underline and so on."""
    rpr = copy.deepcopy(tmpl) if tmpl is not None else etree.Element(q(W, "rPr"))
    for c in [c for c in rpr if local(c) in KEEP_RUN]: rpr.remove(c)
    orig = run.find(q(W, "rPr"))
    for c in (orig if orig is not None else []):
        if local(c) in KEEP_RUN and is_on(c): rpr.append(copy.deepcopy(c))
    rpr[:] = sorted(rpr, key=lambda c: RPR_ORDER.index(local(c)) if local(c) in RPR_ORDER else len(RPR_ORDER))
    return rpr

def templates(els):
    """Formatting examples from master elements: headings by level, the commonest body and list
    paragraph, tables, and the empty paragraph that follows tables if that is the master's habit."""
    t = {"head": {}, "body": None, "list": None, "tables": [], "spacer": None}
    body, lst, after = {}, {}, []
    for e in els:
        if e.tag == q(W, "tbl"):
            t["tables"].append(e)
            nxt = e.getnext()
            after.append(nxt if nxt is not None and nxt.tag == q(W, "p") and not ptext(nxt).strip() else None)
            continue
        if e.tag != q(W, "p") or not formatted(e) or not ptext(e).strip(): continue
        h = heading_info(e)
        if h: t["head"].setdefault(h[0], e); continue
        if e.find("./w:pPr/w:pStyle", NS) is not None: continue
        bucket = lst if e.find("./w:pPr/w:numPr", NS) is not None else body
        sig = etree.tostring(e.find(q(W, "pPr"))) + etree.tostring(plain_rpr(e) if plain_rpr(e) is not None else etree.Element("x"))
        bucket.setdefault(sig, []).append(e)
    for key, bucket in (("body", body), ("list", lst)):
        if bucket: t[key] = max(bucket.values(), key=len)[0]
    spacers = [s for s in after if s is not None]
    if spacers and len(spacers) * 2 > len(after): t["spacer"] = spacers[0]
    return t

def subsection_kinds(els, key):
    """For each subsection of a master unit, whether its paragraphs are mostly bullets."""
    counts, sec = {}, key
    for e in els:
        h = heading_info(e)
        if h and h[1]: sec = h[1]; continue
        if e.tag == q(W, "p") and ptext(e).strip() and e.find("./w:pPr/w:pStyle", NS) is None:
            c = counts.setdefault(sec, [0, 0]); c[e.find("./w:pPr/w:numPr", NS) is not None] += 1
    return {s: "list" if c[1] > c[0] else "body" for s, c in counts.items()}

def dress_table(tbl, tmpl):
    same = ncols(tmpl) == ncols(tbl)
    set_first(tbl, "tblPr", tmpl.find(q(W, "tblPr")))
    if same:
        grid = tbl.find(q(W, "tblGrid"))
        if grid is not None: grid.getparent().replace(grid, copy.deepcopy(tmpl.find(q(W, "tblGrid"))))
    trows = tmpl.findall(q(W, "tr"))
    for i, row in enumerate(tbl.findall(q(W, "tr"))):
        trow = trows[0] if i == 0 else trows[min(1, len(trows) - 1)]
        set_first(row, "trPr", trow.find(q(W, "trPr")))
        tcells = trow.findall(q(W, "tc"))
        for j, cell in enumerate(row.findall(q(W, "tc"))):
            tcell = tcells[min(j, len(tcells) - 1)]
            old = cell.find(q(W, "tcPr"))
            set_first(cell, "tcPr", tcell.find(q(W, "tcPr")))
            new = cell.find(q(W, "tcPr"))
            if old is not None and new is not None:
                # keep the cell's own geometry: width when columns differ, and any spans or merges
                for tag in (() if same else ("tcW",)) + ("gridSpan", "vMerge", "hMerge"):
                    for c in new.findall(q(W, tag)): new.remove(c)
                    mine = old.find(q(W, tag))
                    if mine is not None: new.insert(0 if tag == "tcW" else len(new.findall(q(W, "tcW"))), copy.deepcopy(mine))
            tp = tcell.find(q(W, "p"))
            for p in cell.findall(q(W, "p")):
                set_first(p, "pPr", tp.find(q(W, "pPr")))
                for r in runs_of(p):
                    # header cells take the template's look outright; body cells keep their emphasis
                    set_first(r, "rPr", (runs_of(tp)[0].find(q(W, "rPr")) if runs_of(tp) else None) if i == 0 else dressed_rpr(plain_rpr(tp), r))

def match_formatting(new_els, old_els, master_els, key):
    """Give merged content the master's formatting. Returns (elements, whether anything changed).

    A unit whose top heading has no direct formatting was written outside the master, so all of
    it (headings, text, lists, tables, spacing after tables) is formatted like the master's copy
    of that unit, falling back to the rest of the master. Otherwise only paragraphs with no
    direct formatting at all are touched; formatting someone applied on purpose is kept."""
    loc, glob = templates(old_els), templates(master_els)
    pick = lambda k: loc[k] if loc[k] is not None else glob[k]
    head = lambda lvl: loc["head"].get(lvl) if loc["head"].get(lvl) is not None else glob["head"].get(lvl)
    first = heading_info(new_els[0]) if new_els else None
    foreign = bool(first) and not formatted(new_els[0]) and head(first[0]) is not None
    kinds = subsection_kinds(old_els, key)
    out, changed, sec = [], False, key
    for e in new_els:
        out.append(e)
        if e.tag == q(W, "tbl"):
            if not foreign: continue
            candidates = loc["tables"] + glob["tables"]
            tmpl = next((t for t in candidates if ncols(t) == ncols(e)), None)
            if tmpl is None and candidates: tmpl = candidates[0]
            if tmpl is not None: dress_table(e, tmpl); changed = True
            out.append(None)                                   # spacer slot, filled below
            continue
        if e.tag != q(W, "p"): continue
        h = heading_info(e)
        if h and h[1]: sec = h[1]
        if not ptext(e).strip() or (formatted(e) and not foreign): continue
        if h:
            tp = head(h[0])
            if tp is None: continue
            set_first(e, "pPr", tp.find(q(W, "pPr")))
            for r in runs_of(e): set_first(r, "rPr", runs_of(tp)[0].find(q(W, "rPr")))
        else:
            if e.find("./w:pPr/w:pStyle", NS) is not None: continue   # a deliberate style, e.g. a caption
            tp = pick("list") if kinds.get(sec) == "list" and pick("list") is not None else pick("body")
            if tp is None: continue
            set_first(e, "pPr", tp.find(q(W, "pPr")))
            for r in runs_of(e): set_first(r, "rPr", dressed_rpr(plain_rpr(tp), r))
        changed = True
    spacer = pick("spacer")
    result = []
    for i, e in enumerate(out):
        if e is not None: result.append(e); continue
        nxt = next((x for x in out[i + 1:] if x is not None), None)
        if spacer is not None and not (nxt is not None and nxt.tag == q(W, "p") and not ptext(nxt).strip()):
            result.append(copy.deepcopy(spacer)); changed = True
    return result, changed

# ---------------------------------------------------------------- merge
def rels_map(parts):
    r = parse(parts["word/_rels/document.xml.rels"])
    return r, {e.get("Id"): e for e in r}

def merge(master_path, sub_path, out_path, bump=True, dry=False, keep_formatting=False):
    mp, sp = read_docx(master_path), read_docx(sub_path)
    mroot, sroot = parse(mp["word/document.xml"]), parse(sp["word/document.xml"])
    mbody, mk = body_children(mroot)
    _, sk = body_children(sroot)
    report, warn = [], []

    # which units does the sub-document contain? top-most numbered headings
    sub_heads = [(i, heading_info(e)) for i, e in enumerate(sk)]
    sub_heads = [(i, h) for i, h in sub_heads if h and h[1]]
    sub_keys = [h[1] for _, h in sub_heads]
    top = [k for k in sub_keys if not any(k != o and k.startswith(o + ".") for o in sub_keys)]
    for key, _ in UNITS:
        if key not in sub_keys: warn.append(f"Unit {key} missing from sub-document; master copy left unchanged.")

    mrels_root, mrels = rels_map(mp)
    _, srels = rels_map(sp)
    used_ids = {p.get(q(W14, "paraId")) for p in mroot.iter(q(W, "p")) if p.get(q(W14, "paraId"))}
    mnums = {n.get(q(W, "numId")) for n in parse(mp["word/numbering.xml"]).iter(q(W, "num"))} if "word/numbering.xml" in mp else set()

    # styles the sub-document defines but the master lacks are copied across
    mstyles_root = parse(mp["word/styles.xml"]) if "word/styles.xml" in mp else None
    sstyles_root = parse(sp["word/styles.xml"]) if "word/styles.xml" in sp else None
    def style_ids(root): return {s.get(q(W, "styleId")): s for s in root.iter(q(W, "style"))} if root is not None else {}
    mstyles, sstyles = style_ids(mstyles_root), style_ids(sstyles_root)
    carried = []

    def carry_styles(elements):
        queue = [n.get(q(W, "val")) for el in elements for tag in ("pStyle", "rStyle", "tblStyle") for n in el.iter(q(W, tag))]
        while queue:
            sid = queue.pop()
            if not sid or sid in mstyles: continue
            src = sstyles.get(sid)
            if src is None or mstyles_root is None:
                warn.append(f"Style {sid} is not defined in either document; Word will fall back to the default."); continue
            new = copy.deepcopy(src)
            mstyles_root.append(new); mstyles[sid] = new; carried.append(sid)
            # a style is only complete with the styles it inherits from or links to
            queue.extend(n.get(q(W, "val")) for tag in ("basedOn", "next", "link") for n in new.findall(q(W, tag)))
            if new.find(".//w:numPr", {"w": W}) is not None:
                warn.append(f"Style {sid} uses list numbering, which is not carried; check its lists.")

    def fix_refs(elements):
        carry_styles(elements)
        for el in elements:
            for node in el.iter():
                for attr in list(node.attrib):
                    if not attr.startswith("{%s}" % R): continue
                    rid = node.get(attr)
                    srel = srels.get(rid)
                    if srel is None: continue
                    mrel = mrels.get(rid)
                    if mrel is not None and mrel.get("Target") == srel.get("Target") and mrel.get("Type") == srel.get("Type"):
                        continue
                    target = srel.get("Target")
                    new_target = target
                    if srel.get("TargetMode") != "External":
                        spath = "word/" + target
                        base, ext = os.path.splitext(os.path.basename(target))
                        n = 1
                        new_target = f"media/{base}_t{n}{ext}"
                        while "word/" + new_target in mp: n += 1; new_target = f"media/{base}_t{n}{ext}"
                        mp["word/" + new_target] = sp[spath]
                    k = 900
                    while f"rId{k}" in mrels: k += 1
                    new = etree.SubElement(mrels_root, q(PR, "Relationship"))
                    new.set("Id", f"rId{k}"); new.set("Type", srel.get("Type")); new.set("Target", new_target)
                    if srel.get("TargetMode"): new.set("TargetMode", srel.get("TargetMode"))
                    mrels[f"rId{k}"] = new
                    node.set(attr, f"rId{k}")
            for tag in ("commentRangeStart", "commentRangeEnd"):
                for c in list(el.iter(q(W, tag))): c.getparent().remove(c)
            for c in list(el.iter(q(W, "commentReference"))):
                r = c.getparent(); r.getparent().remove(r); warn.append("A comment in the sub-document was dropped (comments do not transfer).")
            for n in el.iter(q(W, "numId")):
                if n.get(q(W, "val")) not in mnums: warn.append(f"List numId {n.get(q(W, 'val'))} not defined in master; check list formatting.")

    for key in top:
        srg = unit_range(sk, key, stop_keys=set(top))
        new_els = copy.deepcopy(sk[srg[0]:srg[1]])
        mk = list(mbody)
        mrg = unit_range(mk, key)
        new_txt = "\n".join(text_of(e) for e in new_els)
        if mrg:
            old_txt = "\n".join(text_of(e) for e in mk[mrg[0]:mrg[1]])
            if old_txt == new_txt:
                report.append(f"  §{key}: unchanged"); continue
            old_heads = {heading_info(e)[1] for e in mk[mrg[0]:mrg[1]] if heading_info(e) and heading_info(e)[1]}
            new_heads = {heading_info(e)[1] for e in new_els if heading_info(e) and heading_info(e)[1]}
            added = sorted(new_heads - old_heads, key=numkey); removed = sorted(old_heads - new_heads, key=numkey)
            note = (f"; added {', '.join(added)}" if added else "") + (f"; removed {', '.join(removed)}" if removed else "")
            if not keep_formatting:
                new_els, dressed = match_formatting(new_els, mk[mrg[0]:mrg[1]], mk, key)
                if dressed: note += "; formatting matched to master"
            report.append(f"  §{key}: UPDATED{note}")
            fix_refs(new_els); fresh_para_ids(new_els, used_ids)
            anchor = mk[mrg[0]]
            idx = list(mbody).index(anchor)
            for e in mk[mrg[0]:mrg[1]]: mbody.remove(e)
            for off, e in enumerate(new_els): mbody.insert(idx + off, e)
        else:
            parent = key.rsplit(".", 1)[0] if "." in key else None
            sibs = []
            for i, e in enumerate(mk):
                h = heading_info(e)
                if h and h[1] and "." in h[1] and h[1].rsplit(".", 1)[0] == parent and numkey(h[1]) < numkey(key):
                    sibs.append(h[1])
            if parent is None or not sibs and not unit_range(mk, parent):
                warn.append(f"§{key} is new but has no place in the master; add it by hand."); continue
            after = max(sibs, key=numkey) if sibs else None
            if after:
                pos = unit_range(mk, after)[1]
            else:
                prg = unit_range(mk, parent)
                pos = prg[0] + 1
                for j in range(prg[0] + 1, prg[1]):
                    if heading_info(mk[j]): pos = j; break
                    pos = j + 1
            dressed = False
            if not keep_formatting: new_els, dressed = match_formatting(new_els, [], mk, key)
            fix_refs(new_els); fresh_para_ids(new_els, used_ids)
            anchor = mk[pos] if pos < len(mk) else None
            for e in new_els:
                if anchor is not None: anchor.addprevious(e)
                else: mbody.append(e)
            report.append(f"  §{key}: NEW section inserted" + (f" after §{after}" if after else f" at start of §{parent}")
                          + ("; formatting matched to master" if dressed else ""))

    # duplicate field codes
    codes = {}
    for c, name, sec in code_index(list(mbody)):
        codes.setdefault(c, []).append(sec)
    dups = {c: s for c, s in codes.items() if len(s) > 1}
    for c, s in dups.items(): warn.append(f"Field code {c} appears more than once (sections {', '.join(s)}).")

    vt, vm = find_version(mroot)
    old_v = new_v = None
    if vm:
        old_v = f"v{vm.group(1)}.{vm.group(2)}"
        if bump and any("UPDATED" in r or "NEW" in r for r in report):
            new_v = f"v{vm.group(1)}.{int(vm.group(2)) + 1}"
            vt.text = vt.text.replace(old_v + " (Draft)", new_v + " (Draft)")

    print(f"Merge report: {os.path.basename(sub_path)} -> {os.path.basename(master_path)}")
    print("\n".join(report) if report else "  (no synced sections found)")
    if carried: print(f"  Styles carried from sub-document: {', '.join(carried)}")
    if new_v: print(f"  Master version: {old_v} -> {new_v}")
    for w in dict.fromkeys(warn): print("WARNING:", w)
    if dry:
        print("Dry run: nothing written."); return
    mp["word/document.xml"] = ser(mroot)
    if carried: mp["word/styles.xml"] = ser(mstyles_root)
    mp["word/_rels/document.xml.rels"] = ser(mrels_root)
    # make sure image extensions are registered
    ct = parse(mp["[Content_Types].xml"]); CT = "http://schemas.openxmlformats.org/package/2006/content-types"
    have = {d.get("Extension") for d in ct.iter(q(CT, "Default"))}
    for n in mp:
        ext = n.rsplit(".", 1)[-1].lower()
        if n.startswith("word/media/") and not n.endswith("/") and "." in os.path.basename(n) and ext not in have:
            d = etree.SubElement(ct, q(CT, "Default")); d.set("Extension", ext)
            d.set("ContentType", {"png": "image/png", "jpg": "image/jpeg", "jpeg": "image/jpeg", "gif": "image/gif", "svg": "image/svg+xml"}.get(ext, "application/octet-stream"))
            have.add(ext)
    mp["[Content_Types].xml"] = ser(ct)
    write_docx(mp, out_path)
    print(f"Wrote {out_path}")

def export(master_path, out_dir):
    import json
    parts = read_docx(master_path); root = parse(parts["word/document.xml"])
    _, kids = body_children(root)
    vt, vm = find_version(root)
    ver = f"{vm.group(1)}.{vm.group(2)}" if vm else "unknown"
    os.makedirs(out_dir, exist_ok=True)
    codes, tools, section, title = [], [], "", ""
    for el in kids:
        h = heading_info(el)
        if h and h[1]: section, title = h[1], h[2]
        if el.tag != q(W, "tbl"): continue
        rows = el.findall(q(W, "tr"))
        if not rows: continue
        head = [text_of(c).strip() for c in rows[0].findall(q(W, "tc"))]
        keys = [re.sub(r"[^a-z0-9]+", "_", c.lower()).strip("_") for c in head]
        for r in rows[1:]:
            cells = [text_of(c).strip() for c in r.findall(q(W, "tc"))]
            rec = dict(zip(keys, cells))
            if head and head[0] == "Code" and cells and cells[0]:
                rec["derived"] = cells[0].startswith("D-"); rec["section"] = section; codes.append(rec)
            elif head and head[0] == "Tool" and cells and cells[0]:
                rec["section"] = section; rec["server"] = title; tools.append(rec)
    dup = sorted({c["code"] for c in codes if sum(1 for d in codes if d["code"] == c["code"]) > 1})
    meta = {"spec_version": ver, "spec_title": "AIForE Sustainable Ecosystems SDK with Telemetry for Big Data",
            "generated": datetime.date.today().isoformat(), "field_codes": len(codes), "mcp_tools": len(tools)}
    for name, data in [("spec-version.json", meta), ("field-codes.json", {"spec_version": ver, "codes": codes}),
                       ("mcp-tools.json", {"spec_version": ver, "tools": tools})]:
        with open(os.path.join(out_dir, name), "w") as f: json.dump(data, f, indent=2, ensure_ascii=False)
    print(f"Exported spec v{ver}: {len(codes)} field codes, {len(tools)} MCP tools -> {out_dir}")
    if dup: print("WARNING: duplicate field codes:", ", ".join(dup))

if __name__ == "__main__":
    a = sys.argv[1:]
    if len(a) >= 3 and a[0] == "extract":
        ver = a[a.index("--version") + 1] if "--version" in a else None
        notes = [a[i + 1] for i, v in enumerate(a) if v == "--note"]
        extract(a[1], a[2], ver, notes)
    elif len(a) >= 3 and a[0] == "export":
        export(a[1], a[2])
    elif len(a) >= 4 and a[0] == "merge":
        merge(a[1], a[2], a[3], bump="--no-bump" not in a, dry="--dry-run" in a, keep_formatting="--keep-formatting" in a)
    else:
        print(__doc__); sys.exit(1)
