"""Local, bounded attachment text extraction shared by both client exports."""
from collections import Counter, OrderedDict
import codecs
from copy import deepcopy
from decimal import Decimal, InvalidOperation, ROUND_HALF_UP
import hashlib
import json
import io
import multiprocessing
from pathlib import Path
import posixpath
import re
import struct
import time
import xml.etree.ElementTree as ET
import zipfile

MAX_FILE=20*1024*1024
MAX_TEXT=200000
MAX_SECTIONS=1000
MAX_PAGES=100
W='{http://schemas.openxmlformats.org/wordprocessingml/2006/main}'
S='{http://schemas.openxmlformats.org/spreadsheetml/2006/main}'
A='{http://schemas.openxmlformats.org/drawingml/2006/main}'
R='{http://schemas.openxmlformats.org/officeDocument/2006/relationships}'
P='{http://schemas.openxmlformats.org/presentationml/2006/main}'
SUPPORTED={'.pdf','.docx','.doc','.xlsx','.xls','.pptx','.ppt','.txt','.csv','.md','.json','.log'}
IMAGE_FORMATS={'.png','.jpg','.jpeg','.bmp','.tif','.tiff','.webp','.gif'}
FILE_LABELS={'ready':'原文件已取得','missing':'原文件未取得','not_loaded':'未加载原文件',
             'preview_only':'只有预览，原文件未取得','integrity_error':'原文件完整性校验失败'}
TEXT_LABELS={'extracted':'已提取正文','partial':'部分提取','no_text':'无可提取文字，可能需要OCR',
             'encrypted':'文件加密，未提取','unsupported':'格式未支持','failed':'正文提取失败',
             'too_large':'超过正文提取大小上限','timeout':'正文提取超时','budget_exceeded':'本次提取预算已用完',
             'integrity_error':'解密后完整性校验失败','ocr_extracted':'OCR文字待人工核对','ocr_unavailable':'本机OCR不可用','password_rejected':'密码不匹配','unsupported_encryption':'加密格式暂未支持',
             'original_unavailable':'缺少原文件，未提取','not_requested':'未请求加载附件'}


class ContentIssue(ValueError):
    def __init__(self,status,notice):self.status,self.notice=status,notice


class TextResult:
    def __init__(self):self.sections=[];self.characters=0;self.truncated=False;self.notices=[];self.encoding_issue=False
    def add(self,label,text):
        text=str(text).replace('\r\n','\n').replace('\r','\n')
        # PDF character maps can emit UTF-16 surrogates instead of Unicode scalars.
        text=re.sub(r'[\ud800-\udbff][\udc00-\udfff]',
                    lambda match:match[0].encode('utf-16-le','surrogatepass').decode('utf-16-le'),text)
        readable=text.encode('utf-8','backslashreplace').decode('utf-8')
        if readable!=text:
            if not self.encoding_issue:
                self.notices.append('正文含异常 Unicode 代理字符，已保留为 \\uXXXX 转义标记；请核对原件。')
            self.encoding_issue=True
            text=readable
        text=re.sub(r'[\x00-\x08\x0b\x0c\x0e-\x1f]','',text).strip()
        if not text:return
        if len(self.sections)>=MAX_SECTIONS or self.characters>=MAX_TEXT:
            self.truncated=True;return
        available=MAX_TEXT-self.characters
        if len(text)>available:text=text[:available];self.truncated=True
        self.sections.append({'location':label,'text':text});self.characters+=len(text)
    def result(self):
        status=('partial' if self.truncated or self.notices else 'extracted') if self.sections else 'no_text'
        return {'extractionStatus':status,'text':'\n\n'.join('['+s['location']+']\n'+s['text'] for s in self.sections),
                'sections':self.sections,'characters':self.characters,'truncated':self.truncated,
                **({'requiresReview':True} if self.encoding_issue else {}),
                'notices':self.notices+(['正文达到数量或长度上限，未提取的部分不作已读取处理。'] if self.truncated else [])}


def _xml_data(z,name):
    if name not in z.namelist():raise ContentIssue('failed','文档核心结构缺失。')
    data=z.read(name)
    declarations=data.replace(b'\x00',b'').upper()
    if b'<!DOCTYPE' in declarations or b'<!ENTITY' in declarations:
        raise ContentIssue('failed','XML结构不受支持。')
    return data


def _xml(z,name):
    return ET.fromstring(_xml_data(z,name))


def _target(base,target):
    name=posixpath.normpath(target.lstrip('/') if target.startswith('/') else posixpath.join(base,target))
    if name.startswith('../') or name not in ('word','xl','ppt') and not name.startswith(('word/','xl/','ppt/')):
        raise ContentIssue('failed','文档内部引用超出支持范围。')
    return name


def _rels(z,name,base):
    if name not in z.namelist():return {}
    return {r.get('Id'):_target(base,r.get('Target','')) for r in _xml(z,name)
            if r.get('TargetMode')!='External' and r.get('Target')}


def _hyperlink_rels(z,name):
    """Read hyperlink metadata without resolving or opening its target."""
    if name not in z.namelist():return {}
    return {r.get('Id'):r.get('Target','') for r in _xml(z,name)
            if r.get('Type','').endswith('/hyperlink') and r.get('Target')}


_XLSX_BUILTIN_FORMATS={
    9:'0%',10:'0.00%',
    14:'m/d/yy',15:'d-mmm-yy',16:'d-mmm',17:'mmm-yy',18:'h:mm AM/PM',
    19:'h:mm:ss AM/PM',20:'h:mm',21:'h:mm:ss',22:'m/d/yy h:mm',
    27:'yyyy"年"m"月"d"日"',28:'yyyy"年"m"月"d"日"',29:'mm"月"dd"日"',
    30:'mm-dd-yy',31:'yyyy"年"m"月"d"日"',32:'h"时"mm"分"',
    33:'h"时"mm"分"',34:'h"时"mm"分"',35:'h"时"mm"分"',
    36:'yyyy"年"m"月"',45:'mm:ss',46:'[h]:mm:ss',47:'mmss.0',
}


def _xlsx_format_code(styles,style_index):
    """Return the OOXML number format for a cellXfs index, if declared."""
    if style_index is None:style_index=0
    if style_index<0 or style_index>=len(styles):return None
    return styles[style_index]


