"""Select supplied local image keys by content, independent of old paths.

The key file is input only. Account directory names and stale sourceRoot values
are ordering hints, never evidence that decrypted bytes belong to a message.
"""
from io import BytesIO
import hashlib
import json
from pathlib import Path
import re

MAX_KEY_FILE_BYTES = 65536
MAX_KEY_ROWS = 128
MAX_IMAGE_PIXELS = 40_000_000


class ImageKeys:
    def __init__(self, config):
        self.path = Path(config.get('imageKeyFile') or config.get('keyFile') or '')
        self.source = Path(config.get('sourceRoot') or '').resolve()
        if (self.source / 'db_storage').is_dir():
            self.source = (self.source / 'db_storage').resolve()
        self.keys = None

    def _load(self):
        if self.keys is not None:
            return self.keys
        try:
            if not self.path.is_file() or self.path.stat().st_size > MAX_KEY_FILE_BYTES:
                raise ValueError
            with self.path.open('rb') as stream:
                data = stream.read(MAX_KEY_FILE_BYTES + 1)
            if len(data) > MAX_KEY_FILE_BYTES:
                raise ValueError
            value = json.loads(data.decode('utf-8-sig'))
            if not isinstance(value, dict):
                raise ValueError
            rows = value.get('imageKeys', value.get('keys'))
            if not isinstance(rows, list) or not 0 < len(rows) <= MAX_KEY_ROWS:
                raise ValueError
            preferred, remaining = [], []
            for row in rows:
                if not isinstance(row, dict) or not isinstance(row.get('aes_hex'), str):
                    continue
                if not re.fullmatch(r'[a-fA-F0-9]{32}', row['aes_hex']):
                    continue
                hint = row.get('sourceRoot')
                same_path = False
                if isinstance(hint, str) and hint:
                    try:
                        same_path = Path(hint).resolve() == self.source
                    except (OSError, ValueError):
                        pass
                (preferred if same_path else remaining).append(bytes.fromhex(row['aes_hex']))
            self.keys = list(dict.fromkeys(preferred + remaining))
            if not self.keys:
                raise ValueError
        except (OSError, ValueError, KeyError, TypeError):
            self.keys = []
            raise ValueError('需要有效的已有图片密钥文件；支持 keys 数组中的 aes_hex，最多128项。') from None
        return self.keys

    @staticmethod
    def _valid_image(data):
        # Thumbnails lack the original's full digest. Validate actual image
        # pixels instead of accepting successful AES padding or a magic header.
        from PIL import Image
        try:
            with Image.open(BytesIO(data)) as image:
                if image.format not in ('PNG', 'JPEG', 'GIF', 'WEBP'):
                    return False
                if not 0 < image.width * image.height <= MAX_IMAGE_PIXELS:
                    return False
                image.load()
            return True
        except (OSError, ValueError, SyntaxError, Image.DecompressionBombError):
            return False

    def decode(self, data, expected_md5=None):
        """Return validated bytes, not a key; never modify the key file."""
        from media_container import dat_decode
        expected = expected_md5.lower() if isinstance(expected_md5, str) else None
        if expected and not re.fullmatch(r'[a-f0-9]{32}', expected):
            raise ValueError('图片完整校验值无效。')
        for key in self._load():
            try:
                decoded = dat_decode(data, key)
            except (ValueError, OSError):
                continue
            if expected:
                if hashlib.md5(decoded).hexdigest() != expected:
                    continue
            elif not self._valid_image(decoded):
                continue
            # A validated key may be tried first for later files, but every
            # subsequent file must independently pass the same content proof.
            self.keys.remove(key)
            self.keys.insert(0, key)
            return decoded
        raise ValueError('提供的图片密钥均未通过当前媒体的解码或内容校验；未返回未验证图片。')
