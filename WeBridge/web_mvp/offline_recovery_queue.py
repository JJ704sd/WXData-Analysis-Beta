"""One original per recovery task, retaining every reference count and sample locations."""
import csv
import json
import re
from offline_images import message_nodes

CSV_FILE='原件补齐优先清单.csv'
JSONL_FILE='original-recovery-queue.jsonl'


class RecoveryQueue:
    def __init__(self):self.items={}

    def add(self,row):
        for node,trail,path in message_nodes(row):
            kind=node.get('kind');asset=node.get('asset') or {};media=node.get('media') or {}
            if kind not in ('file','image','video'):continue
            if asset.get('status')=='ready' and not asset.get('previewOnly'):continue
            digest=str(media.get('md5') or '').lower()
            verified_digest=bool(re.fullmatch('[a-f0-9]{32}',digest))
            key=(row.get('client',''),digest) if verified_digest else (row['chatId'],row['id'],str(path))
            item=self.items.setdefault(key,{'md5':digest if verified_digest else '', 'filenames':[], 'kinds':[],
                'references':0,'groups':{},'firstTime':'','lastTime':'','locations':[],'previewReferences':0})
            item['references']+=1;item['previewReferences']+=bool(asset.get('previewOnly'))
            filename=asset.get('filename') or media.get('filename','')
            if filename and filename not in item['filenames']:item['filenames'].append(filename)
            if kind not in item['kinds']:item['kinds'].append(kind)
            item['groups'][row['chatId']]=row.get('chatName',row['chatId'])
            time=str(row.get('time') or '')
            if time:
                item['firstTime']=min(item['firstTime'] or time,time);item['lastTime']=max(item['lastTime'],time)
            if len(item['locations'])<10:
                item['locations'].append({'chatId':row['chatId'],'chatName':row.get('chatName',''),
                    'messageId':row['id'],'time':time,'nodePath':list(path),'context':list(trail)})

    def write(self,root):
        ordered=sorted(self.items.values(),key=lambda item:('file' not in item['kinds'],-item['references'],item['md5']))
        with (root/CSV_FILE).open('x',encoding='utf-8-sig',newline='') as stream,(root/JSONL_FILE).open('x',encoding='utf8') as records:
            writer=csv.writer(stream)
            writer.writerow(['优先级','文件名','类型','引用次数','涉及群数','首次时间','最近时间','原件MD5','已有预览次数','群名称','定位样例','操作'])
            def safe(value):
                text=str(value)
                return "'"+text if text.lstrip().startswith(('=','+','-','@')) else text
            for item in ordered:
                item={**item,'priority':'先补附件' if 'file' in item['kinds'] else '补图片/视频',
                      'matchBasis':'original_md5' if item['md5'] else 'single_occurrence_without_digest'}
                records.write(json.dumps(item,ensure_ascii=False)+'\n')
                where='；'.join(location['chatName']+' '+location['time']+' '+location['messageId'] for location in item['locations'][:3])
                writer.writerow(map(safe,[item['priority'],' / '.join(item['filenames']),'/'.join(item['kinds']),item['references'],len(item['groups']),
                    item['firstTime'],item['lastTime'],item['md5'],item['previewReferences'],' / '.join(item['groups'].values()),where,
                    '按时间在客户端打开或下载，另存到补充原件目录后重新解析；同摘要只需补一份。' if item['md5'] else '缺少原件摘要，请按消息定位人工核对；不按同名自动归并。']))
        return {'uniqueItems':len(ordered),'references':sum(i['references'] for i in ordered),
                'withoutDigest':sum(not i['md5'] for i in ordered),'csvFile':CSV_FILE,'jsonlFile':JSONL_FILE,
                'unit':'original_md5_or_unidentified_occurrence','locationSamplesPerItem':10}