def _xlsx_styles(z):
    """Read cellXfs number formats without attempting full Excel rendering."""
    if 'xl/styles.xml' not in z.namelist():return []
    root=_xml(z,'xl/styles.xml');formats=dict(_XLSX_BUILTIN_FORMATS)
    custom=root.find(S+'numFmts')
    if custom is not None:
        for item in custom.findall(S+'numFmt'):
            formats[int(item.get('numFmtId'))]=item.get('formatCode','')
    cell_xfs=root.find(S+'cellXfs')
    if cell_xfs is None:return []
    return [formats.get(int(item.get('numFmtId','0')),'') for item in cell_xfs.findall(S+'xf')]


def _xlsx_date1904(workbook):
    properties=workbook.find(S+'workbookPr')
    return properties is not None and properties.get('date1904','0').lower() in ('1','true','on')


def _xlsx_format_body(code):
    """Remove literals and conditions before recognizing a small common subset."""
    body=re.sub(r'"[^"]*"','',code)
    body=re.sub(r'\\.', '',body)
    body=re.sub(r'\[[^\]]*\]','',body)
    return body.strip()


def _xlsx_has_unsupported_bracket(code):
    for token in re.findall(r'\[([^\]]+)\]',code):
        if token.lower().startswith('$-'):continue
        return True
    return False


def _xlsx_date_kind(code):
    body=_xlsx_format_body(code).lower()
    if not body:return None
    has_date=bool(re.search(r'[dy]',body))
    has_time=bool(re.search(r'[hs]',body)) or 'am/pm' in body
    if not has_date and not has_time:return None
    return 'datetime' if has_date and has_time else ('date' if has_date else 'time')


def _excel_format_number(raw,code,datemode):
    """Apply only unambiguous date, percentage, and zero-padding semantics."""
    if not code or code.lower()=='general':return raw
    if ';' in code or _xlsx_has_unsupported_bracket(code):
        return raw+' [格式代码: '+code+'；复杂或未识别的数字格式，保留原值，未完整渲染。]'
    try:number=float(raw)
    except ValueError:return raw
    try:exact=Decimal(raw)
    except InvalidOperation:return raw
    kind=_xlsx_date_kind(code)
    if kind:
        if datemode==0 and Decimal('60')<=exact<Decimal('61'):
            return raw+' [格式代码: '+code+'；1900日期序列60无法真实表示，保留原值，未伪装成日期。]'
        import xlrd
        try:value=xlrd.xldate.xldate_as_datetime(number,datemode)
        except (ValueError,OverflowError,xlrd.xldate.XLDateError):
            return raw+' [格式代码: '+code+'；日期序列超出可转换范围，保留原值。]'
        if kind=='date':return value.date().isoformat()
        if kind=='time':return value.time().isoformat()
        return value.isoformat()
    body=code.strip()
    if re.fullmatch(r'[#0,]+(?:\.[#0]+)?%',body):
        integer,_,fraction=body[:-1].partition('.')
        precision=len(fraction)
        comma=',' in integer
        quantum=Decimal(1).scaleb(-precision)
        rounded=(exact*100).quantize(quantum,rounding=ROUND_HALF_UP)
        return format(rounded,(',' if comma else '')+'.'+str(precision)+'f')+'%'
    if re.fullmatch(r'0+',body) and exact==exact.to_integral_value():
        sign='-' if exact<0 else ''
        return sign+format(abs(exact).to_integral_value(),'f').zfill(len(body))
    return raw+' [格式代码: '+code+'；复杂或未识别的数字格式，保留原值，未完整渲染。]'


def _merged_section(out,sheet_name,ref,anchor,values):
    """Record one merge range without inventing values for covered cells."""
    value=values.get(anchor,'')
    shown=value if value else '空'
    out.add('工作表 '+sheet_name+' 合并单元格 '+ref,
            '锚点 '+anchor+'='+shown+'；该值标注合并关系，不作为其他单元格的独立值。')


def _xlsx_merge_anchor(ref):
    return ref.split(':',1)[0].replace('$','').upper()


def _word_text(node,hyperlinks=None,annotations=None):
    if node.tag in (W+'del',W+'moveFrom'):return ''
    if node.tag==W+'tr' and node.find(W+'trPr/'+W+'del') is not None:return ''
    if annotations is not None:
        marker=annotations.marker(node)
        if marker is not None:return marker
    if node.tag==W+'t':return node.text or ''
    if node.tag==W+'tab':return '\t'
    if node.tag==W+'br':return '\n'
    if node.tag==W+'hyperlink':
        text=''.join(_word_text(child,hyperlinks,annotations) for child in node)
        target=(hyperlinks or {}).get(node.get(R+'id'))
        anchor=node.get(W+'anchor') or node.get(W+'docLocation')
        destination=(target+('#'+anchor if anchor else '')) if target else ('#'+anchor if anchor else '')
        if destination:return text+' [链接: '+destination+']'
        return text
    return ''.join(_word_text(child,hyperlinks,annotations) for child in node)


def _word_part_hyperlinks(z,name):
    base=posixpath.dirname(name)
    rels=posixpath.join(base,'_rels',posixpath.basename(name)+'.rels')
    return _hyperlink_rels(z,rels)


def _word_section_refs(node):
    if node.tag in (W+'del',W+'moveFrom',W+'sectPrChange'):return
    if node.tag==W+'tr' and node.find(W+'trPr/'+W+'del') is not None:return
    if node.tag in (W+'headerReference',W+'footerReference'):
        yield node.tag,node.get(R+'id')
    for child in node:yield from _word_section_refs(child)


def _word_header_parts(z,body,out,*,read_xml=_xml):
    references=list(dict.fromkeys(_word_section_refs(body)))
    if not references:return
    rels_name='word/_rels/document.xml.rels'
    relationships={rel.get('Id'):rel for rel in read_xml(z,rels_name)} if rels_name in z.namelist() else {}
    seen=set()
    for tag,reference in references:
        kind='header' if tag==W+'headerReference' else 'footer'
        label='页眉' if kind=='header' else '页脚'
        relation=relationships.get(reference)
        if (relation is None or not relation.get('Type','').endswith('/'+kind)
                or relation.get('TargetMode')=='External' or not relation.get('Target')):
            out.notices.append('Word'+label+'引用未解析；正文保留。');continue
        try:
            name=_target('word',relation.get('Target'))
            if name in seen:continue
            seen.add(name)
            yield name,read_xml(z,name)
        except (ContentIssue,ET.ParseError):
            out.notices.append('Word'+label+'部件无法读取；正文保留。')


