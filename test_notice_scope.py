import json
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from checker import GOOD, ROOT
from file_audit import audit
from notice_plan import make_plan
from run import digest, load_saved, main


NOTICE = '''三、参赛材料
各赛道通用材料：
请提交项目说明书（DOCX）。
1.创意赛道：
①调研报告（PDF格式，不超过7MB，图表不超过4张）；
②作品展示（PPT格式，不超过12MB；可嵌入MP4视频）。
2.实践赛道：
①实训简历（PDF）；
②演示视频（MP4格式），不超过2分钟。
（二）平台使用：
选手在网站中修改PPT模板。'''


class TrackRegression(unittest.TestCase):
    def test_cli_requires_scope_even_if_folder_confirmed(self):
        with tempfile.TemporaryDirectory() as temp:
            folder=Path(temp);source=folder/'通知.txt';source.write_text(NOTICE,encoding='utf-8')
            cmd=[sys.executable,'-X','utf8',str(ROOT/'run.py'),'--notice',str(source),'--folder',str(GOOD),'--confirm','--output',str(folder)]
            result=subprocess.run(cmd,capture_output=True,encoding='utf-8');self.assertNotEqual(result.returncode,0)
            self.assertIn('创意赛道',result.stderr);self.assertFalse(list(folder.rglob('预检结果.json')))
            subprocess.run(cmd+['--track','实践赛道'],capture_output=True,check=True)
            plan=json.loads(next(folder.rglob('任务清单.json')).read_text(encoding='utf-8'))['plan']
            self.assertEqual(plan['track'],'实践赛道');self.assertEqual([item['label'] for item in plan['items']],['书面材料','实训简历','演示视频'])

    def test_selected_and_shared_only_original_evidence(self):
        plan=make_plan(NOTICE,'测试',track='创意赛道');items=plan['items']
        self.assertEqual([item['kind'] for item in items],['word','word','slides'])
        self.assertEqual([item['mode'] for item in items],['required']*3)
        self.assertEqual(items[1]['formats'],['pdf']);self.assertEqual([rule['metric'] for rule in items[1]['rules']],['bytes'])
        self.assertTrue(any('图表不超过4张' in note['text'] for note in items[1]['manual']))
        self.assertTrue(any('嵌入' in note['text'] for note in items[2]['manual']))
        rows=audit(plan,GOOD)
        self.assertEqual([(row['material'],row['key']) for row in rows if row['status']=='需修改'],[('调研报告','文件存在')])
        for row in rows:self.assertEqual(row['quote'],plan['source'][row['source']-1]['text'])
        with self.assertRaises(ValueError):make_plan(NOTICE,'测试',track='不存在')

    def test_optional_scope_and_common_after_tracks(self):
        notice='可选材料：\n1.甲赛道：\n①设计报告（PDF）。\n2.乙赛道：\n①项目报告（DOCX）。\n所有赛道必交材料：\n请提交视频（MP4）。'
        items=make_plan(notice,'test',track='甲赛道')['items']
        self.assertEqual([(item['formats'],item['mode']) for item in items],[(['pdf'],'optional'),(['mp4'],'required')])

    def test_embedded_format_or_size_never_applied_to_host(self):
        for suffix in ['，可插入 MP4 视频（不超过2MB）','；可嵌入视频，大小不得超过2MB']:
            items=make_plan('请提交路演方案（PPTX格式，不超过7MB）'+suffix+'。','test')['items']
            self.assertEqual([(item['kind'],item['formats']) for item in items],[('slides',['pptx'])]);self.assertEqual([r['value'] for r in items[0]['rules']],['7000000'])
        items=make_plan('请提交路演方案（PPTX），可加入视频，另须提交MP4。','test')['items']
        self.assertEqual([item['kind'] for item in items],['slides','video']);self.assertEqual(items[1]['mode'],'required')

    def test_pdf_pages_not_chart_count_or_implicit_word(self):
        plan=make_plan('请提交调研报告（PDF），至多3页，图表不超过5张。','test')
        item,=plan['items'];self.assertEqual([(r['metric'],r['value']) for r in item['rules']],[('pages','3')])
        with tempfile.TemporaryDirectory() as temp:
            folder=Path(temp);(folder/'调研报告.pdf').write_bytes(b'%PDF-1.4\n%%EOF');rows=audit(plan,folder)
            self.assertTrue(any(row['key']=='页数' and row['status']=='人工核查' for row in rows))
        self.assertFalse(make_plan('附件：下载 PDF 阅读器。','test')['items'])

    def test_inline_tracks_without_clear_boundaries_fail_closed(self):
        with self.assertRaisesRegex(ValueError,'摘录'):
            make_plan('创作赛道：请提交PPT；实践赛道：请提交视频。','test')

    def test_common_unclassified_prose_not_a_required_list_header(self):
        items=make_plan('各赛道可选材料：\n请核实个人信息。\n创作赛道：\n①书面报告（PDF）。','test')['items']
        self.assertEqual(items[0]['mode'],'optional')
        item,=make_plan('创作赛道：\n演示视频不超过50MB。','test')['items']
        self.assertEqual(item['mode'],'unspecified')

    def test_inline_host_context_does_not_create_video(self):
        items=make_plan('请提交路演方案（PPTX）。PPT中可以嵌入演示视频，须单独提交MP4。','test')['items']
        self.assertEqual([(item['kind'],item['mode']) for item in items],[('slides','required'),('video','required')])

    def test_menu_blank_track_exits_without_scanning(self):
        with tempfile.TemporaryDirectory() as temp:
            folder=Path(temp);source=folder/'通知.txt';source.write_text(NOTICE,encoding='utf-8')
            with patch('sys.argv',['run.py','--menu','--output',temp]), patch('builtins.input',side_effect=['1',str(source),'N','']), patch('builtins.print'):
                main()
            self.assertFalse(list(folder.rglob('*.json')))

    def test_old_rules_cannot_be_silently_replayed(self):
        with tempfile.TemporaryDirectory() as temp:
            path=Path(temp)/'old.json';old={'version':1};path.write_text(json.dumps({'plan':old,'checksum':digest(old)}),encoding='utf-8')
            with self.assertRaisesRegex(ValueError,'重新生成'):load_saved(path)
