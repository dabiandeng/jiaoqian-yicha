import shutil
import tempfile
import unittest
from pathlib import Path

from checker import GOOD, SOURCE, builtin, inspect_pack, movie_seconds, verify_limits
from fixture_tools import fault_sample


class Preflight(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        temp=tempfile.TemporaryDirectory();cls.addClassCleanup(temp.cleanup);cls.bad=fault_sample(temp.name)
    def setUp(self): self.source=SOURCE.read_text(encoding='utf-8'); self.rules=builtin()
    def rows(self,path): return {row['key']:row for row in inspect_pack(path,self.rules,self.source)}
    def test_bad_exactly_two_hard_failures(self):
        rows=self.rows(self.bad); self.assertEqual({key for key,row in rows.items() if row['status']=='需修改'},{'PPT 页数','视频体积'}); self.assertEqual(rows['PPT 页数']['value'],'21 页')
    def test_repaired_keeps_manual_checks(self):
        rows=self.rows(GOOD); self.assertFalse(any(row['status']=='需修改' for row in rows.values())); self.assertEqual(rows['PPT 页数']['value'],'20 页'); self.assertEqual(rows['解说与内容质量']['status'],'人工核查')
    def test_times_identical(self):
        for folder in [self.bad,GOOD]: self.assertEqual(movie_seconds(folder/'项目演示.mp4'),288)
    def test_optional(self): self.assertEqual(self.rows(GOOD)['代码仓库']['status'],'可选')
    def test_missing_word(self):
        with tempfile.TemporaryDirectory() as temp: self.assertEqual(self.rows(Path(temp))['项目文档']['status'],'需修改')
    def test_unreadable(self):
        with tempfile.TemporaryDirectory() as temp:
            root=Path(temp); (root/'断裂.pptx').write_bytes(b'incomplete'); (root/'断裂.mp4').write_bytes(b'not a movie'); rows=self.rows(root)
            for key in ('项目方案','项目演示'): self.assertEqual(rows[key]['status'],'人工核查')
            self.assertNotIn('PPT 页数',rows)
    def test_duplicate(self):
        with tempfile.TemporaryDirectory() as temp:
            for filename in ['a.pptx','b.pptx']: shutil.copyfile(GOOD/'项目方案.pptx',Path(temp)/filename)
            self.assertEqual(self.rows(Path(temp))['项目方案']['status'],'人工核查')
    def test_old_format_not_missing(self):
        with tempfile.TemporaryDirectory() as temp:
            (Path(temp)/'old.ppt').touch(); self.assertEqual(self.rows(Path(temp))['项目方案']['status'],'人工核查')
    def test_repeatability(self): self.assertEqual(self.rows(self.bad),self.rows(self.bad))
    def test_reject_invented_evidence(self):
        self.rules['slides']['quote']='30页以内'
        with self.assertRaises(ValueError): verify_limits(self.source,self.rules)
    def test_reject_wrong_conversion(self):
        self.rules['duration']['value']=5
        with self.assertRaises(ValueError): verify_limits(self.source,self.rules)
    def test_new_threshold_not_hardcoded(self):
        source=self.source.replace('20页以内','18页以内'); self.rules['slides']={'quote':'18页以内','value':18}; rows=inspect_pack(GOOD,self.rules,source)
        self.assertEqual(next(row['status'] for row in rows if row['key']=='PPT 页数'),'需修改')


if __name__=='__main__': unittest.main()
