import unittest

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


def mention_block(member=11, name='成员'):
    return pb(1, pb(1, 5) + pb(2, pb(1, member) + pb(2, name)))


class WeComTextGapTests(unittest.TestCase):
    def test_malformed_known_text_fragment_keeps_gap_in_order(self):
        block = lambda body: pb(1, pb(1, 0) + pb(2, pb(1, body)))
        data = block('金额10') + block(b'\xff') + block('0元')

        parsed = typed_message(data, 2)

        self.assertEqual(parsed['text'], '金额10[文本片段未完整解析]0元')
        self.assertEqual(parsed['parseStatus'], 'unsupported_text_blocks')
        self.assertEqual(parsed['links'], [])

    def test_type_zero_and_two_keep_text_links_mentions_and_newline_order(self):
        data = (text_block('前 https://example.invalid ') + mention_block() +
                text_block(b'\xff') + text_block('后\n😀'))

        for kind in (0, 2):
            with self.subTest(kind=kind):
                parsed = typed_message(data, kind)
                self.assertEqual(parsed['text'], '前 https://example.invalid @成员[文本片段未完整解析]后\n😀')
                self.assertEqual(parsed['links'], ['https://example.invalid'])
                self.assertEqual(parsed['mentions']['ids'], ['11'])
                self.assertEqual(parsed['parseStatus'], 'unsupported_text_blocks')

    def test_empty_text_body_does_not_create_a_gap(self):
        parsed = typed_message(text_block(b'') + text_block('有效'), 2)

        self.assertEqual(parsed['text'], '有效')
        self.assertEqual(parsed['parseStatus'], 'ok')

    def test_known_text_block_type_three_keeps_an_unreadable_gap(self):
        parsed = typed_message(text_block(b'\xff', block_type=3), 2)

        self.assertEqual(parsed['text'], '[文本片段未完整解析]')
        self.assertEqual(parsed['parseStatus'], 'unsupported_text_blocks')

    def test_multiple_bad_fragments_and_malformed_body_keep_each_gap(self):
        data = (text_block('左') + raw_text_block(pb(1, b'\xff') + pb(1, b'\xfe')) +
                raw_text_block(b'\xff') + text_block('右'))

        parsed = typed_message(data, 2)

        self.assertEqual(parsed['text'], '左[文本片段未完整解析][文本片段未完整解析][文本片段未完整解析]右')
        self.assertEqual(parsed['parseStatus'], 'unsupported_text_blocks')

    def test_gap_survives_nested_forward_and_quote(self):
        source = text_block('引用前') + raw_text_block(b'\xff') + text_block('引用后')
        entry = pb(1, 11) + pb(2, 1700000000) + pb(5, 2) + pb(101, source)
        forward = pb(1, pb(5, 4) + pb(107, pb(1, entry)))

        forwarded = typed_message(forward, 4)

        nested = forwarded['record']['items'][0]['record']['items'][0]
        self.assertEqual(nested['text'], '引用前[文本片段未完整解析]引用后')
        self.assertEqual(nested['parseStatus'], 'unsupported_text_blocks')

        extra = pb(1002, pb(1, entry))
        quoted = typed_message(source, 2, extra=extra)

        self.assertEqual(quoted['quote']['message']['text'], '引用前[文本片段未完整解析]引用后')
        self.assertEqual(quoted['quote']['message']['parseStatus'], 'unsupported_text_blocks')
        self.assertEqual(quoted['quote']['resolutionStatus'], 'embedded_partial')


if __name__ == '__main__':
    unittest.main()