def _word_child_int(node,name,default=0):
    child=node.find(W+name)
    if child is None:return default
    value=child.get(W+'val')
    if value is None:return default
    try:return max(0,int(value))
    except ValueError:return default


def _word_table_rows(table,hyperlinks=None,annotations=None):
    active={}
    for row in table.findall(W+'tr'):
        trpr=row.find(W+'trPr')
        if trpr is not None and trpr.find(W+'del') is not None:continue
        grid_before=_word_child_int(trpr,'gridBefore') if trpr is not None else 0
        rendered=(['[前置空列 '+str(grid_before)+']'] if grid_before else [])
        column=grid_before
        next_active={}
        for cell in row.findall(W+'tc'):
            tcpr=cell.find(W+'tcPr')
            span=_word_child_int(tcpr,'gridSpan',1) if tcpr is not None else 1
            span=max(1,span)
            key=(column,span)
            own_text=_word_text(cell,hyperlinks,annotations)
            merge=tcpr.find(W+'vMerge') if tcpr is not None else None
            merge_value=merge.get(W+'val','continue') if merge is not None else None
            text=own_text
            if merge is not None and merge_value=='continue':
                if key in active:
                    text=own_text or active[key]+'（合并继承）'
                    next_active[key]=active[key]
                else:
                    text=own_text+'[合并来源未匹配]'
            elif merge is not None and merge_value=='restart':
                next_active[key]=own_text
            rendered.append(text)
            rendered.extend(['']*(span-1))
            column+=span
        active=next_active
        yield '\t'.join(rendered)


def _word_blocks(parent):
    for child in parent:
        if child.tag in (W+'del',W+'moveFrom'):continue
        if child.tag in (W+'p',W+'tbl'):yield child
        elif child.tag==W+'sdt':
            content=child.find(W+'sdtContent')
            if content is not None:yield from _word_blocks(content)


def detect_format(path,declared):
    """Prefer an identifiable original format to an attachment's display name."""
    with path.open('rb') as stream:head=stream.read(1024)
    if b'%PDF-' in head[:1024]:return '.pdf'
    if head.startswith(b'\x89PNG\r\n\x1a\n'):return '.png'
    if head.startswith(b'\xff\xd8\xff'):return '.jpg'
    if head.startswith((b'II*\x00',b'MM\x00*')):return '.tiff'
    if head.startswith(b'BM'):return '.bmp'
    if head.startswith((b'GIF87a',b'GIF89a')):return '.gif'
    if head.startswith(b'RIFF') and head[8:12]==b'WEBP':return '.webp'
    if head.startswith(b'PK\x03\x04'):
        try:
            with zipfile.ZipFile(path) as z:
                names=set(z.namelist())
                for name,ext in [('word/document.xml','.docx'),('xl/workbook.xml','.xlsx'),('ppt/presentation.xml','.pptx')]:
                    if name in names:return ext
        except zipfile.BadZipFile:pass
    return declared


