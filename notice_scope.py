import re


PREFIX = r'^\s*(?:#{1,6}\s*|[-*]\s+)?(?:(?:[（(][一二三四五六七八九十\d]+[）)]|[一二三四五六七八九十\d]+[.．、）)]|[①-⑳])\s*)?'
TRACK = re.compile(r'([\w·-]{1,22}赛道)(?:[（(]?(?:提交材料|材料要求|参赛材料)[）)]?)?[：:]?$')
SHARED = re.compile(r'^(?:(?:所有|各|全体|全部)赛道(?:均须|都需)?|通用|公共|共同)(?:通用|共同|统一|必交|选交|可选|提交|报送|要求|材料|文件|清单|部分)*[：:]?$')
MATERIAL = re.compile(r'^(?:必交|选交|可选|提交|报送|参赛|作品)?材料(?:要求|清单|如下)?[：:]?$')
NONFILE = re.compile(r'^(?:平台使用|报名方式|提交方式|赛程安排|联系方式|评审说明|评分标准|附件|大赛内容|赛道介绍|赛道设置)[：:]?$')
INLINE = re.compile(r'(?:^|[；;。\n：:])\s*(?:\d+[).）、．]\s*)?([\w·-]{1,22}赛道)\s*[：:]\s*(?=\S)')


def heading(line):
    return re.sub(PREFIX,'',line.strip()).strip()


def track_heading(line):
    text=heading(line);match=TRACK.fullmatch(text)
    return match[1] if match and not SHARED.fullmatch(text) else None


def track_names(source):
    tracks=list(dict.fromkeys(name for line in source.splitlines() if (name:=track_heading(line))))
    return tracks or list(dict.fromkeys(section for section in submission_sections(source).values()))


def submission_sections(source):
    sections={}
    for number,line in enumerate(source.splitlines(),1):
        text=heading(line)
        phase=re.fullmatch(r'((?:初赛|复赛|决赛)(?:报名|提交|准备|交件)指南)[：:]?',text)
        topic=re.match(r'^参加(主题[一二三四五六七八九十\d]+)的选手(?:共|须|需)提交',text)
        if phase or topic:sections[number]=(phase or topic)[1]
    return sections if len(set(sections.values()))>1 else {}


def line_scopes(source, selected):
    names=track_names(source)
    inline_names={name for line in source.splitlines() for name in INLINE.findall(line) if not SHARED.fullmatch(name)}
    if len(inline_names | set(names))>1 and inline_names.difference(names):
        raise ValueError('多个赛道的条款未按独立标题分段；请仅导入所选赛道的材料摘录，不混合检查。')
    if (selected is None and len(names)>1) or (selected is not None and selected not in names):
        raise ValueError('请用 --track 指定通知中的赛道：'+'、'.join(names) if names else '未识别到可选择的赛道标题，请导入该赛道的材料条款摘录。')
    if not names:return None,{}
    selected=selected or names[0]; state='manual'; scopes={}; reference=False
    sections=submission_sections(source)
    if sections and set(names)==set(sections.values()):
        for number,line in enumerate(source.splitlines(),1):
            if number in sections:state='active' if sections[number]==selected else 'excluded'
            elif re.match(r'^\s*[一二三四五六七八九十]+、',line):state='manual'
            scopes[number]=state
        return selected,scopes
    for number,line in enumerate(source.splitlines(),1):
        text=heading(line); name=track_heading(line)
        if name:
            state=('manual' if reference else 'active') if name==selected else 'excluded'
            scopes[number]='track' if state=='active' else state;continue
        if SHARED.fullmatch(text):state='common';reference=False
        elif MATERIAL.fullmatch(text):
            reference=False
            if state=='manual':state='common'
        elif (re.match(r'^\s*(?:#{1,6}\s|[一二三四五六七八九十]+、|[（(][一二三四五六七八九十]+[）)])',line) and len(text)<35 and not re.search(r'PDF|PPT|DOCX?|MP4|格式',text,re.I)
              or NONFILE.fullmatch(text)):
            state='manual';reference=bool(NONFILE.fullmatch(text))
        if len(set(INLINE.findall(line)))>1:scopes[number]='manual'
        elif state=='common' and (SHARED.fullmatch(text) or MATERIAL.fullmatch(text)):scopes[number]='common-heading'
        elif re.match(r'^(?:所有|各|全部)赛道(?:均|都)?(?:须|需|必须|应)提交',text):scopes[number]='common'
        else:scopes[number]=state
    return selected,scopes
