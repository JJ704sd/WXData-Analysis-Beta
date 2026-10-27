"""Stream one already-scoped original into an export, checking its message digest."""
import hashlib
import mimetypes
import os
from pathlib import Path
import re
import tempfile


def stamp(path):
    value=path.stat()
    return value.st_size,value.st_mtime_ns,value.st_ctime_ns,value.st_ino


def copy_original(path,destination,expected,filename,kind,progress=None):
    if not re.fullmatch('[a-fA-F0-9]{32}',expected or ''):return None
    before=stamp(path)
    with path.open('rb') as source:
        if hashlib.file_digest(source,'md5').hexdigest()!=expected.lower():return None
    assets=destination/'assets';assets.mkdir(exist_ok=True)
    fd,name=tempfile.mkstemp(prefix='.media-',dir=assets);temporary=Path(name)
    md5,sha=hashlib.md5(),hashlib.sha256();size=0
    try:
        with os.fdopen(fd,'wb') as output,path.open('rb') as source:
            for chunk in iter(lambda:source.read(1024*1024),b''):
                md5.update(chunk);sha.update(chunk);output.write(chunk);size+=len(chunk)
                if progress:progress({'bytesCopied':size})
        if md5.hexdigest()!=expected.lower() or stamp(path)!=before or size!=before[0]:return None
        suffix=Path(filename).suffix.lower()
        mime=mimetypes.guess_type(filename)[0] or 'application/octet-stream'
        if kind=='image':
            from PIL import Image
            try:
                with Image.open(temporary) as image:
                    fmt=image.format;image.verify()
                    suffix='.'+('jpg' if fmt=='JPEG' else fmt.lower());mime='image/'+('jpeg' if fmt=='JPEG' else fmt.lower())
            except Image.DecompressionBombError as error:raise ValueError('image_pixel_limit') from error
        elif kind=='video':
            from media_container import media_type
            with temporary.open('rb') as stream:detected,extension=media_type(stream.read(64))
            if detected=='video/mp4':
                mime=detected;suffix='.'+extension
                if not Path(filename).suffix:filename+='.'+extension
        if not re.fullmatch(r'\.[a-z0-9]{1,12}',suffix):suffix='.bin'
        relative='assets/'+sha.hexdigest()+suffix
        os.replace(temporary,destination/relative)
        return {'status':'ready','path':relative,'filename':filename,'size':size,'digest':sha.hexdigest(),
                'mime':mime,'kind':kind,'previewOnly':False,'matchBasis':'content_md5','reason':''}
    finally:temporary.unlink(missing_ok=True)
