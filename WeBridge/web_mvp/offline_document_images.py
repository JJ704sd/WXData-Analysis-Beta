"""OCR only images referenced by active Office parts; never fetch external links."""
from pathlib import Path
import posixpath
import tempfile
import xml.etree.ElementTree as ET
import zipfile


def extract_embedded_images(archive,parts,out,extra,enabled,*,read_xml=None):
    from offline_documents import _xml,_target,_ocr_into,ContentIssue,MAX_PAGES,IMAGE_FORMATS,detect_format,R
    if read_xml is None:read_xml=_xml
    pending=list(parts);visited=set();images={}
    names=set(archive.namelist())
    while pending:
        name,label=pending.pop(0)
        if name in visited:continue
        visited.add(name)
        relname=posixpath.dirname(name)+'/_rels/'+posixpath.basename(name)+'.rels'
        if relname not in names:continue
        try:
            part=read_xml(archive,name)
            ids={value for node in part.iter() for key,value in node.attrib.items()
                 if key in (R+'embed',R+'link',R+'id') or key=='{urn:schemas-microsoft-com:office:office}relid'}
            for rel in read_xml(archive,relname):
                if rel.get('Id') not in ids:continue
                kind=rel.get('Type','').rsplit('/',1)[-1]
                if kind not in ('image','drawing','header','footer','notesSlide'):continue
                if rel.get('TargetMode')=='External':
                    out.notices.append(label+' 含外部图片或绘图引用，未下载或读取。');continue
                target=_target(posixpath.dirname(name),rel.get('Target',''))
                if target not in names:
                    out.notices.append(label+' 的内嵌图片或绘图缺失。');continue
                if kind=='image':
                    images.setdefault(target,[]).append(label)
                else:pending.append((target,label+' / '+posixpath.basename(target)))
        except (ContentIssue,ET.ParseError,KeyError,ValueError,zipfile.BadZipFile):
            out.notices.append(label+' 的图片引用无法完整解析，已保留可读正文。')
    if not images:return
    extra['embeddedImages']={'referenced':len(images),'processed':0,'locations':[]}
    if not enabled:
        out.notices.append(f'文档包含 {len(images)} 个内嵌图片；未启用文档 OCR，图片内容尚未读取。');return
    if len(images)>MAX_PAGES:
        out.truncated=True;out.notices.append('内嵌图片超过100个，本次只处理前100个。')
    with tempfile.TemporaryDirectory(prefix='archive-embedded-') as folder:
        paths=[];labels={}
        for name,locations in list(images.items())[:MAX_PAGES]:
            path=Path(folder)/(str(len(paths)+1)+Path(name).suffix)
            try:
                path.write_bytes(archive.read(name))
                ext=detect_format(path,path.suffix.lower())
            except (OSError,zipfile.BadZipFile):
                out.notices.append(' / '.join(locations)+' 内嵌图片读取失败，已保留其他正文。');continue
            if ext not in IMAGE_FORMATS:
                out.notices.append(' / '.join(locations)+' 内嵌图片格式暂未支持：'+Path(name).suffix);continue
            from PIL import Image
            try:
                with Image.open(path) as image:
                    if image.n_frames>1:out.notices.append(' / '.join(locations)+' 内嵌多帧图片仅识别文档展示的首帧；需要其他帧时请单独导出该图片。')
            except (OSError,ValueError):
                out.notices.append(' / '.join(locations)+' 内嵌图片损坏或无法解码，已保留其他正文。');continue
            number=len(paths)+1
            paths.append(path)
            labels[number]=' / '.join(dict.fromkeys(locations))+' · 图片 '+name
            extra['embeddedImages']['locations'].append({'image':number,'part':name,'locations':locations})
        if paths:
            _ocr_into(out,extra,paths[0],images=paths,page_labels=labels)
            extra['embeddedImages']['processed']=len(extra['ocr']['recognizedPages'])
