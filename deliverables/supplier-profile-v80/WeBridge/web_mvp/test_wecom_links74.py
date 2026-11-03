import unittest

from rich_content import http_links
from wecom_content import typed_message


def varint(value):
    data = bytearray()
    while value > 127:
        data.append((value & 127) | 128)
        value >>= 7
    return bytes(data) + bytes([value])


def pb(number, value):
    if isinstance(value, int):
        return varint(number << 3) + varint(value)
    value = value.encode() if isinstance(value, str) else value
    return varint((number << 3) | 2) + varint(len(value)) + value


def text_block(value, block_type=0):
    return pb(1, pb(1, block_type) + pb(2, pb(1, value)))


def raw_text_block(body, block_type=0):
    return pb(1, pb(1, block_type) + pb(2, body))


def incomplete_mention_block(name='成员'):
    return pb(1, pb(1, 5) + pb(2, pb(2, name)))


def forward_message(source):
    entry = pb(1, 11) + pb(2, 1700000000) + pb(5, 2) + pb(101, source)
    return pb(1, pb(5, 4) + pb(107, pb(1, entry)))


class WeComLinkGapTests(unittest.TestCase):
    def test_unknown_text_block_keeps_gap_without_guessing_body(self):
        data = (text_block('金额10') +
                raw_text_block(pb(1, 99) + pb(2, pb(1, 'unmapped'))) +
                text_block('0元'))

        parsed = typed_message(data, 2)

        self.assertEqual(parsed['text'], '金额10[文本片段未完整解析]0元')
        self.assertEqual(parsed['parseStatus'], 'unsupported_text_blocks')

    def test_invalid_utf8_text_block_keeps_gap(self):
        parsed = typed_message(text_block('金额10') + text_block(b'\xff') + text_block('0元'), 2)

        self.assertEqual(parsed['text'], '金额10[文本片段未完整解析]0元')
        self.assertEqual(parsed['parseStatus'], 'unsupported_text_blocks')

    def test_link_does_not_cross_invalid_utf8_gap(self):
        data = text_block('https://example.') + text_block(b'\xff') + text_block('com')

        parsed = typed_message(data, 2)

        self.assertEqual(parsed['text'], 'https://example.[文本片段未完整解析]com')
        self.assertEqual(parsed['links'], [])

    def test_complete_link_in_same_message_is_preserved(self):
        data = (text_block('前 https://example.com 后') +
                text_block(b'\xff') + text_block('尾'))

        self.assertEqual(typed_message(data, 2)['links'], ['https://example.com'])

    def test_link_across_normal_text_runs_is_preserved(self):
        data = text_block('https://example.') + text_block('com')

        self.assertEqual(typed_message(data, 2)['links'], ['https://example.com'])

    def test_literal_gap_marker_is_not_treated_as_system_gap(self):
        literal = '[文本片段未完整解析]'
        parsed = typed_message(text_block('https://example.' + literal + 'com'), 2)

        self.assertEqual(parsed['links'], ['https://example.' + literal + 'com'])

    def test_unresolved_mention_placeholder_is_a_link_gap(self):
        data = (text_block('https://example.') + incomplete_mention_block() +
                text_block('com'))

        parsed = typed_message(data, 2)

        self.assertEqual(parsed['text'], 'https://example.成员[提及对象未记录]com')
        self.assertEqual(parsed['links'], [])

    def test_duplicate_link_order_is_preserved(self):
        data = text_block('https://one.example https://two.example https://one.example')

        self.assertEqual(typed_message(data, 2)['links'], [
            'https://one.example', 'https://two.example', 'https://one.example'])

    def test_punctuation_and_nested_url_parentheses_are_trimmed(self):
        text = '参见（https://example.com/path_(v1)）。'

        self.assertEqual(http_links(text), ['https://example.com/path_(v1)'])

    def test_excluded_gap_span_is_checked_after_punctuation_trimming(self):
        url = 'https://example.com'
        text = url + ')。' + '[文本片段未完整解析]'
        gap_start = len(url) + 2

        self.assertEqual(http_links(text, excluded_spans=((gap_start, len(text)),)), [url])

    def test_forward_and_quote_links_keep_nested_gap_boundaries(self):
        source = (text_block('引用 https://example.com') + text_block(' ') +
                  text_block(b'\xff') + text_block(' 后'))
        forwarded = typed_message(forward_message(source), 4)
        nested = forwarded['record']['items'][0]['record']['items'][0]
        self.assertEqual(nested['links'], ['https://example.com'])
        self.assertEqual(nested['parseStatus'], 'unsupported_text_blocks')

        quoted = typed_message(source, 2, extra=pb(1002, pb(1,
            pb(1, 11) + pb(2, 1700000000) + pb(5, 2) + pb(101, source))))
        self.assertEqual(quoted['quote']['message']['links'], ['https://example.com'])
        self.assertEqual(quoted['quote']['message']['parseStatus'], 'unsupported_text_blocks')


if __name__ == '__main__':
    unittest.main()
