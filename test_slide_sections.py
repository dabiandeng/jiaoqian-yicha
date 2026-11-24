import shutil
import tempfile
import unittest
from pathlib import Path

from checker import GOOD, ROOT
from file_audit import audit
from notice_plan import load_notice, make_plan


class SlideSectionTests(unittest.TestCase):
    def test_synthetic_notice_by_phase(self):
        source=load_notice(ROOT/'示例通知'/'分阶段路演-合成通知.txt')
        for stage,line in [('初赛报名指南',3),('复赛提交指南',None),('决赛准备指南',7)]:
            with self.subTest(stage=stage):
                plan=make_plan(source,'原文',track=stage)
                rows=[row for row in audit(plan,GOOD) if row['key']=='页数']
                if line is None:
                    self.assertEqual(rows,[]);continue
                self.assertEqual([(row['status'],row['value']) for row in rows],[('需修改','20 页')])
                self.assertEqual(plan['source'][rows[0]['source']-1]['line'],line)
                deck=next(item for item in plan['items'] if any(rule['metric']=='pages' for rule in item['rules']))
                self.assertTrue(any('动画' in note['text'] for note in deck['manual']))
                if stage=='决赛准备指南':
                    self.assertEqual([r['value'] for r in deck['rules'] if r['metric']=='bytes'],['10000000'])

    def test_inclusive_limit_and_ambiguous_files(self):
        for cap,status in [(19,'需修改'),(20,'通过'),(21,'通过')]:
            plan=make_plan(f'PPT要求：\nPPT最多包含{cap}页。','页数边界')
            self.assertEqual([row['status'] for row in audit(plan,GOOD) if row['key']=='页数'],[status])
        with tempfile.TemporaryDirectory() as temp:
            folder=Path(temp)
            for name in ('甲.pptx','乙.pptx'):shutil.copyfile(GOOD/'项目方案.pptx',folder/name)
            rows=audit(plan,folder);self.assertFalse(any(row['key']=='页数' for row in rows));self.assertTrue(any(row['key']=='文件匹配' for row in rows))

    def test_sections_do_not_merge_distinct_decks_or_soft_limits(self):
        text='PPT要求：\nPPT建议最多包含5页。\n请另行提交答辩稿（PPTX），不超过12页。'
        plan=make_plan(text,'分别交件');decks=[item for item in plan['items'] if item['kind']=='slides']
        self.assertEqual(len(decks),2);self.assertFalse(decks[0]['rules']);self.assertEqual([r['value'] for r in decks[1]['rules']],['12'])
        plan=make_plan('PPT要求：\nPPT最多包含5页（不含封面）。','口径不明');self.assertFalse(plan['items'][0]['rules'])
