"""Unit tests for the pure parts of migrate.py (no network)."""
import json

import migrate as m

FOLDER = m.FOLDER
DOC = "application/vnd.google-apps.document"
FORM = "application/vnd.google-apps.form"


def item(id, name, mime="application/pdf", parents=("root",), **kw):
    d = {"id": id, "name": name, "mimeType": mime, "parents": list(parents), "ownedByMe": True, "size": "10"}
    d.update(kw)
    return d


def test_sanitize_strips_separators_and_controls():
    assert m.sanitize("a/b\\c:d") == "a_b_c_d"
    assert m.sanitize(" x\x00y. ") == "x_y"
    assert m.sanitize("") == "_unnamed"
    long = "a" * 300 + ".pdf"
    assert len(m.sanitize(long)) == 200 and m.sanitize(long).endswith(".pdf")


def test_dedupe_keeps_first_and_numbers_the_rest():
    assert m.dedupe(["test1", "test1", "test2.csv", "test2.csv", "TEST1"]) == \
        ["test1", "test1 (2)", "test2.csv", "test2 (2).csv", "TEST1 (3)"]


def test_local_name_adds_export_extension_once():
    assert m.local_name({"name": "Menu", "mimeType": DOC}) == "Menu.docx"
    assert m.local_name({"name": "Menu.docx", "mimeType": DOC}) == "Menu.docx"
    assert m.local_name({"name": "Menu.pdf", "mimeType": "application/pdf"}) == "Menu.pdf"


def test_build_tree_paths_kinds_and_skips():
    items = [
        item("f1", "YumYums", FOLDER),
        item("f2", "Operations", FOLDER, parents=("f1",)),
        item("d1", "Payroll", DOC, parents=("f2",)),
        item("p1", "EIN.pdf", parents=("f1",), md5Checksum="abc"),
        item("p2", "EIN.pdf", parents=("f1",)),
        item("form", "Tips", FORM),
        item("sc", "Link", m.SHORTCUT, shortcutDetails={"targetId": "p1"}),
        item("sh", "Shared.pdf", parents=(), ownedByMe=False),
        item("orph", "Lost.pdf", parents=("gone",)),
    ]
    nodes, skipped = m.build_tree(items, "root")
    paths = {n["id"]: n["path"] for n in nodes.values()}
    assert paths["f2"] == "YumYums/Operations"
    assert paths["d1"] == "YumYums/Operations/Payroll.docx"
    assert nodes["d1"]["kind"] == "export"
    assert {paths["p1"], paths["p2"]} == {"YumYums/EIN.pdf", "YumYums/EIN (2).pdf"}
    assert paths["orph"] == "_Orphaned (no parent folder)/Lost.pdf"
    reasons = {it["id"]: why for it, why in skipped}
    assert "Form" in reasons["form"]
    assert "shortcut" in reasons["sc"]
    assert "shared with me" in reasons["sh"]
    assert "sh" not in nodes

    nodes, skipped = m.build_tree(items, "root", include_shared=True)
    assert nodes["sh"]["path"] == "_Shared with me/Shared.pdf"
    assert all(it["id"] != "sh" for it, _ in skipped)


def test_build_tree_is_json_serialisable_and_deterministic():
    items = [item("b", "b.pdf"), item("a", "a.pdf"), item("c", "a.pdf")]
    n1, _ = m.build_tree(items, "root")
    n2, _ = m.build_tree(list(reversed(items)), "root")
    assert json.dumps(n1, sort_keys=True) == json.dumps(n2, sort_keys=True)


def test_upload_route_thresholds():
    assert m.upload_route(5 * m.MB) == "simple"
    assert m.upload_route(250 * m.MB) == "simple"
    assert m.upload_route(250 * m.MB + 1) == "stream"
    assert m.upload_route(1024 * m.MB) == "stream"
    assert m.upload_route(1024 * m.MB + 1) == "chunk"


def test_find_key_walks_nested_json_api_shapes():
    d = {"data": [{"attributes": {"resource_id": "r1", "File INFO": {"RESOURCE_ID": "r1"}}}]}
    assert m.find_key(d, "resource_id", "RESOURCE_ID") == "r1"
    assert m.find_key({"data": {"id": "x"}}, "resource_id", "id") == "x"
    assert m.find_key({"a": []}, "nope") is None


def test_human_sizes():
    assert m.human(0) == "0 B"
    assert m.human(1536) == "1.5 KB"
    assert m.human(8665 * m.MB) == "8.5 GB"
