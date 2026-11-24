import contextlib
import io
import json
import os
import sys
import tempfile
import unittest
from pathlib import Path
from unittest.mock import Mock, patch

from cloud_api import CloudClient
from dsh_review import ReviewError
from notice_plan import load_notice, make_plan
from run import EXAMPLE, GOOD, main, optional_review


class PortableCloudTests(unittest.TestCase):
    def test_cloud_hints_send_no_title_or_files_and_ignore_fabricated_rules(self):
        client=Mock();client.chat.return_value=json.dumps({'materials':[{'id':2,'kind':'word','label':'非原文','max_size_mb':1000}]})
        text=load_notice(EXAMPLE);plan=make_plan(text,'不外发的本地标题',cloud=client)
        self.assertEqual(plan['items'],make_plan(text,'离线')['items'])
        _,sent=client.chat.call_args.args;self.assertTrue(all(set(item)=={'id','text'} for item in sent))
        self.assertNotIn('不外发',str(sent));self.assertNotIn(str(GOOD),str(sent))

    @patch('cloud_api.http.client.HTTPSConnection')
    def test_authenticated_contract_and_verified_quote(self, connection):
        selected=[{'id':4,'quote':'须匿名。','focus':['人工核查']}];response=connection.return_value.getresponse.return_value
        response.status=200
        def payload(limit):
            method,path,body,headers=connection.return_value.request.call_args.args
            self.assertEqual((method,path),('POST','/chat/completions'));self.assertEqual(headers['Authorization'],'Bearer test-placeholder')
            body=json.loads(body);self.assertEqual(body['model'],'deepseek-v4-flash');self.assertEqual(body['response_format']['type'],'json_object')
            request=json.loads(body['messages'][1]['content']);self.assertEqual(request['clauses'],selected)
            result={'request_id':request['request_id'],'items':[{'id':4,'evidence':'须匿名','action':'检查封面署名。'}]}
            return json.dumps({'choices':[{'finish_reason':'stop','message':{'content':json.dumps(result)}}]}).encode()
        response.read.side_effect=payload
        with patch.dict(os.environ,{'DEEPSEEK_API_KEY':'test-placeholder'}):result=CloudClient().review(selected)
        connection.assert_called_once_with('api.deepseek.com',timeout=45);self.assertEqual(result['items'][0]['evidence'],'须匿名');self.assertNotIn('placeholder',str(result))

    def test_missing_key_and_preview_make_no_connection(self):
        with patch.dict(os.environ,{'DEEPSEEK_API_KEY':''}),patch('cloud_api.http.client.HTTPSConnection') as connection, contextlib.redirect_stdout(io.StringIO()):
            with self.assertRaises(ReviewError):CloudClient().chat('JSON',[])
            self.assertTrue(optional_review(make_plan(load_notice(EXAMPLE),'test'),[],None,'',cloud=CloudClient()))
            connection.assert_not_called()

    def test_failures_do_not_leak_keys_follow_redirects_or_retry(self):
        for status in (302,401,402,429,503,200):
            with self.subTest(status=status),patch.dict(os.environ,{'DEEPSEEK_API_KEY':'test-private'}),patch('cloud_api.http.client.HTTPSConnection') as connection:
                response=connection.return_value.getresponse.return_value;response.status=status
                response.read.return_value=b'{"choices":[{"finish_reason":"length"}]}'
                with self.assertRaises(ReviewError) as error:CloudClient().chat('JSON',[])
                self.assertNotIn('test-private',str(error.exception));self.assertEqual(connection.call_count,1);connection.return_value.close.assert_called_once()

    def test_cloud_outage_leaves_local_plan_and_report(self):
        with tempfile.TemporaryDirectory() as temp,patch.dict(os.environ,{'DEEPSEEK_API_KEY':''}),patch('cloud_api.http.client.HTTPSConnection') as connection, contextlib.redirect_stdout(io.StringIO()),contextlib.redirect_stderr(io.StringIO()):
            with patch.object(sys,'argv',['run.py','--notice',str(EXAMPLE),'--folder',str(GOOD),'--confirm','--output',temp,'--api-ai']),self.assertRaises(SystemExit) as exit_status:main()
            self.assertEqual(exit_status.exception.code,2);connection.assert_not_called();report=json.loads(next(Path(temp).rglob('预检结果.json')).read_text(encoding='utf-8'))
            self.assertEqual({row['key'] for row in report['results'] if row['status']=='需修改'},{'页数','时长'})