def _office(path,ext,out,extra=None,ocr=False):
    with zipfile.ZipFile(path) as z:
        entries=z.infolist()
        if len(entries)>5000 or sum(e.file_size for e in entries)>40*1024*1024:
            raise ContentIssue('too_large','文档展开后超过40MB或5000个条目。')
        if any(e.flag_bits&1 for e in entries):raise ContentIssue('encrypted','文档加密，未尝试密码。')
        parts=[]
        read_part=_xml
        if ext=='.docx':
            from offline_word_annotations import WordAnnotations
            from offline_word_xml import parse_word_xml
            word_parts={}
            def read_part(archive,name):
                if name not in word_parts:
                    root,omitted=parse_word_xml(_xml_data(archive,name))
                    word_parts[name]=root
                    if omitted:
                        out.notices.append(name+' 有 '+str(omitted)+' 处兼容内容无可用分支，未读取；其他正文保留。')
                return word_parts[name]
            annotations=WordAnnotations(max_references=MAX_SECTIONS)
            parts.append(('word/document.xml','Word 正文'))
            document_name='word/document.xml'
            document=read_part(z,document_name)
            body=document.find(W+'body')
            if body is None:raise ContentIssue('failed','Word正文结构缺失。')
            document_links=_word_part_hyperlinks(z,document_name)
            for number,node in enumerate(_word_blocks(body),1):
                if node.tag==W+'p':out.add('段落 '+str(number),_word_text(node,document_links,annotations))
                elif node.tag==W+'tbl':
                    for rownum,row_text in enumerate(_word_table_rows(node,document_links,annotations),1):
                        out.add(f'表格 {number} 行 {rownum}',row_text)
                if out.truncated:break
            for name,header in _word_header_parts(z,body,out,read_xml=read_part):
                links=_word_part_hyperlinks(z,name)
                for node in _word_blocks(header):
                    if node.tag==W+'p':out.add(name,_word_text(node,links,annotations))
                    else:
                        for row_text in _word_table_rows(node,links,annotations):out.add(name,row_text)
            for label,name,node,metadata in annotations.referenced(z,out,read_xml=read_part,resolve_target=_target):
                if out.truncated:break
                if metadata:out.add(label+' 来源',metadata)
                links=_word_part_hyperlinks(z,name)
                for number,block in enumerate(_word_blocks(node),1):
                    if block.tag==W+'p':out.add(label+' 段落 '+str(number),_word_text(block,links,annotations))
                    else:
                        for rownum,row_text in enumerate(_word_table_rows(block,links,annotations),1):
                            out.add(label+f' 表格 {number} 行 {rownum}',row_text)
                    if out.truncated:break
        elif ext=='.xlsx':
            from offline_spreadsheet_annotations import shared_formulas,formula_annotation,extract_sheet_comments
            from offline_spreadsheet_visibility import xlsx_visibility
            book=_xml(z,'xl/workbook.xml');rels=_rels(z,'xl/_rels/workbook.xml.rels','xl')
            styles=_xlsx_styles(z);datemode=1 if _xlsx_date1904(book) else 0
            strings=[]
            if 'xl/sharedStrings.xml' in z.namelist():strings=[''.join(n.text or '' for n in si.iter(S+'t')) for si in _xml(z,'xl/sharedStrings.xml')]
            for sheet in book.iter(S+'sheet'):
                target=rels.get(sheet.get(R+'id'))
                if not target:out.notices.append('有工作表引用未解析。');continue
                sheet_root=_xml(z,target)
                visibility=xlsx_visibility(sheet,sheet_root)
                visibility.describe(out)
                parts.append((target,'工作表 '+visibility.sheet_name))
                shared=shared_formulas(sheet_root)
                sheet_links=_hyperlink_rels(z,
                    posixpath.join(posixpath.dirname(target),'_rels',posixpath.basename(target)+'.rels'))
                hyperlinks={}
                merges=sheet_root.find(S+'mergeCells')
                merge_refs=[merge.get('ref') for merge in merges.findall(S+'mergeCell')
                            if merge.get('ref')] if merges is not None else []
                merge_anchors={_xlsx_merge_anchor(ref) for ref in merge_refs}
                cell_values={}
                hyperlink_root=sheet_root.find(S+'hyperlinks')
                if hyperlink_root is not None:
                    for link in hyperlink_root.findall(S+'hyperlink'):
                        ref=link.get('ref')
                        if not ref:continue
                        destination=sheet_links.get(link.get(R+'id')) or ''
                        location=link.get('location')
                        if location:destination+=('#' if destination else '')+location
                        if destination:
                            hyperlinks[ref]=(link.get('display'),destination)
                for row in sheet_root.iter(S+'row'):
                    cells=[]
                    for cell in row.findall(S+'c'):
                        value=cell.findtext(S+'v','');kind=cell.get('t','')
                        if kind=='s':value=strings[int(value)] if value.isdigit() and int(value)<len(strings) else '[共享文本缺失]'
                        elif kind=='inlineStr':value=''.join(n.text or '' for n in cell.iter(S+'t'))
                        elif kind=='b' and value:value='TRUE' if value=='1' else 'FALSE'
                        elif kind in ('','n') and value:
                            style=cell.get('s');style_index=int(style) if style is not None else None
                            value=_excel_format_number(value,_xlsx_format_code(styles,style_index),datemode)
                        formula=formula_annotation(cell,shared)
                        if formula:value+=((' ' if value else '')+formula)
                        cell_ref=cell.get('r','?')
                        cell_key=cell_ref.replace('$','').upper()
                        if cell_key in merge_anchors:cell_values[cell_key]=value
                        if value:cells.append(visibility.cell_text(cell_ref,value))
                    row_number=row.get('r','?')
                    out.add(visibility.row_label(int(row_number) if row_number.isdigit() else row_number,
                        hidden=row.get('hidden','0').lower() in ('1','true')),'\t'.join(cells))
                    if out.truncated:break
                for ref in merge_refs:
                    _merged_section(out,visibility.sheet_name,ref,
                                    _xlsx_merge_anchor(ref),cell_values)
                for ref,(display,destination) in hyperlinks.items():
                    out.add('工作表 '+visibility.sheet_name+' 超链接 '+ref,
                            (display+' -> ' if display else '')+destination)
                extract_sheet_comments(z,target,visibility.sheet_name,out,read_xml=_xml,resolve_target=_target)
                if out.truncated:break
        else:
            from offline_presentation_tables import extract_slide_text
            from offline_presentation_links import read_presentation_links,extract_presentation_links
            presentation=_xml(z,'ppt/presentation.xml');rels=_rels(z,'ppt/_rels/presentation.xml.rels','ppt')
            slides=list(presentation.iter(P+'sldId'))
            if len(slides)>MAX_PAGES:out.truncated=True
            for number,slide in enumerate(slides[:MAX_PAGES],1):
                name=rels.get(slide.get(R+'id'))
                if not name:out.notices.append('有幻灯片引用未解析。');continue
                slide_root=_xml(z,name)
                label='幻灯片 '+str(number)
                if slide_root.get('show','true').strip() in ('0','false'):
                    label+=' [隐藏幻灯片]'
                parts.append((name,label))
                extract_slide_text(slide_root,out,label)
                slide_relationships=read_presentation_links(z,name,_xml)
                extract_presentation_links(slide_root,out,label,slide_relationships)
                note_rels=_rels(z,posixpath.dirname(name)+'/_rels/'+posixpath.basename(name)+'.rels',posixpath.dirname(name))
                from offline_presentation_charts import C,extract_chart
                for chart in slide_root.iter(C+'chart'):
                    target=note_rels.get(chart.get(R+'id'))
                    if not target:
                        out.notices.append(label+'：图表引用未解析。');continue
                    extract_chart(_xml(z,target),out,label)
                for relationship in slide_relationships.values():
                    if relationship['type'].endswith('/notesSlide') and relationship['mode']!='External':
                        target=_target(posixpath.dirname(name),relationship['target'])
                        parts.append((target,label+' 备注'))
                        note_root=_xml(z,target)
                        extract_slide_text(note_root,out,label+' 备注')
                        extract_presentation_links(note_root,out,label+' 备注',
                                                   read_presentation_links(z,target,_xml))
                if out.characters>=MAX_TEXT:break
        from offline_document_images import extract_embedded_images
        extract_embedded_images(z,parts,out,extra if extra is not None else {},ocr,read_xml=read_part)


