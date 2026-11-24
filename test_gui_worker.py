import os
import json
import threading
from http.server import BaseHTTPRequestHandler, HTTPServer
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from checker import GOOD
from gui_worker import dispatch
from run import EXAMPLE


class GuiWorkerTests(unittest.TestCase):
    def setUp(self):
        temporary = tempfile.TemporaryDirectory()
        self.addCleanup(temporary.cleanup)
        self.output = Path(temporary.name)

    def call(self, action, **inputs):
        return dispatch(dict(inputs, action=action), self.output)

    def plan(self, **settings):
        notice = self.call('read', path=str(EXAMPLE))
        return self.call('plan', text=notice['text'], title=notice['title'], **settings)

    def test_personal_api_roundtrip(self):
        received=[]
        class Handler(BaseHTTPRequestHandler):
            def log_message(self,*args):pass
            def do_POST(self):
                body=json.loads(self.rfile.read(int(self.headers['Content-Length'])))
                payload=json.loads(body['messages'][1]['content'])
                received.append((self.path,self.headers.get('Authorization'),body,payload))
                if isinstance(payload,list):answer={'materials':[]}
                elif 'probe' in payload:answer=payload
                else:answer={'request_id':payload['request_id'],'items':[dict(id=item['id'],evidence=item['quote'],action='人工核对原句。') for item in payload['clauses']]}
                response=json.dumps({'model':'served-model','choices':[{'finish_reason':'stop','message':{'content':json.dumps(answer)}}]}).encode()
                self.send_response(200);self.end_headers();self.wfile.write(response)
        server=HTTPServer(('127.0.0.1',0),Handler);thread=threading.Thread(target=server.serve_forever,daemon=True);thread.start()
        self.addCleanup(server.server_close);self.addCleanup(thread.join);self.addCleanup(server.shutdown)
        secret='dummy-personal-key';settings=dict(custom=True,base=f'http://127.0.0.1:{server.server_port}/v1',model='chosen-model')
        with patch.dict(os.environ,{'DEEPSEEK_API_KEY':'unrelated-secret'}):
            result=self.call('test_api',text='PRIVATE NOTICE',**settings)
        self.assertEqual(result,{'model':'chosen-model','reported':'served-model'});self.assertIsNone(received[0][1]);self.assertEqual(received[0][3],{'probe':'ok'});self.assertNotIn('PRIVATE NOTICE',str(received[0]));self.assertFalse(list(self.output.iterdir()))
        settings['key']=secret
        with self.assertRaises(ValueError):self.plan(cloud=True,**settings)
        plan=self.plan(cloud=True,consent=True,**settings);self.assertIn('chosen-model',plan['method']);item=plan['options'][0]
        report=self.call('review',provider='API',directory=plan['directory'],ids=str(item['id']),consent=True,**settings)
        self.assertEqual(report['model'],'served-model');self.assertEqual(received[-1][3]['clauses'],[item]);self.assertEqual(len(received),3)
        for path,token,body,_ in received:
            self.assertEqual(path,'/v1/chat/completions');self.assertIn(token,(None,'Bearer '+secret));self.assertEqual(body['model'],'chosen-model');self.assertNotIn('thinking',body);self.assertNotIn(secret,str(body))
        self.assertEqual(received[1][1],'Bearer '+secret);self.assertEqual(received[2][1],'Bearer '+secret)
        for output in self.output.rglob('*'):
            if output.is_file():self.assertNotIn(secret.encode(),output.read_bytes())

    def test_rejects_unsafe_api_destination_before_connecting(self):
        with patch('cloud_api.http.client.HTTPSConnection') as secure,patch('cloud_api.http.client.HTTPConnection') as plain:
            for base in ('http://example.org/v1','https://user:password@example.org','https://example.org/v1?key=secret','https://example.org:bad/v1'):
                with self.subTest(base=base),self.assertRaises(ValueError):self.call('test_api',custom=True,base=base,model='demo',key='dummy-key')
            secure.assert_not_called();plain.assert_not_called()

    def test_read_requires_text(self):
        with self.assertRaisesRegex(ValueError, '80000'):
            self.call('read', text='   ')

    def test_track_scope_keeps_original_evidence(self):
        text = '必交材料：\n1.创意赛道：\n请提交项目说明书（DOCX）。\n2.实践赛道：\n请提交演示视频（MP4），不超过2分钟。'
        notice = self.call('read', text=text)
        self.assertEqual(notice['tracks'], ['创意赛道', '实践赛道'])
        with self.assertRaises(ValueError):
            self.call('plan', text=text, title='粘贴的比赛通知')
        plan = self.call('plan', text=text, title='粘贴的比赛通知', track='实践赛道')
        self.assertEqual([item['quote'] for item in plan['items']], [text.splitlines()[-1]])

    def test_requires_confirmation_and_known_plan(self):
        plan = self.plan()
        for false_value in (False, 'true', 1):
            with self.assertRaisesRegex(ValueError, '确认任务清单'):
                self.call('audit', directory=plan['directory'], folder=str(GOOD), confirmed=false_value)
        with self.assertRaisesRegex(ValueError, '本窗口生成'):
            self.call('audit', directory=str(GOOD), confirmed=True, folder=str(GOOD))
        self.assertFalse(list(self.output.rglob('预检结果.json')))

    def test_demotion_does_not_overwrite_prior_report(self):
        plan = self.plan()
        args = dict(directory=plan['directory'], folder=str(GOOD), confirmed=True)
        original = self.call('audit', **args)
        before = Path(original['file']).read_bytes()
        changed = self.call('audit', manual='1', **args)
        failures = lambda response: {row['key'] for row in response['rows'] if row['status'] == '需修改'}
        self.assertEqual(failures(original), {'页数', '时长'})
        self.assertEqual(failures(changed), {'时长'})
        self.assertNotEqual(original['directory'], changed['directory'])
        self.assertEqual(Path(original['file']).read_bytes(), before)

    def test_demo_compares_actual_reports(self):
        demo = self.call('demo')
        self.assertEqual([len(case['failures']) for case in demo['cases']], [0, 2])
        for case in ('A', 'B'):
            self.assertTrue((Path(demo['file']).parent / case / '预检结果.html').is_file())

    def test_offline_and_review_consent(self):
        with patch('gui_worker.client') as cloud:
            plan = self.plan()
            with self.assertRaisesRegex(ValueError, '确认要发送'):
                self.call('review', directory=plan['directory'], ids='1', consent=False)
            with self.assertRaisesRegex(ValueError, '只能选'):
                self.call('review', directory=plan['directory'], ids='999', consent=True)
            cloud.assert_not_called()
            selected = plan['options'][0]
            fake_key = 'gui-test-dummy-not-a-credential'
            cloud.return_value.review.return_value = dict(title='测试模型', items=[dict(id=selected['id'], evidence=selected['quote'], action='请人工对照原句。')])
            result = self.call('review', directory=plan['directory'], ids=str(selected['id']), consent=True, key=fake_key)
            cloud.return_value.review.assert_called_once_with([selected])
            self.assertTrue(Path(result['file']).is_file())
            for path in self.output.rglob('*'):
                if path.is_file():
                    self.assertNotIn(fake_key.encode(), path.read_bytes())

    def test_api_no_key_falls_back_without_network(self):
        with patch.dict(os.environ, {'DEEPSEEK_API_KEY': ''}), patch('cloud_api.http.client.HTTPSConnection') as network:
            response = self.plan(cloud=True, consent=True)
            network.assert_not_called()
        self.assertIn('没有 API Key', response['warning'])
        self.assertIn('离线', response['method'])
        self.assertTrue(response['items'])

    def test_local_and_rules_share_evidence_not_model_numbers(self):
        text='根据《某市短视频大赛的通知》精神，现通知如下：\n作品时长60—180秒，格式为MP4/MOV，文件大小不超过500MB。'
        with patch('notice_plan.ai_hints', return_value={}) as local, patch('gui_worker.client') as cloud:
            plan=self.call('plan',text=text,title='短视频',local=True)
        local.assert_called_once();cloud.assert_not_called();self.assertIn('9b',plan['method'])
        audit=self.call('audit',directory=plan['directory'],folder=str(GOOD),confirmed=True)
        self.assertEqual([(row['key'],row['value']) for row in audit['rows'] if row['status']=='需修改'],[('时长','288 秒')])

    def test_local_no_silent_model_fallback(self):
        with patch('notice_plan.ai_hints',side_effect=ValueError('模型不可用')),patch('gui_worker.client') as cloud:
            with self.assertRaisesRegex(ValueError,'模型不可用'):self.plan(local=True)
            cloud.assert_not_called()
        self.assertFalse(list(self.output.iterdir()))

    def test_final_format_and_optional_followup(self):
        text='必交材料：\n1.项目介绍材料（PPT制作，转PDF格式提交文件），必填项。\n2.项目演示视频（MP4格式文件）：项目录屏。视频时长不超过5分钟，大小不超过200M，选填项。'
        with patch('notice_plan.ai_hints',return_value={}):plan=self.call('plan',text=text,title='提交补充',local=True)
        self.assertEqual(len(plan['items']),2);self.assertIn('PDF',plan['items'][0]['rule']);self.assertNotIn('PPT',plan['items'][0]['rule'])
        empty=self.output/'空目录';empty.mkdir();audit=self.call('audit',directory=plan['directory'],folder=str(empty),confirmed=True)
        self.assertEqual([row['status'] for row in audit['rows'] if row['material']=='项目演示视频'],['可选'])

    def test_dsh_only_receives_selection_and_returns_receipt(self):
        plan=self.plan(); clause=plan['options'][0]; original=Path(plan['planFile']).read_bytes()
        reply={'title':'纯聊天','provider':'test-provider','model':'test-default','items':[{'id':clause['id'],'evidence':clause['quote'],'action':'核对原文'}]}
        with patch('gui_worker.review',return_value=reply) as dsh,patch('gui_worker.client') as api:
            args=dict(directory=plan['directory'],provider='DSH',ids=str(clause['id']))
            with self.assertRaises(ValueError):self.call('review',consent=False,**args)
            dsh.assert_not_called();result=self.call('review',consent=True,**args);dsh.assert_called_once_with([clause]);api.assert_not_called()
        self.assertEqual(result['model'],'test-default');self.assertEqual(Path(result['file']).name,'DSH复核建议.html');self.assertEqual(Path(plan['planFile']).read_bytes(),original)

    def test_scope_before_local_model(self):
        text='初赛报名指南\n请提交演示文稿（PPTX）。\n复赛提交指南\n请提交演示视频（MP4）。'
        with patch('notice_plan.ai_hints',return_value={}) as local:
            with self.assertRaises(ValueError):self.call('plan',text=text,title='阶段',local=True)
            local.assert_not_called();self.call('plan',text=text,title='阶段',local=True,track='复赛提交指南')
            self.assertNotIn('PPTX',str(local.call_args));self.assertIn('MP4',str(local.call_args))
