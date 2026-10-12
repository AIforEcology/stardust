"""Tests for docs/tools/stardust_telemetry_sync.py.

Most tests build small .docx files whose formatting follows the master's conventions (direct
paragraph and run formatting, dark table headers, an empty paragraph after each table), so each
behaviour is checked in isolation. The last few run against the committed v1.6 master and
T1.5 sub-document.
"""
import json
import os
import sys
import zipfile
from pathlib import Path

import pytest
from lxml import etree

TOOLS = Path(__file__).resolve().parents[1]
REPO = TOOLS.parents[1]
sys.path.insert(0, str(TOOLS))
import stardust_telemetry_sync as sync  # noqa: E402

W_NS = "http://schemas.openxmlformats.org/wordprocessingml/2006/main"
W = "{%s}" % W_NS
NS = {"w": W_NS}

# --- building documents -----------------------------------------------------------------------

MASTER_HEAD = {1: ("360", "160", "0f4a2e", "30"), 2: ("280", "120", "1e7a4c", "24"), 3: ("200", "100", "333333", "21")}
MASTER_FILL = "1e7a4c"
FOREIGN_FILL = "E8F5EC"


def esc(t):
    return t.replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;")


def run(text, bold=False, sz=None):
    rpr = ('<w:b w:val="1"/><w:bCs w:val="1"/>' if bold else "") + (f'<w:sz w:val="{sz}"/><w:szCs w:val="{sz}"/>' if sz else "")
    return f'<w:r>{"<w:rPr>" + rpr + "</w:rPr>" if rpr else ""}<w:t xml:space="preserve">{esc(text)}</w:t></w:r>'


def H(level, text, fmt=True):
    before, after, color, sz = MASTER_HEAD[level]
    ppr = f'<w:pStyle w:val="Heading{level}"/>' + (f'<w:spacing w:before="{before}" w:after="{after}"/>' if fmt else "")
    r = (f'<w:r><w:rPr><w:b w:val="1"/><w:bCs w:val="1"/><w:color w:val="{color}"/><w:sz w:val="{sz}"/><w:szCs w:val="{sz}"/></w:rPr>'
         f'<w:t xml:space="preserve">{esc(text)}</w:t></w:r>') if fmt else run(text)
    return f"<w:p><w:pPr>{ppr}</w:pPr>{r}</w:p>"


def P(text, fmt=True, lead=None):
    sz = "21" if fmt else None
    ppr = '<w:pPr><w:spacing w:after="140"/></w:pPr>' if fmt else ""
    return f"<w:p>{ppr}{run(lead, True, sz) if lead else ''}{run(text, False, sz)}</w:p>"


def BUL(text, lead, fmt=True):
    if not fmt:
        return P(text, fmt=False, lead=lead)
    ppr = ('<w:pPr><w:numPr><w:ilvl w:val="0"/><w:numId w:val="1"/></w:numPr>'
           '<w:spacing w:after="60"/><w:ind w:left="720" w:hanging="360"/></w:pPr>')
    return f"<w:p>{ppr}{run(lead, True, '21')}{run(text, False, '21')}</w:p>"


def SPACER():
    return '<w:p><w:pPr><w:spacing w:after="120"/></w:pPr></w:p>'