def _legacy_word(ole,out):
    word=ole.openstream('WordDocument').read()
    if len(word)<34 or word[:2]!=b'\xec\xa5':raise ContentIssue('unsupported','不支持此DOC版本，原文件保留。')
    flags=struct.unpack_from('<H',word,10)[0]
    if flags&0x8100:raise ContentIssue('encrypted','DOC文件加密，未尝试密码。')
    table=ole.openstream('1Table' if flags&0x200 else '0Table').read()
    offset=32;csw=struct.unpack_from('<H',word,offset)[0];offset+=2+csw*2
    cslw=struct.unpack_from('<H',word,offset)[0];rglw=offset+2
    ccp=struct.unpack_from('<I',word,rglw+12)[0]
    offset=rglw+cslw*4
    pairs=struct.unpack_from('<H',word,offset)[0];offset+=2
    if pairs<=33:raise ContentIssue('unsupported','此DOC版本没有受支持的正文片段索引。')
    start,length=struct.unpack_from('<II',word,offset+33*8);clx=table[start:start+length]
    cursor=0
    while cursor<len(clx) and clx[cursor]==1:cursor+=3+struct.unpack_from('<H',clx,cursor+1)[0]
    if cursor+5>len(clx) or clx[cursor]!=2:raise ContentIssue('unsupported','DOC正文片段索引不受支持。')
    size=struct.unpack_from('<I',clx,cursor+1)[0];plc=clx[cursor+5:cursor+5+size]
    if size<4 or (size-4)%12 or len(plc)!=size:raise ContentIssue('failed','DOC正文片段结构损坏。')
    count=(size-4)//12;text=[]
    for index in range(count):
        low,high=struct.unpack_from('<II',plc,index*4)
        if low>=ccp:break
        chars=min(high,ccp)-low
        if chars<0:raise ContentIssue('failed','DOC字符位置异常。')
        fc=struct.unpack_from('<I',plc,4*(count+1)+8*index+2)[0]
        compressed=bool(fc&0x40000000);start=fc&0x3fffffff
        if compressed:start//=2
        raw=word[start:start+chars*(1 if compressed else 2)]
        if len(raw)!=chars*(1 if compressed else 2):raise ContentIssue('failed','DOC正文片段不完整。')
        text.append(raw.decode('cp1252' if compressed else 'utf-16-le',errors='replace'))
        if sum(map(len,text))>=MAX_TEXT:out.truncated=True;break
    for number,line in enumerate(''.join(text).replace('\x07','\t').replace('\r','\n').splitlines(),1):out.add('正文段落 '+str(number),line)
    out.notices.append('旧版DOC已提取主文档文字；图形、文本框、修订和版式未完整还原。')


def _legacy_ppt(ole,out):
    data=ole.openstream('PowerPoint Document').read();current=ole.openstream('Current User').read()
    latest=struct.unpack_from('<I',current,16)[0];persist={};edit=latest;seen=set();doc_id=None
    while edit and edit not in seen and len(seen)<128:
        seen.add(edit)
        if edit+36>len(data):raise ContentIssue('failed','PPT编辑索引损坏。')
        previous,directory,document=struct.unpack_from('<III',data,edit+16)
        if doc_id is None:doc_id=document
        _,kind,size=struct.unpack_from('<HHI',data,directory)
        if kind not in (6001,6002) or directory+8+size>len(data):raise ContentIssue('unsupported','PPT持久对象索引未支持。')
        pos,end=directory+8,directory+8+size
        while pos+4<=end:
            packed=struct.unpack_from('<I',data,pos)[0];pos+=4;first,count=packed&0xfffff,packed>>20
            if not count or pos+4*count>end:raise ContentIssue('failed','PPT对象索引损坏。')
            for i in range(count):persist.setdefault(first+i,struct.unpack_from('<I',data,pos+4*i)[0])
            pos+=4*count
        edit=previous
    visited=set()
    def records(start,end,depth=0):
        if depth>32:out.notices.append('PPT嵌套层级超过限制。');return
        pos=start
        while pos+8<=end:
            ver,kind,size=struct.unpack_from('<HHI',data,pos);low,high=pos+8,pos+8+size
            if high>end:raise ContentIssue('failed','PPT记录边界异常。')
            if kind==4000:out.add('旧版幻灯片文字',data[low:high].decode('utf-16-le',errors='replace'))
            elif kind==4008:out.add('旧版幻灯片文字',data[low:high].decode('latin1'))
            elif kind==1011 and size>=4:object_record(struct.unpack_from('<I',data,low)[0],depth+1)
            elif ver&15==15 or kind==0xf00d:records(low,high,depth+1)
            if out.characters>=MAX_TEXT:return
            pos=high
    def object_record(identifier,depth=0):
        if identifier in visited:return
        visited.add(identifier);pos=persist.get(identifier)
        if pos is None:out.notices.append('有PPT对象未取得。');return
        if pos+8>len(data):raise ContentIssue('failed','PPT对象边界异常。')
        size=struct.unpack_from('<I',data,pos+4)[0]
        if pos+8+size>len(data):raise ContentIssue('failed','PPT对象内容损坏。')
        records(pos,pos+8+size,depth)
    object_record(doc_id)
    out.notices.append('旧版PPT按当前编辑的对象索引提取文字；图表、图形和精确页序未完整还原。')


def _pdf_has_images(page):
    # Inspect resources without decompressing every raster before Windows renders it.
    pending=[page.get('/Resources',{})];seen=set()
    while pending:
        resource=pending.pop()
        resources=resource.get_object() if hasattr(resource,'get_object') else resource
        objects=resources['/XObject'].get_object() if '/XObject' in resources else {}
        for reference in objects.values():
            image=reference.get_object()
            if id(image) in seen:continue
            seen.add(id(image))
            if image.get('/Subtype')=='/Image':return True
            if image.get('/Subtype')=='/Form':pending.append(image.get('/Resources',{}))
    contents=page.get_contents()
    return bool(contents and any(operator==b'INLINE IMAGE' for _,operator in contents.operations))


