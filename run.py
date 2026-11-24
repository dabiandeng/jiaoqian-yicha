import argparse
import hashlib
import html
import json
import os
import re
import sys
import xml.etree.ElementTree as ET
import zipfile
from datetime import datetime
from pathlib import Path

from checker import GOOD, ROOT, SOURCE
from cloud_api import CloudClient
from dsh_review import ReviewError, candidates, review, select
from file_audit import MODES, audit, display
from notice_plan import CloudHintError, KINDS, METRICS, load_notice, make_plan
from notice_scope import track_names


OUTPUT = ROOT/'通用版输出'
EXAMPLE = ROOT/'示例通知'/'校园短片展-合成通知.txt'
ESC = html.escape
STYLE = '''*{box-sizing:border-box}body{margin:0;background:#f5f5ef;color:#173632;font:16px/1.6 system-ui,"Microsoft YaHei",sans-serif}main{max-width:1200px;margin:auto;padding:34px 22px}header{background:#143a36;color:#fff;border-radius:14px;padding:24px 32px}header small{color:#a8dbc9}h1{font-size:32px;margin:5px 0}h2{font-size:22px}section,details{background:white;border-radius:10px;margin:14px 0;padding:14px 20px;overflow:auto}summary{cursor:pointer;font-weight:650}table{width:100%;border-collapse:collapse}td,th{text-align:left;border-bottom:1px solid #d5dfd8;padding:9px;vertical-align:top}th{color:#415e53}blockquote{margin:6px 0;padding-left:12px;border-left:3px solid #bbcec2;color:#436055}mark{white-space:nowrap;border-radius:4px;padding:2px 6px}.ok{background:#d8f1e7}.fail{background:#ffddd0}.manual{background:#fff0c6}.skip{background:#e5eaed}footer{color:#53685e}code{font-weight:600}'''


def dump(path,data): path.write_text(json.dumps(data,ensure_ascii=False,indent=2),encoding='utf-8')


def digest(data): return hashlib.sha256(json.dumps(data,ensure_ascii=False,sort_keys=True,separators=(',',':')).encode()).hexdigest()


def load_saved(path):
    envelope=json.loads(path.read_text(encoding='utf-8')); plan=envelope['plan']
    if envelope.get('checksum')!=digest(plan) or plan.get('version')!=2: raise ValueError('清单已修改或使用旧的解析规则，请从原通知重新生成。')
    return plan


def document(path,title,description,content):
    path.write_text('<!doctype html><html lang="zh-CN"><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1"><title>'+ESC(title)+'</title><style>'+STYLE+'</style><main><header><small>交前一查 / 通用比赛任务检查单</small><h1>'+ESC(title)+'</h1><div>'+ESC(description)+'</div></header>'+content+'<footer>只读检查。MB 按十进制字节，MiB 按二进制；客观检查通过不表示作品或整份材料合格。</footer></main></html>',encoding='utf-8')


def summary(item):
    if item['mode']=='review': return '待人工确认：不执行该材料的自动判定。下方保留原文和候选条件。'
    formats=' / '.join(x.upper() for x in item['formats']) or '未提取到明确格式'
    checks='；'.join(dict.fromkeys(METRICS[r['metric']]+' '+r['op']+' '+display(r['metric'],r['value']) for r in item['rules'])) or '无可自动判断的数值限制'
    return f"{MODES[item['mode']]} · {formats} · {checks}"


def plan_view(plan):
    blocks=[]
    for pos,item in enumerate(plan['items'],1):
        notes=''.join('<li>人工查看：'+ESC(e['text'])+'</li>' for e in item['manual']); rules=''.join(f"<li>{ESC(METRICS[r['metric']])}：<code>{ESC(r['quote'])}</code>〔#{r['source']}〕</li>" for r in item['rules']); context=item['context']
        cite=lambda part:f"<blockquote>#{part['id']} · 原文第 {part['line']} 行　{ESC(part['text'])}</blockquote>"
        evidence=('清单上下文'+cite(context)) if context else ''
        evidence+=''.join(cite(part) for part in item['parts'])
        blocks.append(f"<section><h2>{pos}. {ESC(item['label'])}</h2><b>{ESC(summary(item))}</b><ul>{rules}{notes}</ul><details><summary>查看本项通知原文</summary>{evidence}</details></section>")
    if not blocks: blocks.append('<section>没有识别到能够自动归属的材料。请人工核对原文，必要时只导入交件条款。</section>')
    remaining=''.join(f"<li>#{p['id']}　{ESC(p['text'])}</li>" for p in plan['other'])
    if remaining: blocks.append(f"<details><summary>其余原文：待人工筛查（{len(plan['other'])} 段，不算缺件）</summary><ol>{remaining}</ol></details>")
    if plan.get('excluded'):
        excluded=''.join(f"<li>#{part['id']}　{ESC(part['text'])}</li>" for part in plan['excluded'])
        blocks.append('<details><summary>其他赛道原文（不参与本次检查）</summary><ol>'+excluded+'</ol></details>')
    return ''.join(blocks)


