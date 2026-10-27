"""Forwarded link fields must survive parsing, reading material and search."""
import unittest

from offline_analysis import _body, content
from offline_chat import _message_html, business_text
from offline_content import interpret
from offline_markdown import message as markdown_message
from private_insights import search_messages


def wechat_forward_xml():
    """Reusable WeChat appmsg fixture for all four export entry points."""
    nested = (
        '<recordinfo><title>Nested forward</title><desc>NESTED_FORWARD_DESCRIPTION</desc>'
        '<datalist><dataitem datatype="5"><sourcename>Nested sender</sourcename>'
        '<datatitle>NESTED_LINK_TITLE</datatitle><datadesc>Nested link description</datadesc>'
        '<weburl>https://example.test/nested</weburl></dataitem></datalist></recordinfo>'
    )
    record = (
        '<recordinfo><title>Outer forward</title><desc>FORWARD_DESCRIPTION_NEEDLE</desc><datalist>'
        '<dataitem datatype="5"><sourcename>Link sender</sourcename>'
        '<datatitle>LINK_TITLE_NEEDLE</datatitle><datadesc>LINK_DESCRIPTION_NEEDLE</datadesc>'
        '<weburl>https://example.test/path</weburl></dataitem>'
        '<dataitem datatype="17"><sourcename>Nested sender</sourcename>'
        '<datatitle>Nested record</datatitle><recordxml>' + nested + '</recordxml>'
        '</dataitem></datalist></recordinfo>'
    )
    return '<msg><appmsg><type>19</type><title>Outer appmsg</title><recorditem><![CDATA[' + record + ']]></recorditem></appmsg></msg>'


def _source(parsed):
    return {"analysisRefs": {"forward-1": 1}, "messages": [{
        **parsed, "id": "forward-1", "time": "2026-10-25T09:00:00+08:00",
        "senderName": "Current sender", "direction": "incoming",
    }]}


class ForwardSemantics58Tests(unittest.TestCase):
    def test_parser_keeps_link_title_and_description_independently(self):
        parsed = interpret(wechat_forward_xml(), (19 << 32) | 49)
        child = parsed["record"]["items"][0]
        self.assertEqual(child["title"], "LINK_TITLE_NEEDLE")
        self.assertEqual(child["description"], "LINK_DESCRIPTION_NEEDLE")
        self.assertEqual(parsed["record"]["description"], "FORWARD_DESCRIPTION_NEEDLE")
        self.assertEqual(parsed["record"]["items"][1]["record"]["description"],
                         "NESTED_FORWARD_DESCRIPTION")

    def test_reader_body_and_search_retain_nested_forward_material(self):
        parsed = interpret(wechat_forward_xml(), (19 << 32) | 49)
        reader = content(parsed)
        self.assertEqual(reader["forward"]["description"], "FORWARD_DESCRIPTION_NEEDLE")
        self.assertEqual(reader["forward"]["items"][0]["title"], "LINK_TITLE_NEEDLE")
        self.assertEqual(reader["forward"]["items"][0]["description"], "LINK_DESCRIPTION_NEEDLE")
        body = "\n".join(_body(reader))
        self.assertEqual(body.count("LINK_TITLE_NEEDLE"), 1)
        self.assertEqual(body.count("LINK_DESCRIPTION_NEEDLE"), 1)
        self.assertIn("FORWARD_DESCRIPTION_NEEDLE", body)
        self.assertIn("NESTED_FORWARD_DESCRIPTION", body)

        source = _source(parsed)
        for query, source_label in (
                ("LINK_TITLE_NEEDLE", "标题"),
                ("LINK_DESCRIPTION_NEEDLE", "正文"),
                ("FORWARD_DESCRIPTION_NEEDLE", "转发说明"),
                ("NESTED_FORWARD_DESCRIPTION", "转发说明"),
                ("example.test/path", "链接")):
            with self.subTest(query=query):
                result = search_messages(source, query)
                self.assertEqual(result["total"], 1)
                self.assertIn(source_label, result["items"][0]["matchSource"])

    def test_old_forward_text_only_rows_remain_searchable(self):
        source = {"analysisRefs": {"old-1": 2}, "messages": [{
            "id": "old-1", "kind": "record", "text": "old outer text",
            "record": {"items": [{"kind": "link", "text": "LEGACY_TEXT_NEEDLE"}]},
        }]}
        result = search_messages(source, "LEGACY_TEXT_NEEDLE")
        self.assertEqual(result["total"], 1)
        self.assertEqual(result["items"][0]["matchSource"], "转发第 1 条 · 正文")

    def test_three_reading_exports_keep_the_same_forward_fields(self):
        row = interpret(wechat_forward_xml(), (19 << 32) | 49)
        row.update(id="forward-1", time="2026-10-25T09:00:00+08:00", senderName="Current sender",
                   senderId="current", direction="incoming", isSelf=False, provenance={})
        business = business_text(row)
        markdown = markdown_message(row, 1)
        html = _message_html(row)
        for rendered in (business, markdown, html):
            with self.subTest(rendered=type(rendered).__name__):
                self.assertIn("LINK_TITLE_NEEDLE", rendered)
                self.assertIn("LINK_DESCRIPTION_NEEDLE", rendered)
                self.assertIn("FORWARD_DESCRIPTION_NEEDLE", rendered)
                self.assertIn("转发标题：Outer forward", rendered)
                self.assertIn("转发标题：Nested forward", rendered)
                self.assertEqual(rendered.count("LINK_TITLE_NEEDLE"), 1)

    def test_equal_record_and_node_description_is_rendered_once(self):
        row = interpret(wechat_forward_xml(), (19 << 32) | 49)
        row["description"] = row["record"]["description"]
        outputs = (business_text(row), markdown_message({**row, "id": "same-description"}, 1),
                   _message_html(row))
        for rendered in outputs:
            with self.subTest(rendered=type(rendered).__name__):
                self.assertEqual(rendered.count("FORWARD_DESCRIPTION_NEEDLE"), 1)

    def test_equal_link_title_and_description_are_rendered_once(self):
        parsed = interpret(wechat_forward_xml().replace(
            "<datadesc>LINK_DESCRIPTION_NEEDLE</datadesc>",
            "<datadesc>LINK_TITLE_NEEDLE</datadesc>"), (19 << 32) | 49)
        reader = content(parsed)
        for rendered in (
                "\n".join(_body(reader)), business_text(parsed),
                markdown_message({**parsed, "id": "equal-1"}, 1), _message_html(parsed)):
            with self.subTest(rendered=type(rendered).__name__):
                self.assertEqual(rendered.count("LINK_TITLE_NEEDLE"), 1)

    def test_multiline_equal_fields_and_existing_kind_descriptions_survive(self):
        equal = {"kind": "link", "title": "同一行\n第二行", "text": "同一行\n第二行",
                 "description": "同一行\n第二行"}
        rendered = "\n".join(_body(equal))
        self.assertEqual(rendered.count("同一行"), 1)
        self.assertEqual(rendered.count("第二行"), 1)
        for kind in ("file", "quote", "revoke", "unknown"):
            with self.subTest(kind=kind):
                value = {"kind": kind, "text": "legacy text", "description": kind + " description",
                         "localType": 999}
                self.assertIn(kind + " description", "\n".join(_body(value)))
                self.assertIn(kind + " description", business_text(value))
                self.assertIn(kind + " description", markdown_message({**value, "id": kind}, 1))


if __name__ == "__main__":
    unittest.main()