def extract_document(path,ext, *, password=None, ocr=False):
    """Worker-side parser; does not launch Office, resolve links or run formulas."""
    out=TextResult()
    extra={}
    if ext in IMAGE_FORMATS:
        _ocr_into(out,extra,path,password=password)
    elif ext=='.pdf':
        from pypdf import PdfReader
        from offline_pdf_forms import extract_pdf_forms, sort_pdf_sections
        from offline_pdf_links import extract_pdf_annotations
        import logging
        logging.getLogger('pypdf').setLevel(logging.CRITICAL)
        reader=PdfReader(path,strict=False)
        if reader.is_encrypted:
            if not reader.decrypt(password if password is not None else ''):
                raise ContentIssue('password_rejected' if password is not None else 'encrypted',
                    '所提供的PDF打开密码不匹配。' if password is not None else 'PDF需要打开密码；可按文件摘要提供已知密码。')
            extra['decryption']={'status':'opened','passwordSupplied':password is not None}
        pages=len(reader.pages)
        if pages>MAX_PAGES:out.truncated=True
        needs_ocr=[]
        for index in range(min(pages,MAX_PAGES)):
            try:
                text=reader.pages[index].extract_text() or ''
                if text.strip():out.add('第 '+str(index+1)+' 页',text)
                if not text.strip() or _pdf_has_images(reader.pages[index]):needs_ocr.append(index)
            except Exception:needs_ocr.append(index)
            if out.characters>=MAX_TEXT:
                if index+1<pages:out.truncated=True
                break
        extract_pdf_forms(reader,out,max_pages=MAX_PAGES,max_sections=MAX_SECTIONS,max_text=MAX_TEXT)
        extract_pdf_annotations(reader,out,max_pages=MAX_PAGES,max_sections=MAX_SECTIONS,max_text=MAX_TEXT)
        if needs_ocr:
            if ocr:_ocr_into(out,extra,path,pages=needs_ocr,password=password)
            else:out.notices.extend('第 '+str(index+1)+' 页含图片或无可提取文字；可启用文档 OCR 补充。' for index in needs_ocr)
        # Native text and OCR arrive in separate passes; preserve document page order.
        sort_pdf_sections(out.sections)
    elif ext in ('.docx','.xlsx','.pptx'):
        if path.read_bytes()[:8]==bytes.fromhex('d0cf11e0a1b11ae1'):
            if password is None:raise ContentIssue('encrypted','Office文件需要打开密码；可按文件摘要提供已知密码。')
            import msoffcrypto
            from msoffcrypto.exceptions import InvalidKeyError, DecryptionError
            try:
                with path.open('rb') as source:
                    encrypted=msoffcrypto.OfficeFile(source)
                    encrypted.load_key(password=password,verify_password=True)
                    plain=io.BytesIO()
                    try:encrypted.decrypt(plain,verify_integrity=True)
                    except InvalidKeyError:raise ContentIssue('integrity_error','密码已验证，但文件内容完整性校验未通过；保留原件，不使用解密内容。')
                    plain.seek(0)
                    _office(plain,ext,out,extra,ocr)
                    extra['decryption']={'status':'opened','passwordSupplied':True}
            except InvalidKeyError:raise ContentIssue('password_rejected','所提供的Office打开密码不匹配。')
            except DecryptionError:raise ContentIssue('unsupported_encryption','此加密格式暂未支持，原文件保留。')
        else:_office(path,ext,out,extra,ocr)
    elif ext=='.xls':
        import xlrd
        from offline_spreadsheet_visibility import xls_visibility
        from offline_legacy_spreadsheet_annotations import extract_xls_annotations
        try:book=xlrd.open_workbook(str(path),formatting_info=True,on_demand=True,ragged_rows=True)
        except xlrd.XLRDError as error:
            if 'encrypt' in str(error).lower():raise ContentIssue('encrypted','Excel文件加密，未尝试密码。')
            raise
        try:
            for sheet in book.sheets():
                visibility=xls_visibility(sheet)
                visibility.describe(out)
                merges=sheet.merged_cells
                merge_anchors={(rlo,clo) for rlo,rhi,clo,chi in merges}
                cell_values={}
                for rownum in range(sheet.nrows):
                    cells=[]
                    for col in range(sheet.row_len(rownum)):
                        cell=sheet.cell(rownum,col);value=cell.value
                        if cell.ctype==xlrd.XL_CELL_BOOLEAN:
                            value='TRUE' if value else 'FALSE'
                        elif cell.ctype==xlrd.XL_CELL_ERROR:
                            code=int(value)
                            value=xlrd.error_text_from_code.get(code)
                            if value is None:value='#ERROR('+str(code)+') [错误码: '+str(code)+']'
                        elif cell.ctype in (xlrd.XL_CELL_NUMBER,xlrd.XL_CELL_DATE):
                            format_key=book.xf_list[cell.xf_index].format_key
                            format_definition=book.format_map.get(format_key)
                            format_code=format_definition.format_str if format_definition is not None else ''
                            value=_excel_format_number(str(value),format_code,book.datemode)
                            if format_definition is None and format_key:
                                value += ' [格式代码: '+str(format_key)+'；格式定义缺失，保留原值。]'
                        if (rownum,col) in merge_anchors:
                            cell_values[xlrd.formula.colname(col)+str(rownum+1)]=str(value)
                        if value!='':cells.append(visibility.cell_text(xlrd.formula.colname(col)+str(rownum+1),str(value),col+1))
                    out.add(visibility.row_label(rownum+1),'\t'.join(cells))
                    if out.truncated:break
                for rlo,rhi,clo,chi in merges:
                    ref=xlrd.formula.colname(clo)+str(rlo+1)+':'+xlrd.formula.colname(chi-1)+str(rhi)
                    _merged_section(out,visibility.sheet_name,ref,
                                    xlrd.formula.colname(clo)+str(rlo+1),cell_values)
                extract_xls_annotations(sheet,visibility,out)
                if out.truncated:break
        finally:book.release_resources()
    elif ext in ('.doc','.ppt'):
        import olefile
        with olefile.OleFileIO(path) as ole:
            if ole.exists('EncryptedPackage') or ole.exists('EncryptionInfo'):
                raise ContentIssue('encrypted','Office文件加密，未尝试密码。')
            (_legacy_word if ext=='.doc' else _legacy_ppt)(ole,out)
    else:
        data=path.read_bytes()
        # UTF-32 LE starts with the UTF-16 LE marker, so match it first.
        if data.startswith((codecs.BOM_UTF32_LE,codecs.BOM_UTF32_BE)):encoding='utf-32'
        elif data.startswith((codecs.BOM_UTF16_LE,codecs.BOM_UTF16_BE)):encoding='utf-16'
        elif data.startswith(codecs.BOM_UTF8):encoding='utf-8-sig'
        else:encoding=None
        if encoding:
            try:text=data.decode(encoding)
            except UnicodeDecodeError:raise ContentIssue('failed','文本声明为 '+encoding.upper()+'，但字节序列不完整或无效。')
        else:
            try:text=data.decode('utf-8-sig')
            except UnicodeDecodeError:
                try:text=data.decode('gb18030')
                except UnicodeDecodeError:raise ContentIssue('failed','文本编码未识别。')
        out.add('文件正文',text)
    result=out.result()
    if extra.get('ocr'):
        result['requiresReview']=True
        if not result['text'] and extra['ocr']['status']=='unavailable':result['extractionStatus']='ocr_unavailable'
        elif result['text'] and result['extractionStatus']=='extracted':result['extractionStatus']='ocr_extracted'
    return {**result,**extra}