def report_view(rows):
    def table(items):
        body=[]
        for row in items:
            css={'需修改':'fail','通过':'ok','人工核查':'manual'}.get(row['status'],'skip')
            body.append('<tr><td>'+ESC(row['material'])+'<br>'+ESC(row['key'])+'</td><td><mark class="'+css+'">'+ESC(row['status'])+'</mark></td><td>'+ESC(row['value'])+'</td><td>'+ESC(row['action'])+f"<details><summary>依据 #{row['source']}</summary>"+ESC(row['quote'])+'</details></td></tr>')
        return '<table><thead><tr><th>材料 / 检查项</th><th>判定</th><th>读数或要求</th><th>说明与原文</th></tr></thead><tbody>'+''.join(body)+'</tbody></table>'
    direct=[r for r in rows if r['material']!='通知其余原文']; remaining=[r for r in rows if r['material']=='通知其余原文']; failures=sum(r['status']=='需修改' for r in direct); manual=sum(r['status']=='人工核查' for r in direct)
    return f'<section><h2>{failures} 项需修改 · {manual} 项待人工查看</h2>'+table(sorted(direct,key=lambda r:r['status']!='需修改'))+'</section>'+(f'<details><summary>另有 {len(remaining)} 段通知原文未自动判定</summary>'+table(remaining)+'</details>' if remaining else '')


def new_dir(output):
    directory=output/datetime.now().strftime('%Y%m%d-%H%M%S-%f');directory.mkdir(parents=True,exist_ok=False);return directory


def save_plan(plan,directory):
    dump(directory/'任务清单.json',{'plan':plan,'checksum':digest(plan)});document(directory/'任务清单.html',plan_title(plan)+'｜任务清单',plan['method']+'。请先核对原句；发现分组错误可将该项转人工。',plan_view(plan))


def plan_title(plan):
    return plan['title']+('｜'+plan['track'] if plan.get('track') else '')


def save_audit(plan,folder,directory,confirmed):
    rows=audit(plan,folder); dump(directory/'预检结果.json',{'notice':plan['title'],'track':plan.get('track'),'plan_checksum':digest(plan),'confirmation':confirmed,'folder':str(folder.resolve()),'results':rows});document(directory/'预检结果.html',plan_title(plan)+'｜预检结果',f'{folder.name} · {plan["method"]} · {confirmed}','<p><a href="任务清单.html">查看完整任务清单与未判定原文</a></p>'+report_view(rows)); return rows


def demote(plan,numbers):
    for token in filter(None,re.split(r'[,，\s]+',numbers.strip())):
        if not token.isdecimal() or not 1<=int(token)<=len(plan['items']): raise ValueError('人工复核编号不在清单范围内。')
        plan['items'][int(token)-1]['mode']='review'


def show(plan):
    print('\n'+plan_title(plan)+'｜'+plan['method'])
    for pos,item in enumerate(plan['items'],1): print(f"{pos}. {item['label']}：{summary(item)}\n   原文：{item['basis']['text']}")
    print(f"其余原文：{len(plan['other'])} 段，保留在 HTML 供人工查看。")


def demo(output,use_ai=False):
    directory=new_dir(output); sections=[]; results={}
    for key,path in [('A · 校园创意展（合成）',SOURCE),('B · 校园短片展（合成）',EXAMPLE)]:
        plan=make_plan(load_notice(path),key,use_ai); folder=directory/key[0];folder.mkdir(); save_plan(plan,folder);rows=save_audit(plan,GOOD,folder,'预设演示样例，非人工签核'); fails=[r['material']+'：'+r['key'] for r in rows if r['status']=='需修改']; results[key]=fails
        sections.append('<section><h2>'+ESC(key)+'</h2><p>'+ESC('；'.join(fails) if fails else '未发现硬性错误，仍有人工检查项')+'</p><a href="'+key[0]+'/任务清单.html">查看条款</a>　<a href="'+key[0]+'/预检结果.html">查看逐项报告</a></section>')
    document(directory/'两赛对照.html','同一文件夹，两套比赛规则',f'两次均使用“{GOOD.name}”；A、B 均为合成测试通知。',''.join(sections)); print(json.dumps(results,ensure_ascii=False,indent=2));print(directory/'两赛对照.html'); return directory/'两赛对照.html'


