import hashlib
import io
from pathlib import Path
import tempfile
import unittest

from PIL import Image

from wecom_media import WeComMedia


class WeComEmotionMediaTests(unittest.TestCase):
    def test_md5_fallback_matches_opaque_cache_and_emotion_files(self):
        with tempfile.TemporaryDirectory() as folder:
            base = Path(folder)
            source = base / 'account' / 'Data'
            source.mkdir(parents=True)
            image = base / 'account' / 'Cache' / 'opaque-image-id'
            attachment = base / 'account' / 'Emotion' / 'opaque-file-id'
            image.parent.mkdir()
            attachment.parent.mkdir()
            image_data = bytearray()
            with io.BytesIO() as stream:
                Image.new('RGB', (3, 2), '#335577').save(stream, format='PNG')
                image_data.extend(stream.getvalue())
            image.write_bytes(image_data)
            file_data = b'approved synthetic attachment in Emotion'
            attachment.write_bytes(file_data)
            media = WeComMedia({'sourceRoot': str(source)})

            image_destination = base / 'image-export'
            file_destination = base / 'file-export'
            image_destination.mkdir()
            file_destination.mkdir()
            image_result = media.attach({
                'kind': 'image',
                'media': {'filename': 'shown.png', 'size': len(image_data) + 9,
                          'md5': hashlib.md5(image_data).hexdigest()},
            }, image_destination)
            file_result = media.attach({
                'kind': 'file',
                'media': {'filename': 'report.txt', 'size': len(file_data) + 9,
                          'md5': hashlib.md5(file_data).hexdigest()},
            }, file_destination)

            self.assertEqual(image_result['status'], 'ready')
            self.assertEqual(image_result['sourceRelativePath'], 'Cache/opaque-image-id')
            self.assertEqual(file_result['status'], 'ready')
            self.assertEqual(file_result['sourceRelativePath'], 'Emotion/opaque-file-id')

    def test_wrong_md5_does_not_match_cache_or_emotion_content(self):
        with tempfile.TemporaryDirectory() as folder:
            base = Path(folder)
            source = base / 'account' / 'Data'
            source.mkdir(parents=True)
            cache = base / 'account' / 'Cache'
            emotion = base / 'account' / 'Emotion'
            cache.mkdir()
            emotion.mkdir()
            cache_file = cache / 'opaque-cache-id'
            emotion_file = emotion / 'opaque-emotion-id'
            cache_file.write_bytes(b'cache bytes')
            emotion_file.write_bytes(b'emotion bytes')
            media = WeComMedia({'sourceRoot': str(source)})
            wrong = '0' * 32

            for filename, size, destination in (
                    ('cache.txt', 1, base / 'cache-export'),
                    ('emotion.txt', 1, base / 'emotion-export')):
                destination.mkdir()
                result = media.attach({
                    'kind': 'file',
                    'media': {'filename': filename, 'size': size, 'md5': wrong},
                }, destination)
                self.assertEqual(result['status'], 'unavailable')


if __name__ == '__main__':
    unittest.main()