def _ocr_into(out,extra,path,*,pages=None,password=None,images=None,page_labels=None):
    from offline_windows_ocr import recognize
    options={'pages':pages,'password':password}
    if images is not None:options['images']=images
    result=recognize(path,**options)
    extra['ocr']={k:v for k,v in result.items() if k!='pages'}
    extra['ocr']['recognizedPages']=[]
    extra['ocr']['languages']=sorted({p['language'] for p in result['pages'] if p.get('language')})
    completed=set()
    for page in result['pages']:
        if page.get('text','').strip():
            label=(page_labels or {}).get(page['page'],'第 '+str(page['page'])+' 页')
            before=len(out.sections)
            out.add(label+' OCR（请核对数字、单位和表格）',page['text'])
            if len(out.sections)>before and page.get('lines'):
                # Coordinates refer to the rendered OCR bitmap, not PDF points or worksheet cells.
                layout={k:page[k] for k in ('page','width','height','textAngle') if k in page}
                layout['coordinateUnit']='rendered_pixel'
                remaining=len(out.sections[-1]['text']);lines=[]
                for line in page['lines']:
                    if remaining<=0:break
                    lines.append(line);remaining-=len(line.get('text',''))
                layout['lines']=lines
                out.sections[-1]['ocr']=layout
            extra['ocr']['recognizedPages'].append(page['page'])
            completed.add(page['page'])
    expected=set(range(1,len(images)+1)) if images is not None else set(i+1 for i in pages) if pages is not None else {1}
    if pages is None and images is None:
        from PIL import Image
        with Image.open(path) as image:
            if image.n_frames>1:
                expected=set(range(1,min(image.n_frames,MAX_PAGES)+1))
                if image.n_frames>MAX_PAGES:out.truncated=True
    missing=sorted(expected-completed)
    extra['ocr']['unreadPages']=missing
    if missing:out.notices.append('OCR未取得以下页的文字：'+','.join(map(str,missing))+'；原件保留。')
    if result['status']!='complete':out.notices.append('本机OCR未完整完成：'+result['status']+'；已取得的文字保留。')


def read_passwords(filename):
    if not filename:return {}
    path=Path(filename)
    if path.stat().st_size>1024*1024:raise ValueError('password_file_too_large')
    value=json.loads(path.read_text(encoding='utf-8-sig'))
    entries=value.get('passwords') if isinstance(value,dict) and value.get('schemaVersion')==1 else None
    if not isinstance(entries,dict) or any(not re.fullmatch('[a-fA-F0-9]{64}',k) or not isinstance(v,str) or len(v)>1024 for k,v in entries.items()):
        raise ValueError('invalid_password_file')
    return {k.lower():v for k,v in entries.items() if v}



def _worker(path,ext,connection,options=None):
    try:
        try:result=extract_document(Path(path),ext,**(options or {}))
        except ContentIssue as issue:result={'extractionStatus':issue.status,'text':'','sections':[],'characters':0,'truncated':False,'notices':[issue.notice]}
        except Exception:result={'extractionStatus':'failed','text':'','sections':[],'characters':0,'truncated':False,'notices':['文件结构损坏或此内容尚未支持，原文件仍保留。']}
        connection.send(result)
    finally:connection.close()


class ExtractionCache:
    """Successful parser results owned by one loaded source, never persisted."""
    def __init__(self,max_bytes=16*1024*1024):
        self.max_bytes=max_bytes;self.bytes=0;self.entries=OrderedDict()

    def get(self,key):
        item=self.entries.get(key)
        if item is None:return None
        self.entries.move_to_end(key)
        return deepcopy(item[0])

    def put(self,key,result):
        if result['extractionStatus'] not in ('extracted','partial','ocr_extracted','no_text'):return
        ocr=result.get('ocr')
        if ocr and (ocr.get('status')!='complete' or ocr.get('unreadPages')):return
        size=len(json.dumps(result,ensure_ascii=True).encode('ascii'))
        if size>self.max_bytes:return
        old=self.entries.pop(key,None)
        if old:self.bytes-=old[1]
        while self.entries and self.bytes+size>self.max_bytes:
            _,(_,removed)=self.entries.popitem(last=False);self.bytes-=removed
        self.entries[key]=(deepcopy(result),size);self.bytes+=size


