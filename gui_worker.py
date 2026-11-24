import contextlib
import io
import json
import http.client
import os
import re
import sys
import xml.etree.ElementTree as ET
import zipfile
from pathlib import Path

from cloud_api import CloudClient
from checker import MODEL
from dsh_review import bridge, candidates, review, select
from notice_plan import CloudHintError, load_notice, make_plan, source_parts
from notice_scope import track_names
from run import OUTPUT, demo, demote, digest, load_saved, new_dir, plan_title, save_audit, save_plan, save_review, summary


def client(request):
    return CloudClient(custom=request.get('custom') is True,base=request.get('base',''),model=request.get('model',''),key=request.get('key'),use_env=False)


def connections():
    local=MODEL+' · Ollama 未连接'
    connection=http.client.HTTPConnection('127.0.0.1',11434,timeout=3)
    try:
        connection.request('GET','/api/tags'); response=connection.getresponse()
        if response.status==200:
            installed=any(item.get('name')==MODEL for item in json.loads(response.read(512000))['models'])
            local=MODEL+(' · 可用' if installed else ' · 未安装该型号')
    except (OSError,http.client.HTTPException,ValueError,KeyError,TypeError,AttributeError):pass
    finally:connection.close()
    route='跟随 DSH 默认模型'
    profile=Path(os.getenv('DSH_HOME') or Path.home()/'.dsh')/'profiles'/'desktop'/'cordis.patch.yml'
    try:
        block=re.search(r'(?ms)^- id: agent-default-model\s*$\n(.*?)(?=^- |\Z)',profile.read_text(encoding='utf-8'))
        if block:
            fields=dict(re.findall(r'''(?m)^    (provider|model):\s*["']?([\w./:-]+)["']?\s*$''',block[1]))
            if fields.get('model'):route='默认配置：'+fields.get('provider','DSH')+' / '+fields['model']
    except (OSError,UnicodeError):pass
    try:bridge('dsh_status');status='桥已连接'
    except ValueError:status='桥未连接'
    return {'local':local,'dsh':status+' · '+route}


def directory_for(request, output):
    directory = Path(request['directory']).resolve()
    if directory.parent != output.resolve():
        raise ValueError('请先在本窗口生成清单。')
    return directory


def overview(plan, directory, rows=()):
    return {'directory': str(directory), 'title': plan_title(plan), 'method': plan['method'],
            'planFile': str(directory/'任务清单.html'), 'remaining': len(plan['other']),
            'items': [{'label': item['label'], 'rule': summary(item), 'quote': item['basis']['text'],
                       'source': item['basis']['id'], 'manual': item['mode']=='review'} for item in plan['items']],
            'options': list(candidates(plan,rows).values())}


def dispatch(request, output=OUTPUT):
    action = request['action']
    if action=='connections':return connections()
    if action=='test_api':
        api=client(request)
        raw=api.chat('只返回一个 JSON 对象，原样返回输入中的 probe 字段。',{'probe':'ok'})
        try:valid=json.loads(raw)=={'probe':'ok'}
        except ValueError:valid=False
        if not valid:raise ValueError('已收到回复，但模型未按要求返回 JSON；请核对模型或接口。')
        return {'model':api.model,'reported':api.reply_model}
    if action == 'read':
        path = request.get('path'); text = load_notice(path) if path else request['text']
        source_parts(text)
        return {'text':text,'title':Path(path).stem if path else '粘贴的比赛通知','tracks':track_names(text)}
    if action == 'plan':
        text=request['text']; title=request['title']; track=request.get('track'); warning=''
        local=request.get('local') is True
        if local and request.get('cloud') is True:raise ValueError('一次仅选择一种材料识别方式。')
        if request.get('cloud') is True and request.get('consent') is not True:raise ValueError('请先确认向所选接口发送候选通知段落。')
        try:plan=make_plan(text,title,track=track,use_ai=local,cloud=client(request) if request.get('cloud') is True else None)
        except CloudHintError as exc:
            warning='云端辅助未完成，已改用离线规则：'+str(exc);plan=make_plan(text,title,track=track)
            plan['method']='离线条款解析（云端辅助未完成）'
        directory=new_dir(output);save_plan(plan,directory)
        return dict(overview(plan,directory),warning=warning)
    if action == 'demo':
        with contextlib.redirect_stdout(io.StringIO()):report=demo(output)
        cases=[]
        for key,label in [('A','校园创意展（合成通知）'),('B','校园短片展（合成通知）')]:
            data=json.loads((report.parent/key/'预检结果.json').read_text(encoding='utf-8'))
            cases.append({'label':label,'failures':[row for row in data['results'] if row['status']=='需修改']})
        return {'cases':cases,'file':str(report)}
    directory=directory_for(request,output);plan=load_saved(directory/'任务清单.json')
    if action == 'audit':
        if request.get('confirmed') is not True:raise ValueError('请先确认任务清单。')
        folder=Path(request['folder']);
        if not request['folder'] or not folder.is_dir():raise ValueError('请选择存在的提交文件夹。')
        demote(plan,request.get('manual',''));directory=new_dir(output);save_plan(plan,directory)
        rows=save_audit(plan,folder,directory,'操作者在图形界面确认本清单')
        return dict(overview(plan,directory,rows),rows=rows,file=str(directory/'预检结果.html'))
    if action == 'review':
        if request.get('consent') is not True:raise ValueError('请先选择并确认要发送的原句。')
        rows=[]; audit_file=directory/'预检结果.json'
        if audit_file.exists():
            saved=json.loads(audit_file.read_text(encoding='utf-8'))
            if saved['plan_checksum']!=digest(plan):raise ValueError('报告与清单不一致。')
            rows=saved['results']
        options=candidates(plan,rows);selected=select(options,request['ids']);provider=request.get('provider','API')
        if provider not in ('DSH','API'):raise ValueError('未知复核服务。')
        result=review(selected) if provider=='DSH' else client(request).review(selected)
        path=save_review(plan,directory,provider,result,selected,options)
        return {'file':str(path),'items':result['items'],'model':result.get('model'),'provider':result.get('provider')}
    raise ValueError('未知操作。')


if __name__=='__main__':
    try:
        result={'ok':True,'data':dispatch(json.load(sys.stdin))}
    except (OSError,ValueError,KeyError,TypeError,ET.ParseError,zipfile.BadZipFile) as exc:
        result={'ok':False,'error':str(exc)}
    sys.stdout.write(json.dumps(result,ensure_ascii=True))