def TBL(rows, fmt=True, widths=None, style=None):
    widths = widths or [9600 // len(rows[0])] * len(rows[0])
    if fmt:
        borders = "".join(f'<w:{s} w:val="single" w:sz="4" w:space="0" w:color="000000"/>'
                          for s in ("top", "left", "bottom", "right", "insideH", "insideV"))
        tblpr = f'<w:tblW w:w="{sum(widths)}" w:type="dxa"/><w:tblBorders>{borders}</w:tblBorders><w:tblLayout w:type="fixed"/>'
    else:
        tblpr = f'<w:tblW w:w="{sum(widths)}" w:type="dxa"/>'
    if style:
        tblpr = f'<w:tblStyle w:val="{style}"/>' + tblpr

    def cell(t, w, head):
        if fmt:
            shd = f'<w:shd w:val="clear" w:color="auto" w:fill="{MASTER_FILL}"/>' if head else ""
            rpr = ('<w:b w:val="1"/><w:bCs w:val="1"/><w:color w:val="ffffff"/>' if head else "") + '<w:sz w:val="19"/><w:szCs w:val="19"/>'
        else:
            shd = f'<w:shd w:val="clear" w:fill="{FOREIGN_FILL}"/>' if head else ""
            rpr = ('<w:b/><w:bCs/>' if head else '<w:b w:val="false"/>') + '<w:sz w:val="18"/><w:szCs w:val="18"/>'
        return (f'<w:tc><w:tcPr><w:tcW w:w="{w}" w:type="dxa"/>{shd}</w:tcPr>'
                f'<w:p><w:r><w:rPr>{rpr}</w:rPr><w:t xml:space="preserve">{esc(t)}</w:t></w:r></w:p></w:tc>')

    out = f"<w:tbl><w:tblPr>{tblpr}</w:tblPr><w:tblGrid>" + "".join(f'<w:gridCol w:w="{w}"/>' for w in widths) + "</w:tblGrid>"
    for i, r in enumerate(rows):
        out += "<w:tr>" + ('<w:trPr><w:tblHeader/></w:trPr>' if i == 0 else "") + "".join(cell(c, w, i == 0) for c, w in zip(r, widths)) + "</w:tr>"
    return out + "</w:tbl>"


STYLES = ('<w:style w:type="paragraph" w:default="1" w:styleId="Normal"><w:name w:val="Normal"/></w:style>'
          + "".join(f'<w:style w:type="paragraph" w:styleId="Heading{n}"><w:name w:val="heading {n}"/><w:basedOn w:val="Normal"/></w:style>' for n in (1, 2, 3))
          + '<w:style w:type="table" w:default="1" w:styleId="TableNormal"><w:name w:val="Normal Table"/></w:style>')


def make_docx(path, body, version="v1.5", extra_styles=""):
    doc = (f'<?xml version="1.0" encoding="UTF-8" standalone="yes"?>'
           f'<w:document xmlns:w="{W_NS}" xmlns:r="http://schemas.openxmlformats.org/officeDocument/2006/relationships" '
           f'xmlns:w14="http://schemas.microsoft.com/office/word/2010/wordml"><w:body>'
           f'{P("Product Specification — " + version + " (Draft)")}{body}<w:sectPr/></w:body></w:document>')
    parts = {
        "[Content_Types].xml": (
            '<?xml version="1.0" encoding="UTF-8"?><Types xmlns="http://schemas.openxmlformats.org/package/2006/content-types">'
            '<Default Extension="rels" ContentType="application/vnd.openxmlformats-package.relationships+xml"/>'
            '<Default Extension="xml" ContentType="application/xml"/>'
            '<Override PartName="/word/document.xml" ContentType="application/vnd.openxmlformats-officedocument.wordprocessingml.document.main+xml"/>'
            '<Override PartName="/word/styles.xml" ContentType="application/vnd.openxmlformats-officedocument.wordprocessingml.styles+xml"/>'
            '<Override PartName="/word/numbering.xml" ContentType="application/vnd.openxmlformats-officedocument.wordprocessingml.numbering+xml"/>'
            '</Types>'),
        "_rels/.rels": (
            '<?xml version="1.0" encoding="UTF-8"?><Relationships xmlns="http://schemas.openxmlformats.org/package/2006/relationships">'
            '<Relationship Id="rId1" Type="http://schemas.openxmlformats.org/officeDocument/2006/relationships/officeDocument" Target="word/document.xml"/>'
            '</Relationships>'),
        "word/_rels/document.xml.rels": (
            '<?xml version="1.0" encoding="UTF-8"?><Relationships xmlns="http://schemas.openxmlformats.org/package/2006/relationships">'
            '<Relationship Id="rId1" Type="http://schemas.openxmlformats.org/officeDocument/2006/relationships/styles" Target="styles.xml"/>'
            '<Relationship Id="rId2" Type="http://schemas.openxmlformats.org/officeDocument/2006/relationships/numbering" Target="numbering.xml"/>'
            '</Relationships>'),
        "word/styles.xml": f'<?xml version="1.0" encoding="UTF-8"?><w:styles xmlns:w="{W_NS}">{STYLES}{extra_styles}</w:styles>',
        "word/numbering.xml": (f'<?xml version="1.0" encoding="UTF-8"?><w:numbering xmlns:w="{W_NS}">'
                               '<w:abstractNum w:abstractNumId="0"><w:lvl w:ilvl="0"><w:numFmt w:val="bullet"/></w:lvl></w:abstractNum>'
                               '<w:num w:numId="1"><w:abstractNumId w:val="0"/></w:num></w:numbering>'),
        "word/document.xml": doc,
    }
    with zipfile.ZipFile(path, "w") as z:
        for name, data in parts.items():
            z.writestr(name, data)
    return str(path)


# The master: §7 with a table, an untouched §8, and §21.7 with a fields table and a bulleted method.
FIELDS = [["Code", "Field Name", "Definition"], ["TOU", "Time-of-Use Period", "Tariff period"]]
MASTER_721 = (H(2, "21.7 Time-of-Use") + P("Time intro.") + H(3, "21.7.1 Fields") + TBL(FIELDS) + SPACER()
              + H(3, "21.7.2 Method") + BUL(" Savings count only when declared.", "Declared before.")
              + BUL(" Forecasts are kept.", "Forecast integrity."))
MASTER_BODY = (H(1, "7. Data Model") + P("A normalized event schema.") + TBL([["Field", "Type"], ["event_id", "UUID"]]) + SPACER()
               + H(1, "8. Methodology") + P("Untouched methodology text.")
               + H(1, "21. Efficiency") + P("Efficiency intro.") + MASTER_721
               + H(2, "21.8 Routing") + P("Routing text."))


@pytest.fixture
def master(tmp_path):
    return make_docx(tmp_path / "master.docx", MASTER_BODY)


def merge(master, sub, out, capsys, **kw):
    sync.merge(master, sub, str(out), **kw)
    return capsys.readouterr().out


def doc(path):
    return etree.fromstring(zipfile.ZipFile(path).read("word/document.xml"))


def para(root, start):
    return next(p for p in root.iter(W + "p") if sync.ptext(p).startswith(start))


def tables(root):
    return list(root.iter(W + "tbl"))


def c14n(el):
    return etree.tostring(el, method="c14n")


# --- merge: what gets replaced ----------------------------------------------------------------

def test_identical_sub_reports_unchanged_and_keeps_version(master, tmp_path, capsys):
    out = merge(master, master, tmp_path / "out.docx", capsys)
    # §21.7 sits inside §21 here, so the top-level units are §7, §8 and §21
    assert "§7: unchanged" in out and "§21: unchanged" in out
    assert "Master version" not in out
    assert sync.find_version(doc(tmp_path / "out.docx"))[1].group(0) == "v1.5 (Draft)"


def test_updated_unit_replaces_only_that_unit_and_bumps_version(master, tmp_path, capsys):
    sub = make_docx(tmp_path / "sub.docx", MASTER_721.replace("Time intro.", "Time intro, revised.")
                    + H(3, "21.7.4 Matching Rules") + P("Overlap rule."))
    out = merge(master, sub, tmp_path / "out.docx", capsys)
    assert "§21.7: UPDATED; added 21.7.4" in out
    assert "Master version: v1.5 -> v1.6" in out
    before, after = doc(master), doc(tmp_path / "out.docx")
    assert sync.find_version(after)[1].group(0) == "v1.6 (Draft)"
    assert para(after, "Time intro, revised") is not None
    # the 21.7.4 subsection lands inside §21.7, before §21.8
    texts = [sync.ptext(p) for p in after.iter(W + "p")]
    assert texts.index("21.7.4 Matching Rules") < texts.index("21.8 Routing")
    # §7, §8 and §21.8 are byte-for-byte the master's
    for start in ("7. Data Model", "A normalized event schema", "8. Methodology", "Untouched methodology", "21.8 Routing"):
        assert c14n(para(after, start)) == c14n(para(before, start))
    assert c14n(tables(after)[0]) == c14n(tables(before)[0])


def test_no_bump_and_dry_run(master, tmp_path, capsys):
    sub = make_docx(tmp_path / "sub.docx", MASTER_721.replace("Time intro.", "Changed."))
    merge(master, sub, tmp_path / "keep.docx", capsys, bump=False)
    assert sync.find_version(doc(tmp_path / "keep.docx"))[1].group(0) == "v1.5 (Draft)"
    out = merge(master, sub, tmp_path / "dry.docx", capsys, dry=True)
    assert "Dry run: nothing written." in out and "§21.7: UPDATED" in out
    assert not (tmp_path / "dry.docx").exists()


def test_new_sibling_section_is_inserted_after_the_nearest_lower_sibling(master, tmp_path, capsys):
    sub = make_docx(tmp_path / "sub.docx", H(2, "21.9 Runtime Telemetry") + P("Accelerator fields."))
    out = merge(master, sub, tmp_path / "out.docx", capsys)
    assert "§21.9: NEW section inserted after §21.8" in out
    texts = [sync.ptext(p) for p in doc(tmp_path / "out.docx").iter(W + "p")]
    assert texts.index("Routing text.") < texts.index("21.9 Runtime Telemetry") < texts.index("Accelerator fields.")


def test_units_missing_from_sub_are_warned_and_left_alone(master, tmp_path, capsys):
    sub = make_docx(tmp_path / "sub.docx", MASTER_721.replace("Time intro.", "Changed."))
    out = merge(master, sub, tmp_path / "out.docx", capsys)
    assert "WARNING: Unit 7 missing from sub-document; master copy left unchanged." in out
    assert para(doc(tmp_path / "out.docx"), "A normalized event schema") is not None


def test_duplicate_field_codes_are_reported(master, tmp_path, capsys):
    dup = MASTER_721.replace("Time intro.", "Changed.") + H(3, "21.7.4 More") + TBL([["Code", "Field Name"], ["TOU", "Again"]])
    out = merge(master, make_docx(tmp_path / "sub.docx", dup), tmp_path / "out.docx", capsys)
    assert "Field code TOU appears more than once" in out


# --- merge: styles ----------------------------------------------------------------------------

SUB_STYLES = ('<w:style w:type="table" w:customStyle="1" w:styleId="SubTable"><w:name w:val="Sub Table"/>'
              '<w:basedOn w:val="SubBase"/></w:style>'
              '<w:style w:type="table" w:customStyle="1" w:styleId="SubBase"><w:name w:val="Sub Base"/>'
              '<w:tblPr><w:tblCellMar><w:left w:w="100" w:type="dxa"/></w:tblCellMar></w:tblPr></w:style>')


def styles_of(path):
    return etree.fromstring(zipfile.ZipFile(path).read("word/styles.xml"))


def test_styles_used_by_merged_content_are_carried_with_their_chain(master, tmp_path, capsys):
    # merge compares text, so the unit must change for its formatting to come across
    body = MASTER_721.replace(TBL(FIELDS), TBL(FIELDS, style="SubTable")).replace("Time intro.", "Changed.")
    sub = make_docx(tmp_path / "sub.docx", body, extra_styles=SUB_STYLES)
    out = merge(master, sub, tmp_path / "out.docx", capsys)
    assert "Styles carried from sub-document: SubTable, SubBase" in out
    ids = {s.get(W + "styleId") for s in styles_of(tmp_path / "out.docx").iter(W + "style")}
    assert {"SubTable", "SubBase", "Heading1"} <= ids


def test_style_defined_nowhere_is_warned(master, tmp_path, capsys):
    body = MASTER_721.replace(TBL(FIELDS), TBL(FIELDS, style="Ghost")).replace("Time intro.", "Changed.")
    out = merge(master, make_docx(tmp_path / "sub.docx", body), tmp_path / "out.docx", capsys)
    assert "WARNING: Style Ghost is not defined in either document" in out


def test_styles_xml_is_untouched_when_nothing_needs_carrying(master, tmp_path, capsys):
    sub = make_docx(tmp_path / "sub.docx", MASTER_721.replace("Time intro.", "Changed."), extra_styles=SUB_STYLES)
    merge(master, sub, tmp_path / "out.docx", capsys)
    read = lambda p: zipfile.ZipFile(p).read("word/styles.xml")
    assert read(tmp_path / "out.docx") == read(master)


# --- merge: formatting ------------------------------------------------------------------------

# A §21.7 drafted outside the master: bare headings and text, its own table look, the method
# written as plain paragraphs, no spacer after the table, and a new 21.7.4.
FOREIGN_721 = (H(2, "21.7 Time-of-Use", fmt=False) + P("Time intro, revised.", fmt=False)
               + H(3, "21.7.1 Fields", fmt=False) + TBL(FIELDS + [["IVL", "Interval Bounds", "Start and end"]], fmt=False)
               + P("Derived fields are modeled.", fmt=False)
               + H(3, "21.7.2 Method", fmt=False)
               + BUL(" Savings count only when declared.", "Declared before.", fmt=False)
               + BUL(" Peaks are tracked.", "Demand charges.", fmt=False)
               + H(3, "21.7.4 Matching Rules", fmt=False) + P(" Factors must overlap.", fmt=False, lead="Overlap rule."))


def rpr_tags(r):
    rpr = r.find(W + "rPr")
    return {etree.QName(c).localname: c.get(W + "val") for c in (rpr if rpr is not None else [])}


def test_foreign_unit_takes_the_masters_formatting(master, tmp_path, capsys):
    out = merge(master, make_docx(tmp_path / "sub.docx", FOREIGN_721), tmp_path / "out.docx", capsys)
    assert "§21.7: UPDATED; added 21.7.4; formatting matched to master" in out
    before, after = doc(master), doc(tmp_path / "out.docx")

    # headings: the master's spacing and run formatting for their level
    for start, ref in (("21.7 Time", "21.7 Time"), ("21.7.4", "21.7.1"), ("21.7.2", "21.7.2")):
        new, old = para(after, start), para(before, ref)
        assert c14n(new.find(W + "pPr")) == c14n(old.find(W + "pPr"))
        assert rpr_tags(new.find(W + "r")) == rpr_tags(old.find(W + "r"))

    # body text: the master's paragraph spacing and size
    body = para(after, "Time intro, revised")
    assert c14n(body.find(W + "pPr")) == c14n(para(before, "Time intro.").find(W + "pPr"))
    assert rpr_tags(body.find(W + "r"))["sz"] == "21"

    # the method subsection was bullets in the master, so it stays bullets; run-ins stay bold
    for start in ("Declared before.", "Demand charges."):
        p = para(after, start)
        assert p.find("./w:pPr/w:numPr", NS) is not None
        lead, rest = p.findall(W + "r")
        assert "b" in rpr_tags(lead) and "b" not in rpr_tags(rest)

    # 21.7.4 is a new subsection with no master counterpart: plain body text, bold lead kept
    rule = para(after, "Overlap rule.")
    assert rule.find("./w:pPr/w:numPr", NS) is None
    assert "b" in rpr_tags(rule.findall(W + "r")[0]) and rpr_tags(rule.findall(W + "r")[1]).get("sz") == "21"

    # the table: master tblPr and dark header row, rows and cell text kept, spacer restored after it
    fields = next(t for t in tables(after) if "IVL" in "".join(t.itertext()))
    master_fields = next(t for t in tables(before) if "TOU" in "".join(t.itertext()))
    assert c14n(fields.find(W + "tblPr")) == c14n(master_fields.find(W + "tblPr"))
    head_cell = fields.find(W + "tr").find(W + "tc")
    assert head_cell.find("./w:tcPr/w:shd", NS).get(W + "fill") == MASTER_FILL
    assert rpr_tags(head_cell.find(".//w:r", NS))["color"] == "ffffff"
    assert ["".join(r.itertext()) for r in fields.findall(W + "tr")][-1] == "IVLInterval BoundsStart and end"
    nxt = fields.getnext()
    assert nxt.tag == W + "p" and not sync.ptext(nxt) and sync.ptext(nxt.getnext()) == "Derived fields are modeled."

    # text is never changed
    assert "".join(after.itertext()).count("Factors must overlap.") == 1


def test_keep_formatting_leaves_foreign_content_as_written(master, tmp_path, capsys):
    out = merge(master, make_docx(tmp_path / "sub.docx", FOREIGN_721), tmp_path / "out.docx", capsys, keep_formatting=True)
    assert "formatting matched" not in out
    after = doc(tmp_path / "out.docx")
    assert para(after, "21.7 Time").find("./w:pPr/w:spacing", NS) is None
    fields = next(t for t in tables(after) if "IVL" in "".join(t.itertext()))
    assert fields.find(".//w:shd", NS).get(W + "fill") == FOREIGN_FILL


def test_formatted_unit_keeps_its_formatting_and_only_bare_paragraphs_are_dressed(master, tmp_path, capsys):
    # an extracted-and-edited unit: master formatting throughout, one deliberate change, one bare paragraph
    deliberate = '<w:p><w:pPr><w:spacing w:after="400"/><w:jc w:val="center"/></w:pPr>' + run("Centred on purpose.", sz="28") + "</w:p>"
    body = MASTER_721.replace(P("Time intro."), P("Time intro.") + deliberate + P("A bare new paragraph.", fmt=False))
    out = merge(master, make_docx(tmp_path / "sub.docx", body), tmp_path / "out.docx", capsys)
    assert "§21.7: UPDATED; formatting matched to master" in out
    after = doc(tmp_path / "out.docx")
    kept = para(after, "Centred on purpose.")
    assert kept.find("./w:pPr/w:jc", NS).get(W + "val") == "center" and rpr_tags(kept.find(W + "r"))["sz"] == "28"
    bare = para(after, "A bare new paragraph.")
    assert c14n(bare.find(W + "pPr")) == c14n(para(doc(master), "Time intro.").find(W + "pPr"))
    # the unit's table was not restyled or given a second spacer
    fields = next(t for t in tables(after) if "TOU" in "".join(t.itertext()))
    assert c14n(fields) == c14n(next(t for t in tables(doc(master)) if "TOU" in "".join(t.itertext())))
    assert sync.ptext(fields.getnext().getnext()) == "21.7.2 Method"


def test_new_foreign_section_uses_formatting_from_the_rest_of_the_master(master, tmp_path, capsys):
    sub = make_docx(tmp_path / "sub.docx", H(2, "21.9 Runtime", fmt=False) + P("Accelerator fields.", fmt=False))
    out = merge(master, sub, tmp_path / "out.docx", capsys)
    assert "§21.9: NEW section inserted after §21.8; formatting matched to master" in out
    after, before = doc(tmp_path / "out.docx"), doc(master)
    assert rpr_tags(para(after, "21.9 Runtime").find(W + "r")) == rpr_tags(para(before, "21.8 Routing").find(W + "r"))
    assert c14n(para(after, "Accelerator fields.").find(W + "pPr")) == c14n(para(before, "Routing text.").find(W + "pPr"))


# --- the committed master and sub-document ----------------------------------------------------

MASTER = REPO / "docs" / "stardust-spec-v1.6.docx"
SUB = REPO / "docs" / "stardust-telemetry-spec-T1.5.docx"


def test_committed_sub_document_round_trips_without_changes(tmp_path, capsys):
    out = merge(str(MASTER), str(SUB), tmp_path / "out.docx", capsys, dry=True)
    assert out.count(": unchanged") == len(sync.UNITS)
    assert "WARNING" not in out and "UPDATED" not in out and "NEW" not in out


def test_extract_takes_every_unit_and_round_trips(tmp_path, capsys):
    sub = tmp_path / "sub.docx"
    sync.extract(str(MASTER), str(sub), "T9.9", ["§21.7|A note"])
    out = capsys.readouterr().out
    assert f"Extracted {len(sync.UNITS)} units" in out
    root = doc(sub)
    text = "".join(root.itertext())
    assert "Telemetry Spec T9.9 (Draft)" in text and "A note" in text
    out = merge(str(MASTER), str(sub), tmp_path / "out.docx", capsys, dry=True)
    assert out.count(": unchanged") == len(sync.UNITS)


def test_export_matches_the_committed_schema_files(tmp_path, capsys):
    sync.export(str(MASTER), str(tmp_path))
    for name in ("field-codes.json", "mcp-tools.json"):
        assert json.loads((tmp_path / name).read_text()) == json.loads((REPO / "schema" / name).read_text()), name
    fresh, committed = (json.loads(p.read_text()) for p in (tmp_path / "spec-version.json", REPO / "schema" / "spec-version.json"))
    fresh.pop("generated"); committed.pop("generated")
    assert fresh == committed
