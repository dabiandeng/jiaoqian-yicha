import json
import shutil
import subprocess
import sys
import tempfile
import unittest
import zipfile
from pathlib import Path
from unittest.mock import patch

from checker import GOOD, ROOT, SOURCE
from fixture_tools import fault_sample
from file_audit import audit
from notice_plan import load_notice, make_plan, source_parts
from run import EXAMPLE, demote, load_saved, save_plan


def plan(text): return make_plan(text,'测试规则')


class GenericContest(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        temp=tempfile.TemporaryDirectory();cls.addClassCleanup(temp.cleanup);cls.bad=fault_sample(temp.name)
    def setUp(self):
        self.tmp=tempfile.TemporaryDirectory();self.addCleanup(self.tmp.cleanup);self.folder=Path(self.tmp.name)
        self.school=plan(load_notice(SOURCE));self.alternate=plan(load_notice(EXAMPLE))

    def test_same_folder_different_contests(self):
        errors=lambda notice:{r['key'] for r in audit(notice,GOOD) if r['status']=='需修改'}
        self.assertEqual(errors(self.school),set());self.assertEqual(errors(self.alternate),{'页数','时长'});self.assertEqual({i['kind'] for i in self.alternate['items']},{'slides','video'})

    def test_realistic_missing_word_changes_by_notice(self):
        school=audit(self.school,self.folder);alternate=audit(self.alternate,self.folder)
        self.assertEqual(sum(r['status']=='需修改' for r in school),3);self.assertEqual(sum(r['status']=='需修改' for r in alternate),2)

    def test_old_control_case(self):
        self.assertEqual(sorted(r['key'] for r in audit(self.school,self.bad) if r['status']=='需修改'),sorted(['大小','页数']))

    def test_word_size_and_pages_separate(self):
        notice=plan('请提交以下材料：\n项目报告（DOCX格式）：不超过 1 KB，最多 3 页。');rows=audit(notice,GOOD)
        self.assertEqual(next(r['status'] for r in rows if r['key']=='大小'),'需修改');self.assertEqual(next(r['status'] for r in rows if r['key']=='页数'),'人工核查')

    def test_optional_and_exempt(self):
        notice=plan('提交材料：\n可选视频（MP4格式）：不超过 2 MB。\nWord 说明书无需单独提交。');rows=audit(notice,self.folder)
        self.assertEqual([r['status'] for r in rows[:2]],['可选','免交']);self.assertFalse(any(r['status']=='需修改' for r in rows))

    def test_only_rules_actually_written(self):
        notice=plan('请提交项目视频（MP4 格式），须配中文字幕。');rows=audit(notice,self.bad)
        self.assertFalse(any(r['key'] in ('页数','时长','大小') for r in rows));self.assertTrue(any('中文字幕' in r['value'] and r['status']=='人工核查' for r in rows))

    def test_unsupported_or_unrecognized_prose_retained(self):
        src='请提交以下材料：\n路演方案：PPTX，使用蓝色背景。\n所有人须在周三签到。';notice=plan(src);rows=audit(notice,GOOD)
        self.assertTrue(any('蓝色' in r['value'] and r['status']=='人工核查' for r in rows));self.assertTrue(any('周三' in r['quote'] and r['status']=='人工核查' for r in rows))

    def test_pdf_option_is_not_wrong_format(self):
        (self.folder/'路演方案.pdf').write_bytes(b'%PDF-1.4\nsynthetic signature fixture\n%%EOF');rows=audit(self.alternate,self.folder)
        self.assertTrue(any(r['key']=='文件格式' and r['status']=='通过' for r in rows));self.assertTrue(any(r['key']=='页数' and r['status']=='人工核查' for r in rows))

    def test_wrong_format_and_spoof(self):
        notice=plan('必交材料：\n路演方案：仅接受 PDF，最多 15 页。');rows=audit(notice,GOOD);self.assertTrue(any(r['key']=='文件格式' and r['status']=='需修改' for r in rows))
        (self.folder/'文档.pdf').write_bytes(b'not a PDF');rows=audit(notice,self.folder);self.assertTrue(any(r['key']=='文件格式' and r['status']=='人工核查' for r in rows))

    def test_duplicate_and_roles_sharing_extension(self):
        notice=plan('必交材料：\n技术说明书：DOCX。\n使用说明书：DOCX。'); rows=audit(notice,GOOD);self.assertEqual(sum(r['key']=='文件匹配' for r in rows),2)
        for name in ['a.pptx','b.pptx']:shutil.copyfile(GOOD/'项目方案.pptx',self.folder/name)
        self.assertTrue(any(r['key']=='文件匹配' for r in audit(self.school,self.folder)))

    def test_word_cache_not_trusted(self):
        with zipfile.ZipFile(self.folder/'w.docx','w') as z:
            z.writestr('word/document.xml','<w:document xmlns:w="http://schemas.openxmlformats.org/wordprocessingml/2006/main"><w:body/></w:document>');z.writestr('docProps/app.xml','<Properties><Pages>2</Pages></Properties>')
        rows=audit(plan('请提交说明书（Word 格式），最多 3 页。'),self.folder);self.assertTrue(any(r['key']=='页数' and r['status']=='人工核查' for r in rows))

    def test_exact_numeric_boundaries_and_units(self):
        notice=plan('请提交视频（MP4格式），时长须短于288秒，文件大小不超过 0.6 MiB。');rows=audit(notice,GOOD)
        self.assertEqual(next(r['status'] for r in rows if r['key']=='时长'),'需修改');self.assertEqual(next(r['status'] for r in rows if r['key']=='大小'),'通过')
        for txt,expected in [('不超过二十页',[('<=','20')]),('十至二十页',[('>=','10'),('<=','20')]),('至少二十一页',[('>=','21')])]:
            rules=plan('请提交路演方案（PPTX），'+txt+'。')['items'][0]['rules'];self.assertEqual([(r['op'],r['value']) for r in rules],expected)

    def test_soft_conditions_not_hard_limits(self):
        for clause in ['建议不超过15页','不超过15页（不含封面）','各文件合计不超过10MB']:
            notice=plan('必交材料：\n路演方案：PPTX，'+clause+'。');self.assertFalse(notice['items'][0]['rules']);self.assertTrue(notice['items'][0]['manual'])

    def test_conflict_and_ambiguous_obligation(self):
        notice=plan('请提交视频（MP4格式），最多3分钟，不超过2分钟。');self.assertFalse(notice['items'][0]['rules']);self.assertTrue(notice['items'][0]['manual'])
        rows=audit(plan('演示视频不超过50MB，须配中文字幕。'),self.folder);self.assertFalse(any(r['status']=='需修改' for r in rows))

    def test_optional_section_and_qualitative_only(self):
        notice=plan('可选材料：\n演示视频：MP4。\nWord说明书：DOCX。');self.assertEqual([i['mode'] for i in notice['items']],['optional','optional'])
        self.assertFalse(plan('作品原创，不得抄袭。')['items'])

    def test_shared_simple_list_and_split_inline(self):
        self.assertEqual({i['kind'] for i in plan('请提交PPT、Word和视频。')['items']},{'slides','word','video'})
        notice=plan('提交材料：\nPPT不超过20页，演示视频不超过100MB。');self.assertEqual([[r['metric'] for r in i['rules']] for i in notice['items']],[['pages'],['bytes']])

    def test_scoring_section_not_deliverables(self):
        notice=plan('三、作品要求\n请提交以下材料：\n项目文档：DOCX。\n四、评审说明\nPPT格式和风格统一，排版美观。');self.assertEqual(len(notice['items']),1)

    def test_audit_evidence_binds_to_notice(self):
        for notice in [self.school,self.alternate]:
            for row in audit(notice,GOOD):self.assertEqual(row['quote'],notice['source'][row['source']-1]['text'])

    def test_manual_override_and_plan_replay(self):
        demote(self.alternate,'1');save_plan(self.alternate,self.folder);saved=load_saved(self.folder/'任务清单.json');self.assertEqual(audit(saved,GOOD),audit(self.alternate,GOOD));self.assertFalse(any(r['key']=='页数' for r in audit(saved,GOOD)))
        file=self.folder/'任务清单.json';data=json.loads(file.read_text(encoding='utf-8'));data['plan']['title']='modified';file.write_text(json.dumps(data),encoding='utf-8')
        with self.assertRaises(ValueError):load_saved(file)

    def test_docx_notice_table_and_utf8(self):
        path=self.folder/'通知.docx'; ns='http://schemas.openxmlformats.org/wordprocessingml/2006/main';cell=lambda txt:f'<w:tc><w:p><w:r><w:t>{txt}</w:t></w:r></w:p></w:tc>'
        with zipfile.ZipFile(path,'w') as z:z.writestr('word/document.xml',f'<w:document xmlns:w="{ns}"><w:body><w:p><w:r><w:t>必交材料：</w:t></w:r></w:p><w:tbl><w:tr>{cell("演示视频")}{cell("MP4")}{cell("不超过 1 MB")}</w:tr></w:tbl></w:body></w:document>')
        notice=plan(load_notice(path));self.assertEqual(notice['items'][0]['formats'],['mp4']);self.assertEqual(notice['items'][0]['rules'][0]['value'],'1000000')

    def test_no_cloud_or_silent_ai_fallback(self):
        with patch('notice_plan.ai_hints',side_effect=OSError('offline')):
            with self.assertRaises(OSError):make_plan('请提交MP4。','示例',True)
            self.assertEqual(make_plan('请提交MP4。','示例')['method'],'离线条款解析')

    def test_cli_confirm_gate(self):
        base=[sys.executable,'-X','utf8',str(ROOT/'run.py'),'--notice',str(EXAMPLE),'--folder',str(GOOD),'--output',str(self.folder)]
        self.assertNotEqual(subprocess.run(base,capture_output=True).returncode,0);self.assertFalse(list(self.folder.rglob('预检结果.json')))
        self.assertEqual(subprocess.run(base+['--confirm'],capture_output=True).returncode,0);self.assertEqual(len(list(self.folder.rglob('预检结果.json'))),1)

    def test_unsupported_but_allowed_extension(self):
        notice=plan('请提交参展视频，格式为 MP4 或 FLV，最多 1 MB。');(self.folder/'短片.flv').write_bytes(b'fixture')
        rows=audit(notice,self.folder);self.assertFalse(any(r['status']=='需修改' for r in rows));self.assertTrue(any(r['key']=='文件格式' and r['status']=='人工核查' for r in rows))

    def test_unrelated_file_does_not_hide_missing(self):
        (self.folder/'README.txt').write_text('demo',encoding='utf-8');self.assertEqual(sum(r['status']=='需修改' for r in audit(self.school,self.folder)),3)

    def test_impossible_intervals_go_to_human(self):
        notice=plan('请提交路演方案（PPTX），下限为21页，限20页。');self.assertFalse(notice['items'][0]['rules']);self.assertTrue(notice['items'][0]['manual'])


if __name__=='__main__':unittest.main()
