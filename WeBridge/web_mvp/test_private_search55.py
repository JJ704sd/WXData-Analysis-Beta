"""Synthetic coverage for private-chat search across reviewed message content."""
import unittest

try:
    from private_insights import search_messages
    from private_insights_ui import _evidence_text
except ModuleNotFoundError:  # ``python -m unittest web_mvp.test_private_search55``
    from web_mvp.private_insights import search_messages
    from web_mvp.private_insights_ui import _evidence_text


def _source():
    return {
        "analysisRefs": {"root-1": 11, "root-2": 12, "root-3": 13},
        "messages": [
            {
                "id": "root-1", "time": "2026-10-25T09:00:00+08:00",
                "senderName": "本人", "direction": "outgoing", "kind": "card",
                "text": "卡片标题", "description": "卡片描述命中",
                "links": ["https://example.test/链接命中"],
                "miniProgram": {"appId": "mini-test", "username": "mini-user", "pagePath": "pages/booking?id=42"},
                "attachmentContent": {
                    "extractionStatus": "extracted", "text": "附件正文命中",
                    "requiresReview": False,
                },
                "record": {"items": [{
                    "senderName": "第三方乙", "direction": "incoming", "kind": "text",
                    "text": "转发正文命中", "metadata": {"secret": "不应命中"},
                }]},
                "quote": {"senderName": "引用人丙", "senderId": "quote-3", "message": {
                    "direction": "incoming", "kind": "text", "text": "引用正文命中",
                }},
                "rawColumns": {"secret": "密钥不应命中"},
            },
            {"id": "root-2", "time": "2026-10-25T10:00:00+08:00",
             "senderName": "乙", "direction": "incoming", "kind": "text", "text": "普通消息"},
            {"id": "root-3", "time": "2026-10-25T11:00:00+08:00",
             "senderName": "丙", "direction": "incoming", "kind": "text", "text": "普通消息"},
        ],
    }


class PrivateSearch55Tests(unittest.TestCase):
    def test_searches_attachment_nested_content_card_description_and_link_once_per_root(self):
        source = _source()
        expected_sources = {
            "附件正文命中": "附件正文",
            "转发正文命中": "转发第 1 条",
            "引用正文命中": "引用来源",
            "卡片描述命中": "卡片描述",
            "链接命中": "链接",
            "booking": "小程序信息",
        }
        for query in expected_sources:
            with self.subTest(query=query):
                result = search_messages(source, query)
                self.assertEqual(result["total"], 1)
                item = result["items"][0]
                self.assertEqual((item["messageId"], item["ref"], item["direction"]),
                                 ("root-1", 11, "outgoing"))
                self.assertEqual(item["text"], "卡片标题")
                self.assertIn(query, item["snippet"])
                self.assertIn(expected_sources[query], item["matchSource"])

        self.assertEqual(search_messages(source, "密钥不应命中")["total"], 0)
        quote_item = search_messages(source, "引用正文命中")["items"][0]
        self.assertIn("引用人丙", quote_item["matchSource"])

    def test_nested_match_preserves_third_party_attribution_and_ocr_review(self):
        source = _source()
        source["messages"][0]["record"]["items"][0]["attachmentContent"] = {
            "extractionStatus": "ocr_extracted", "text": "第三方 OCR 命中", "requiresReview": True,
        }
        result = search_messages(source, "OCR 命中")
        item = result["items"][0]
        self.assertEqual(item["senderName"], "本人")
        self.assertIn("第三方乙", item["matchSource"])
        self.assertTrue(item["matchRequiresReview"])
        evidence = _evidence_text(item)
        self.assertIn("命中来源：", evidence)
        self.assertIn("命中片段：", evidence)
        self.assertIn("OCR文字待核对", evidence)

    def test_paging_counts_roots_once_when_multiple_nested_fields_match(self):
        source = _source()
        source["messages"][1]["attachmentContent"] = {
            "extractionStatus": "partial", "text": "分页命中", "requiresReview": False,
        }
        source["messages"][2]["record"] = {"items": [{"text": "分页命中", "senderName": "第三方丁"}]}
        result = search_messages(source, "命中", offset=1, limit=1)
        self.assertEqual(result["total"], 3)
        self.assertEqual(len(result["items"]), 1)
        self.assertEqual(result["items"][0]["messageId"], "root-2")


if __name__ == "__main__":
    unittest.main()