def path_input(prompt): return Path(input(prompt).strip().strip('"'))


def optional_review(plan, rows, directory, numbers=None, interactive=False, cloud=None, teacher=False):
    provider='API' if cloud else 'DSH'
    options=candidates(plan,rows)
    if not options:
        print('没有待复核原句，不调用模型。');return True
    if numbers is None and interactive:
        local_option='' if teacher else 'Y = 已安装的 DSH；'
        choice=input('需要 AI 拆解人工条款？A = DeepSeek API；'+local_option+'回车不调用：').strip().upper()
        if choice=='A':cloud=cloud or CloudClient(interactive=True);provider='API'
        elif teacher or choice!='Y':return True
        else:cloud=None;provider='DSH'
    print('\n'+('DeepSeek 官方 API（按你的账户计费）' if cloud else 'DSH 纯聊天会话')+'：只发送所选原句与检查项类别；不发送作品、目录、文件名或读数。')
    for key,item in options.items():print(f"#{key}　{item['quote']}　〔{'；'.join(item['focus'])}〕")
    if numbers is None:
        numbers=input('输入同意发送的原文编号（如 4,6）；直接回车不发送：').strip() if interactive else ''
    if not numbers:
        print('未发送。命令行可用 --'+provider.lower()+'-review 4,6 明确选择。');return True
    try:
        selected=select(options,numbers)
        print('正在请求'+provider+'复核建议……',flush=True)
        result=cloud.review(selected) if cloud else review(selected)
    except ReviewError as exc:
        print(provider+' 复核未完成：',exc,'本地清单和预检结果不受影响。',file=sys.stderr);return False
    target=save_review(plan,directory,provider,result,selected,options)
    print(provider+' 复核建议：',target)
    if interactive:os.startfile(target)
    return True


def save_review(plan,directory,provider,result,selected,options):
    dump(directory/(provider+'复核建议.json'),dict(result,plan_checksum=digest(plan),selected=selected))
    blocks=[]
    for item in result['items']:
        key=item['id']
        blocks.append(f"<section><h2>原文 #{key} · 待人工</h2><blockquote>{ESC(options[key]['quote'])}</blockquote><p>引用：{ESC(item['evidence'])}</p><p><b>建议你这样查：</b>{ESC(item['action'])}</p></section>")
    target=directory/(provider+'复核建议.html')
    links='<p><a href="任务清单.html">本地任务清单</a></p>'
    if (directory/'预检结果.html').exists():links+='<p><a href="预检结果.html">本地预检结果（判定未改变）</a></p>'
    document(target,provider+' 条款复核建议','模型未查看作品。以下是人工操作建议，不是通过证明。来源：'+result['title'],links+''.join(blocks))
    return target


