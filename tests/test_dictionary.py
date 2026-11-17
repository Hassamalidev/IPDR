import io
import json

import pytest

from ipdr import dictionary, store
from ipdr.dictionary import DictionaryError, parse_upload


def names(fields):
    return [f["name"] for f in fields]


def test_csv_with_header_aliases():
    raw = b"Column Name,Meaning,Data Type\napp_label,Application name,String\nduration,Flow length in ms,UInt64\n"
    fields, notes = parse_upload("d.csv", raw)
    assert names(fields) == ["app_label", "duration"]
    assert fields[1]["description"] == "Flow length in ms" and fields[1]["type"] == "UInt64"
    assert notes == ""


def test_csv_semicolon_and_bom():
    raw = "﻿field;description\nmnc;Mobile network code\n".encode("utf-8")
    fields, _ = parse_upload("d.csv", raw)
    assert fields == [{"name": "mnc", "description": "Mobile network code", "type": "", "example": "", "values": "", "notes": ""}]


def test_csv_without_header():
    fields, _ = parse_upload("d.csv", b"app_label,Application name\ncid,Cell id\n")
    assert names(fields) == ["app_label", "cid"]


def test_csv_missing_name_column():
    with pytest.raises(DictionaryError, match="field names"):
        parse_upload("d.csv", b"foo,bar,baz\n1,2,3\n")


def test_excel():
    openpyxl = pytest.importorskip("openpyxl")
    wb = openpyxl.Workbook()
    ws = wb.active
    ws.append(["Field", "Description", "Values"])
    ws.append(["block_type", "Blocking reason", "0 = none, 1 = policy"])
    buf = io.BytesIO()
    wb.save(buf)
    fields, _ = parse_upload("dict.xlsx", buf.getvalue())
    assert fields[0]["name"] == "block_type" and fields[0]["values"] == "0 = none, 1 = policy"


@pytest.mark.parametrize("payload", [
    [{"field": "cid", "description": "Cell id"}],
    {"cid": "Cell id"},
    {"fields": [{"name": "cid", "desc": "Cell id"}]},
    {"columns": {"cid": {"description": "Cell id"}}},
])
def test_json_shapes(payload):
    fields, _ = parse_upload("d.json", json.dumps(payload).encode())
    assert fields[0]["name"] == "cid" and fields[0]["description"] == "Cell id"


def test_yaml_with_extra_sections_become_notes():
    raw = b"fields:\n  - name: cid\n    description: Cell id\nrules:\n  - Always group by cid\n"
    fields, notes = parse_upload("d.yaml", raw)
    assert names(fields) == ["cid"] and "Always group by cid" in notes


def test_markdown_table():
    raw = b"# Guide\n\n| Field | Description |\n|---|---|\n| app_label | App name |\n| loc | Address |\n\nSome rules here.\n"
    fields, notes = parse_upload("guide.md", raw)
    assert names(fields) == ["app_label", "loc"] and "Some rules here." in notes


def test_plain_text_lines_for_known_columns():
    fields, notes = parse_upload("notes.txt", b"- app_label: the app\n- not_a_column: ignored\nGeneral advice.\n")
    assert names(fields) == ["app_label"] and "General advice." in notes


def test_unsupported_extension():
    with pytest.raises(DictionaryError, match="CSV"):
        parse_upload("d.pdf", b"%PDF")


def test_upload_overrides_and_is_used_in_prompt():
    d = dictionary.save_upload("guide.csv", b"field,description\nblock_type,Why the flow was blocked (TEST-MARKER)\nnot_real,x\n")
    try:
        assert d["report"]["matched"] == 1 and d["report"]["unknown"] == ["not_real"]
        eff = {c["name"]: c for c in dictionary.effective()["columns"]}
        assert eff["block_type"]["description"].endswith("(TEST-MARKER)")
        assert eff["block_type"]["source"] == "guide"
        assert "TEST-MARKER" in dictionary.render_for_prompt()
        store.set_dictionary_active(d["id"], False)
        assert "TEST-MARKER" not in dictionary.render_for_prompt()
    finally:
        store.delete_dictionary(d["id"])


def test_prompt_marks_empty_columns_and_is_stable():
    a = dictionary.render_for_prompt()
    assert "user_agent" in a and "EMPTY" in a
    assert a == dictionary.render_for_prompt()


def test_template_lists_every_column():
    text = dictionary.template_csv()
    assert text.splitlines()[0] == "field,type,description,example,values,notes"
    assert "app_label" in text and "subscriber_id" in text
