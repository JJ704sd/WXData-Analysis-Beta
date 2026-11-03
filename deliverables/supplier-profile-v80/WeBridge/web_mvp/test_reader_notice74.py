"""Per-message notices keep incomplete text visible in TXT and Markdown readers."""
import unittest

from offline_chat import business_text
from offline_content_status import message_parse_notice
from offline_markdown import message as markdown_message


NOTICE = '解析提醒：这条消息含有未完整解析的内容，请结合原记录核对。'


def row(**overrides):
    value = {
        'id': 'message-1',
        'kind': 'text',
        'text': '已保留的文字',
        'senderName': '发送者',
        'senderId': 'sender-1',
        'time': '2026-10-28T09:00:00+08:00',
    }
    value.update(overrides)
    return value


class ReaderNotice74Tests(unittest.TestCase):
    def test_parse_notice_reuses_message_gap_codes(self):
        self.assertEqual(message_parse_notice(row(parseStatus='unsupported_text_blocks')), NOTICE)
        self.assertEqual(message_parse_notice(row(decodeStatus='failed')), NOTICE)
        self.assertEqual(message_parse_notice(row(decodeError='invalid utf-8')), NOTICE)
        self.assertEqual(message_parse_notice(row(parseStatus='unsupported_text_blocks', decodeStatus='failed')), NOTICE)

    def test_normal_and_attachment_only_gaps_do_not_get_text_notice(self):
        self.assertEqual(message_parse_notice(row()), '')
        self.assertEqual(message_parse_notice(row(kind='file', text='附件说明',
                                                   attachmentContent={'extractionStatus': 'partial',
                                                                      'fileStatus': 'ready'})), '')
        self.assertEqual(message_parse_notice(row(kind='image', text='图片说明',
                                                   asset={'status': 'missing'})), '')

    def test_parse_notice_stays_near_known_text_in_both_readers(self):
        value = row(text='前后仍可读', parseStatus='unsupported_text_blocks')
        business = business_text(value)
        markdown = markdown_message(value, 1)
        for rendered in (business, markdown):
            with self.subTest(rendered=type(rendered).__name__):
                self.assertIn('前后仍可读', rendered)
                self.assertIn(NOTICE, rendered)
                self.assertEqual(rendered.count(NOTICE), 1)
                self.assertLess(rendered.index('前后仍可读'), rendered.index(NOTICE))

    def test_nested_forward_and_quote_each_get_one_notice(self):
        value = row(
            id='outer', text='外层文字',
            record={'items': [row(id='forwarded', text='转发文字', parseStatus='unsupported_text_blocks')]},
            quote={'senderName': '引用者', 'message': row(
                id='quoted', text='引用文字', decodeStatus='failed')},
        )
        business = business_text(value)
        markdown = markdown_message(value, 1)
        for rendered in (business, markdown):
            with self.subTest(rendered=type(rendered).__name__):
                self.assertEqual(rendered.count(NOTICE), 2)
                positions = [index for index in range(len(rendered))
                             if rendered.startswith(NOTICE, index)]
                for text in ('转发文字', '引用文字'):
                    text_position = rendered.index(text)
                    self.assertTrue(any(position > text_position for position in positions))


if __name__ == '__main__':
    unittest.main()
