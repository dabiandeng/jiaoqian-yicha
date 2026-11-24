import io
import json
import os
import tempfile
import unittest
from contextlib import redirect_stderr, redirect_stdout
from pathlib import Path
from unittest.mock import patch

from dsh_review import ReviewError, bridge, candidates, checked_reply, review, select
from run import EXAMPLE, GOOD, load_notice, make_plan, optional_review, save_audit, save_plan


class DshReviewTests(unittest.TestCase):
    def setUp(self):
        self.plan = make_plan(load_notice(EXAMPLE), '不可外发的本地标题')
        self.options = candidates(self.plan)
        self.selected = select(self.options, '4,6')
        self.calls = []
        self.workspace = str(Path.home()/'Documents'/'deepseek-harness'/'default-workspace')
        self.preset = 'pure-chat'
        self.reasons = ['idle']

    def fake(self, name, **args):
        self.calls.append((name, args))
        if name == 'dsh_workspaces':
            return {'items': [{'path': self.workspace, 'workspaceId': 'workspace'}]}
        if name == 'dsh_create':
            self.assertEqual(args['agentPreset'], 'pure-chat')
            return {'sessionId': 'new-session', 'agentPreset': self.preset}
        if name == 'dsh_send':
            self.request = args['requestId']
            return {'accepted': True, 'cursor': 42}
        self.assertEqual(name, 'dsh_wait')
        self.assertEqual(args['afterSeq'], 42)
        self.assertEqual(args['sessionId'], 'new-session')
        reply = {'request_id': self.request, 'items': self.answers()}
        message = {'content': [{'type': 'thinking', 'text': '不应输出'}, {'type': 'text', 'text': json.dumps(reply)}]}
        event = {'type': 'assistant/message', 'data': {'message': message}}
        return {'reason': self.reasons.pop(0), 'output': {'records': [{'event': event}]}}

    def answers(self):
        return [{'id': item['id'], 'evidence': item['quote'], 'action': '<打开作品核对>'} for item in self.selected]

    def test_minimal_payload_and_timeout_then_success_without_resend(self):
        rows = [{'status': '人工核查', 'material': '私人材料名', 'source': 4, 'key': '文件匹配', 'value': '秘密文件.pptx'}]
        self.selected = select(candidates(self.plan, rows), '4，6,4')
        self.reasons = ['timeout', 'idle']
        result = review(self.selected, self.fake)
        self.assertEqual(result['items'], self.answers())
        self.assertEqual([name for name, args in self.calls], ['dsh_workspaces', 'dsh_create', 'dsh_send', 'dsh_wait', 'dsh_wait'])
        prompt = self.calls[2][1]['text']
        for private in ('私人材料名', '秘密文件', '不可外发', str(GOOD), '15 页', '不要求提交代码仓库'):
            self.assertNotIn(private, prompt)

    def test_selection_limits(self):
        for numbers in ('2,3', '', '0', '4,-1'):
            with self.subTest(numbers=numbers), self.assertRaises(ReviewError):select(self.options, numbers)
        with self.assertRaises(ReviewError):select({4: {'quote': '字'*6001}}, '4')

    def test_no_other_workspace_or_fallback_preset(self):
        self.workspace = str(Path.home()/'other')
        with self.assertRaises(ReviewError):review(self.selected, self.fake)
        self.assertEqual(len(self.calls), 1)

    def test_different_preset_never_receives_text(self):
        self.preset = 'default'
        with self.assertRaises(ReviewError):review(self.selected, self.fake)
        self.assertNotIn('dsh_send', [name for name, args in self.calls])

    def test_input_required_and_timeout_do_not_resend(self):
        for reasons in (['input-required'], ['timeout']*4):
            with self.subTest(reasons=reasons):
                self.calls.clear();self.reasons = reasons.copy()
                with self.assertRaisesRegex(ReviewError, '交前一查'):review(self.selected, self.fake)
                self.assertEqual(sum(name == 'dsh_send' for name, args in self.calls), 1)

    def test_reject_missing_extra_duplicate_misquoted_and_wrong_request(self):
        first, second = self.answers()
        for items in ([first], [first, first], [first, second, dict(first, id=3)],
                      [first, dict(second, evidence='英文字幕')], [first, dict(second, action='')]):
            with self.subTest(items=items), self.assertRaises(ReviewError):
                checked_reply(json.dumps({'request_id': 'r', 'items': items}), 'r', self.selected)
        with self.assertRaises(ReviewError):checked_reply(json.dumps({'request_id': 'other', 'items': [first, second]}), 'r', self.selected)

    def test_preview_decline_and_no_candidates_never_call_bridge(self):
        with tempfile.TemporaryDirectory() as temp, patch('run.review') as remote, redirect_stdout(io.StringIO()):
            directory = Path(temp)
            self.assertTrue(optional_review(self.plan, [], directory, ''))
            with patch('builtins.input', return_value=''):
                self.assertTrue(optional_review(self.plan, [], directory, interactive=True))
            self.assertTrue(optional_review(make_plan('无需单独提交演示视频。','test'), [], directory, '1'))
            remote.assert_not_called()
            self.assertEqual(list(directory.iterdir()), [])

    def test_separate_report_and_failure_leave_local_evidence_intact(self):
        with tempfile.TemporaryDirectory() as temp, redirect_stdout(io.StringIO()), redirect_stderr(io.StringIO()):
            directory = Path(temp);save_plan(self.plan, directory)
            rows = save_audit(self.plan, GOOD, directory, 'test')
            baseline = {path: path.read_bytes() for path in directory.iterdir()}
            with patch('run.review', side_effect=ReviewError('断连')):
                self.assertFalse(optional_review(self.plan, rows, directory, '4,6'))
                self.assertFalse(list(directory.glob('DSH*')))
            with patch('run.review', side_effect=lambda selected: review(selected, self.fake)):
                self.assertTrue(optional_review(self.plan, rows, directory, '4,6'))
            self.assertIn('&lt;打开作品核对&gt;', (directory/'DSH复核建议.html').read_text(encoding='utf-8'))
            self.assertEqual(baseline, {path: path.read_bytes() for path in baseline})

    def test_offline_or_nonloopback_fails_before_connecting(self):
        with tempfile.TemporaryDirectory() as temp, patch('http.client.HTTPConnection') as connection:
            path = Path(temp)/'endpoint.json'
            with patch.dict(os.environ, {'DSH_AGENT_BRIDGE_ENDPOINT_FILE': str(path)}):
                with self.assertRaises(ReviewError):bridge('dsh_workspaces')
                path.write_text(json.dumps({'url':'http://example.com:80','token':'0'*64}))
                with self.assertRaises(ReviewError):bridge('dsh_workspaces')
            connection.assert_not_called()