class AttachmentExtractor:
    def __init__(self,config=None,progress=None,*,shared_cache=None):
        self.config=config or {};self.progress=progress
        self.cache={};self.file_counts=Counter();self.status_counts=Counter();self.total=0;self.characters=0;self.image_total=0;self.ocr_total=0;self.preview_ocr_total=0
        self.shared_cache=shared_cache;self.extraction_runs=0;self.cache_hits=0;self.reused=0
        self.password_issue=False
        try:self.passwords=read_passwords(self.config.get('documentPasswordFile'))
        except (OSError,ValueError):self.passwords={};self.password_issue=True
        self.passwords.update(self.config.get('_documentPasswords',{}))
        self.options={}

    def _run(self,path,ext):
        context=multiprocessing.get_context('spawn');parent,child=context.Pipe(duplex=False)
        process=context.Process(target=_worker,args=(str(path),ext,child,self.options),daemon=True)
        try:
            process.start();child.close()
            deadline=time.monotonic()+(90 if self.options.get('ocr') else 15)
            while time.monotonic()<deadline:
                if parent.poll(.25):return parent.recv()
                if self.progress:self.progress({'stage':'attachment_text','processedFiles':self.total,'imageFiles':self.image_total})
            return {'extractionStatus':'timeout','notices':['单个文件正文提取超时，原文件保留；后续文件继续。']}
        except (EOFError,OSError):return {'extractionStatus':'failed','notices':['附件提取进程未正常完成，原文件保留。']}
        finally:
            parent.close();child.close()
            if process.pid:
                process.join(.2)
                if process.is_alive():process.terminate();process.join(2)
                process.close()
    def attach(self,row,destination,copy_media):
        from offline_images import message_nodes
        for node,trail,_ in message_nodes(row):
            is_image=node.get('kind')=='image'
            if node.get('kind')!='file' and not (is_image and self.config.get('ocrImages')):continue
            asset=node.get('asset') or {};filename=asset.get('filename') or node.get('media',{}).get('filename') or ''
            ext=Path(asset.get('path','') if is_image else filename).suffix.lower();status=asset.get('status')
            file_status='not_loaded' if not asset else 'preview_only' if asset.get('previewOnly') else 'ready' if status=='ready' else 'missing'
            value={'schemaVersion':1,'filename':filename,'format':ext,'fileStatus':file_status,
                   'extractionStatus':'not_requested' if not copy_media else 'original_unavailable',
                   'text':'','sections':[],'characters':0,'truncated':False,'notices':[],
                   'source':{'chatId':row['chatId'],'chatName':row['chatName'],'rootMessageId':row['id'],
                             'rootSenderName':row.get('senderName',''),'rootSenderId':row.get('senderId',''),
                             'rootTime':row.get('time',''),'serverId':row.get('serverId',''),
                             'senderName':node.get('senderName') or row.get('senderName',''),
                             'senderId':node.get('senderId') or row.get('senderId',''),
                             'time':node.get('time') or row.get('time',''),'part':node.get('part',''),
                             'context':list(trail),'provenance':deepcopy(row.get('provenance',{}))}}
            preview_ocr=is_image and file_status=='preview_only' and status=='ready' and self.config.get('ocrImages')
            if copy_media and (file_status=='ready' or preview_ocr):
                value['extractionStatus']='unsupported'
                try:
                    relative=asset.get('path','');path=(destination/relative).resolve()
                    if not relative.startswith('assets/') or not path.is_relative_to((destination/'assets').resolve()) or not path.is_file():raise ValueError('invalid_asset')
                    with path.open('rb') as stream:digest=hashlib.file_digest(stream,'sha256').hexdigest()
                    if path.stat().st_size!=asset.get('size') or digest!=asset.get('digest'):raise ValueError('changed_asset')
                    if path.stat().st_size>MAX_FILE:value.update(extractionStatus='too_large',notices=['正文提取限20MB，原文件已保留。'])
                    else:
                        detected=detect_format(path,ext)
                        value.update(declaredFormat=ext,format=detected,formatSource='content_signature' if detected!=ext else 'signature_or_filename')
                        ext=detected
                        use_ocr=bool(is_image or self.config.get('ocrEnabled'))
                        password=self.passwords.get(digest)
                        cache_key=(digest,ext,use_ocr,password)
                        if ext in IMAGE_FORMATS and not use_ocr:
                            value.update(extractionStatus='not_requested',notices=['这是图片附件；启用文档 OCR 后可自动识别正文。'])
                        elif ext not in SUPPORTED|IMAGE_FORMATS:value['notices']=['暂未支持此格式的正文提取，原文件保留。']
                        elif cache_key in self.cache:
                            self.cache_hits+=1;value.update(deepcopy(self.cache[cache_key]))
                        else:
                            result=self.shared_cache.get(cache_key) if self.shared_cache is not None else None
                            if result is None:
                                self.options={'password':password,'ocr':use_ocr}
                                self.extraction_runs+=1;result=self._run(path,ext)
                                if self.shared_cache is not None:self.shared_cache.put(cache_key,result)
                            else:self.cache_hits+=1;self.reused+=1
                            self.cache[cache_key]=result;value.update(deepcopy(result))
                except (OSError,ValueError):value.update(fileStatus='integrity_error',extractionStatus='failed',notices=['附件文件或完整性校验失败。'])
            if self.password_issue and value['extractionStatus']=='encrypted':value['notices'].append('密码文件无法读取或格式不正确；未记录其中内容。')
            value['sha256']=asset.get('digest','')
            value['contentKind']='image_ocr' if is_image else 'attachment'
            if preview_ocr:
                value['sourceQuality']='preview';value['requiresReview']=True
                value['notices'].append('文字来自本地预览或缩略图，原图仍未取得；小字和裁切内容可能缺失。')
                if value['text']:
                    value['extractionStatus']='partial'
                    for section in value['sections']:section['location']='预览图 · '+section['location']
                    value['text']='[预览图 OCR，原图未取得]\n'+value['text']
                    self.preview_ocr_total+=1
            value['fileStatusText']=FILE_LABELS[value['fileStatus']]
            value['extractionStatusText']=TEXT_LABELS[value['extractionStatus']]
            node['attachmentContent']=value
            if value.get('ocr',{}).get('recognizedPages'):self.ocr_total+=1
            if is_image:self.image_total+=1
            self.total+=1;self.file_counts[value['fileStatus']]+=1;self.status_counts[value['extractionStatus']]+=1;self.characters+=value['characters']
    def summary(self):return {'schemaVersion':1,'unit':'reference_occurrence','total':self.total,'imageOCRReferences':self.image_total,'ocrTextReferences':self.ocr_total,'previewOCRReferences':self.preview_ocr_total,'passwordFileIssue':self.password_issue,'fileStatuses':dict(self.file_counts),'extractionStatuses':dict(self.status_counts),'characters':self.characters,'uniqueContentsProcessed':len(self.cache),'extractionRuns':self.extraction_runs,'cacheHits':self.cache_hits,'reusedFromPreviousExports':self.reused}


def summary_text(summary):
    statuses=summary['extractionStatuses']
    reuse=('本次实际提取 '+str(summary['extractionRuns'])+' 份，跨次复用 '+str(summary.get('reusedFromPreviousExports',0))+' 份已校验正文。' if 'extractionRuns' in summary else '')
    return '附件正文（按引用）：'+('无附件。' if not summary['total'] else '、'.join(TEXT_LABELS[k]+' '+str(v)+'处' for k,v in statuses.items())+'。'+reuse+'文件取得状态与正文提取状态分别记录；OCR文字另行标注，须核对数字、单位和表格；未启用或未完成OCR的内容不作已读取处理。')
