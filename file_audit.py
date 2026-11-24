import operator
import struct
import xml.etree.ElementTree as ET
import zipfile
from decimal import Decimal

from checker import movie_seconds, office
from notice_plan import KINDS, METRICS


FAMILY={'slides':{'ppt','pptx','pptm','ppsx','odp','key'},'word':{'doc','docx','docm','odt','wps','rtf'},'video':{'mp4','mov','avi','mkv','webm','wmv','m4v','flv','mpeg','mpg'}}
MODES={'required':'必交','optional':'可选','unspecified':'未明确必交性','review':'待确认','exempt':'免单独提交'}
COMPARE={'<':operator.lt,'<=':operator.le,'>':operator.gt,'>=':operator.ge}
ERRORS=(OSError,ValueError,KeyError,IndexError,RuntimeError,struct.error,ET.ParseError,zipfile.BadZipFile)


def properties(path):
    ext=path.suffix.lower().lstrip('.'); size=path.stat().st_size
    result={'bytes':size,'ext':ext,'valid':None,'reason':'仅识别扩展名，未验证容器'}
    try:
        if ext in ('pptx','docx'):
            count=office(path,ext=='pptx')
            if ext=='pptx': result['pages']=count
        elif ext=='mp4': result['seconds']=movie_seconds(path)
        elif ext=='pdf':
            with path.open('rb') as stream:
                if not stream.read(8).startswith(b'%PDF-'): raise ValueError('缺少 PDF 文件头')
                stream.seek(max(0,size-1024))
                if b'%%EOF' not in stream.read(): raise ValueError('缺少 PDF 文件尾')
        else: return result
        result.update(valid=True,reason='已识别主要结构或文件标记')
    except ERRORS as exc: result.update(valid=False,reason=f'无法验证文件：{type(exc).__name__}')
    return result


def display(metric,value):
    value=Decimal(str(value))
    if metric=='bytes': return f'{value:,f} 字节（{value/1000000:,.3f} MB）'
    return f"{value.normalize():f} {'页' if metric=='pages' else '秒'}"


def audit(plan,folder):
    if not folder.is_dir(): raise ValueError('找不到提交文件夹。')
    files=sorted([p for p in folder.iterdir() if p.is_file() and not p.name.startswith(('~$','.'))],key=lambda path:path.name.lower()); rows=[]; links=[]; cache={}
    claimed_formats={fmt for item in plan['items'] if item['mode'] not in ('review','exempt') for fmt in item['formats']}
    for item in plan['items']:
        if item['mode'] in ('review','exempt'): links.append([]); continue
        allow=set(item['formats']); kinds=FAMILY[item['kind']]
        supported=[p for p in files if p.suffix.lower()[1:] in (allow or kinds)]
        links.append(supported if supported else [p for p in files if p.suffix.lower()[1:] not in claimed_formats and (p.suffix.lower()[1:] in kinds or (len(item['label'])>1 and item['label'] in p.stem))])
    for idx,item in enumerate(plan['items']):
        base=item['basis']; all_sources={part['id']:part for part in item['parts']}; candidates=links[idx]; mode=item['mode']; label=item['label']; formats=item['formats']
        def add(key,status,value,why,source=base): rows.append(dict(material=label,key=key,status=status,value=value,quote=source['text'],source=source['id'],action=why))
        if mode=='exempt': add('交件义务','免交','无需单独提交','不报缺件');continue
        if mode=='review':
            for part in item['parts']: add('材料规则','人工核查',part['text'],'按原文确认条件，不自动判定',part)
            continue
        if not candidates:
            if mode=='optional': add('文件存在','可选','未发现可选材料','不用补交');continue
            if mode=='unspecified': add('文件存在','人工核查','通知未明确是否必交','不自动报缺件');continue
            if not formats and files:
                add('文件存在','人工核查','没有明确匹配；目录中可能有无法归类的材料','确认是否为其他格式或导出版本')
            else: add('文件存在','需修改','缺少必交材料','补交或核对文件与材料的对应关系')
            continue
        if len(candidates)>1 or any(candidates[0] in options for n,options in enumerate(links) if n!=idx):
            add('文件匹配','人工核查','、'.join(p.name for p in candidates),'同类多份或一份匹配多项，不能确定映射');continue
        path=candidates[0];format_source=all_sources[item['format_source']]
        if formats and path.suffix.lower()[1:] not in formats:
            add('文件格式','需修改',path.name,'候选文件不是要求的格式：'+', '.join(formats)+'；不再对该文件判定通过',format_source);continue
        add('文件存在','通过',path.name,'只表示找到了候选文件')
        if path not in cache:
            try: cache[path]=properties(path)
            except ERRORS: cache[path]={'valid':None,'reason':'文件不可读','ext':path.suffix.lower()[1:]}
        meta=cache[path]; ext=meta['ext']
        if formats:
            add('文件格式','通过' if meta['valid'] else '人工核查',path.name,meta['reason']+'；通知允许：'+', '.join(formats),format_source)
        elif meta['valid'] is False: add('文件可读性','人工核查',path.name,meta['reason'])
        for rule in item['rules']:
            metric=rule['metric']; actual=meta.get(metric); source=all_sources[rule['source']]
            if actual is None:
                reason='Word 页数须用最终版式可靠分页；不采信缓存页数' if metric=='pages' and ext in ('doc','docx') else '现有解析器无法可靠测量，请打开最终文件核对'
                add(METRICS[metric],'人工核查',path.name,reason,source);continue
            ok=COMPARE[rule['op']](Decimal(str(actual)),Decimal(rule['value']))
            add(METRICS[metric],'通过' if ok else '需修改',display(metric,actual),'要求：'+rule['quote'],source)
        for note in item['manual']: add('其他要求','人工核查',note['text'],'需人工查看，不分析内容',all_sources[note['source']])
    for part in plan['other']:
        rows.append(dict(material='通知其余原文',key='未自动判定',status='人工核查',value=part['text'],quote=part['text'],source=part['id'],action='核对是否涉及交件；不计为缺件'))
    return rows