def main():
    parser=argparse.ArgumentParser(description='从比赛通知生成任务清单，并按确认后的规则只读检查本地文件夹。');group=parser.add_mutually_exclusive_group();group.add_argument('--notice',type=Path);group.add_argument('--plan',type=Path);group.add_argument('--demo',action='store_true');parser.add_argument('--folder',type=Path);parser.add_argument('--output',type=Path,default=OUTPUT);parser.add_argument('--ai',action='store_true',help='本地 Ollama 辅助识别材料，无云端调用');parser.add_argument('--confirm',action='store_true',help='确认采用生成或读入的清单');parser.add_argument('--manual',default='',help='将指定材料编号转人工，逗号分隔');parser.add_argument('--menu',action='store_true')
    parser.add_argument('--dsh-review',nargs='?',const='',metavar='原文编号',help='不带编号仅预览；给出 4,6 等编号即同意把这些原句发送给 DSH 配置的模型，不发送作品')
    parser.add_argument('--api-ai',action='store_true',help='同意将通知候选段落发送至 DeepSeek 官方 API 辅助识别（可能计费）')
    parser.add_argument('--teacher',action='store_true',help='免安装评审菜单，只显示离线和可选云 API')
    parser.add_argument('--api-review',nargs='?',const='',metavar='原文编号',help='不带编号仅预览；提供编号即同意发往 DeepSeek 官方 API')
    parser.add_argument('--track',metavar='赛道名',help='通知有多个独立赛道标题时必须选择一个，例如 成长赛道')
    args=parser.parse_args(); menu=args.menu or not any((args.notice,args.plan,args.demo));source=None;title=None
    if args.api_ai and (args.ai or args.plan or args.demo):parser.error('--api-ai 仅用于新通知，不能与本机模型或演示并用。')
    if args.api_review is not None and (args.dsh_review is not None or args.demo):parser.error('API 复核不能与 DSH 复核或演示并用。')
    cloud=CloudClient(interactive=menu);cloud_failed=False
    if args.teacher and (args.ai or args.dsh_review is not None):parser.error('评审版无需本地 AI 服务，请使用离线或 API 模式。')
    if args.demo and args.dsh_review is not None:parser.error('演示模式不调用 DSH，请用 --notice 或 --plan。')
    if menu:
        print('交前一查｜通用比赛任务检查单\n1  导入比赛通知 → 生成清单 → 可选检查目录\n2  复用先前生成的任务清单\n3  同一文件夹、两场比赛的验证演示')
        choice=input('选择 [1]：').strip() or '1'
        if choice=='3': os.startfile(demo(args.output));return
        if choice=='2': args.plan=path_input('任务清单.json 路径：')
        elif choice=='1':
            val=input('拖入通知 TXT / MD / DOCX，或输入 P 后粘贴正文：').strip().strip('"')
            if val.casefold()=='p':
                print('粘贴多行正文，另起一行输入 END 结束。');lines=[]
                while True:
                    line=input()
                    if line=='END': break
                    lines.append(line)
                source='\n'.join(lines);title='粘贴的比赛通知'
            elif val:args.notice=Path(val)
            else: return
            local_option='' if args.teacher else '；Y = 已安装的 Ollama'
            choice=input('材料识别：回车/N = 离线；A = 同意把候选通知段落（无赛道时可能是全文）发给 DeepSeek 官方 API，可能计费'+local_option+'：').strip().upper()
            args.ai=not args.teacher and choice=='Y';args.api_ai=choice=='A'
        else: raise ValueError('未知选择')
    if args.track and (args.demo or args.plan):parser.error('--track 仅用于从通知新建清单；复用清单时赛道已固定。')
    if args.demo: demo(args.output,args.ai);return
    if args.plan: plan=load_saved(args.plan)
    else:
        if source is None: source=load_notice(args.notice);title=args.notice.stem
        tracks=track_names(source)
        if menu and len(tracks)>1 and not args.track:
            for index,name in enumerate(tracks,1):print(f'{index}. {name}')
            answer=input('请选择本次检查的赛道编号或完整名称（回车退出，不扫描）：').strip()
            if not answer:return
            args.track=tracks[int(answer)-1] if answer.isdecimal() and 1<=int(answer)<=len(tracks) else answer
        try:plan=make_plan(source,title,args.ai,track=args.track,cloud=cloud if args.api_ai else None)
        except CloudHintError as exc:
            print('云端辅助未完成：',exc,'改用离线规则继续，不重发请求。',file=sys.stderr);cloud_failed=True
            plan=make_plan(source,title,track=args.track);plan['method']='离线条款解析（云端辅助未完成）'
    demote(plan,args.manual);directory=new_dir(args.output);save_plan(plan,directory);show(plan); print('清单：',directory/'任务清单.html')
    if menu:
        os.startfile(directory/'任务清单.html'); numbers=input('需要整项转人工？输入编号（逗号分隔），回车保留：');demote(plan,numbers);save_plan(plan,directory)
        folder=input('拖入待检目录；直接回车只生成清单：').strip().strip('"')
        if folder:
            args.folder=Path(folder);args.confirm=input('已查看清单，按以上规则检查？输入 Y：').strip().upper()=='Y'
            if not args.confirm:print('未确认，只保留清单。');args.folder=None
    rows=[]
    if args.folder:
        if not args.confirm: raise ValueError('只生成了清单，未扫描文件。确认后请用 --plan 任务清单.json --folder 目录 --confirm。')
        rows=save_audit(plan,args.folder,directory,'操作者确认使用本清单');print('需修改：',sum(r['status']=='需修改' for r in rows),'项；报告：',directory/'预检结果.html')
        if menu:os.startfile(directory/'预检结果.html')
    if menu or args.dsh_review is not None or args.api_review is not None:
        numbers=args.api_review if args.api_review is not None else args.dsh_review
        client=cloud if args.api_review is not None or (menu and numbers is None) else None
        if not optional_review(plan,rows,directory,numbers,menu,client,args.teacher) and not menu:sys.exit(2)
    if cloud_failed and not menu:sys.exit(2)


if __name__=='__main__':
    try:main()
    except (OSError,ValueError,KeyError,EOFError,ET.ParseError,zipfile.BadZipFile) as exc:
        print('未完成：',exc,'\n如果本机模型不可用，可明确改选离线模式。',file=sys.stderr);sys.exit(1)
