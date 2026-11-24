import getpass
import http.client
import json
import os
import re
import sys
from urllib.parse import urlsplit
from uuid import uuid4

from dsh_review import REVIEW_INSTRUCTION, ReviewError, checked_reply


MODEL = 'deepseek-v4-flash'
HOST = 'api.deepseek.com'


class CloudClient:
    def __init__(self, interactive=False, *, custom=False, base='', model='', key=None, use_env=True):
        self.interactive = interactive
        self.key = key
        self.use_env = use_env and not custom
        self.model = MODEL
        self.host, self.path, self.secure = HOST, '/chat/completions', True
        self.provider = 'DeepSeek API'
        self.custom = custom
        if custom:
            try:
                url = urlsplit(base)
                if (not base.isascii() or not re.fullmatch(r'[A-Za-z0-9.:/\[\]_%~-]+',base) or not url.hostname
                        or url.username is not None or '%' in url.netloc or not (url.port is None or url.port > 0)
                        or not (url.scheme == 'https' or (url.scheme == 'http' and url.hostname in ('localhost','127.0.0.1','::1')))):
                    raise ValueError()
            except ValueError:
                raise ReviewError('接口地址无效。请使用 HTTPS 基础地址；本机回环地址也可使用 HTTP。') from None
            if not re.fullmatch(r'[A-Za-z0-9_./:@+-]{1,160}',model):raise ReviewError('请填写有效的模型 ID。')
            self.model, self.host, self.secure = model, url.netloc, url.scheme == 'https'
            self.path = url.path.rstrip('/')
            if not self.path.endswith('/chat/completions'):self.path += '/chat/completions'
            self.provider = '自定义 API'
        self.reply_model = None

    def chat(self, instruction, data):
        key = self.key or (os.getenv('DEEPSEEK_API_KEY', '').strip() if self.use_env else '')
        if not key and self.interactive and sys.stdin.isatty():
            key = getpass.getpass('DeepSeek API Key（隐藏输入，仅本次使用；回车跳过）：').strip()
        if not key and not self.custom:
            raise ReviewError('没有 API Key；请提供自己的密钥或选择离线模式。')
        if key and (not key.isascii() or not key.isprintable() or any(char.isspace() for char in key)):
            raise ReviewError('API Key 格式无效。')
        self.key = key
        body = {'model': self.model, 'messages': [{'role':'system','content':instruction},
                {'role':'user','content':json.dumps(data,ensure_ascii=False)}],
                'stream':False}
        if not self.custom:body.update(response_format={'type':'json_object'},thinking={'type':'disabled'},max_tokens=3200)
        headers = {'Content-Type':'application/json'}
        if key:headers['Authorization']='Bearer '+key
        connection = (http.client.HTTPSConnection if self.secure else http.client.HTTPConnection)(self.host, timeout=45)
        try:
            connection.request('POST',self.path,json.dumps(body,ensure_ascii=False).encode(),headers)
            response=connection.getresponse()
            if response.status!=200:
                reason={401:'Key 无效',402:'余额不足',403:'无权限',429:'请求受限'}.get(response.status,'服务未接受请求')
                raise ReviewError(f'{self.provider}：{reason}（HTTP {response.status}）。')
            raw=response.read(512001)
            if len(raw)>512000:raise ReviewError('云端回复过长，不采用。')
            payload=json.loads(raw);choice=payload['choices'][0]
            reported=payload.get('model')
            if key and (key in str(reported or '') or key in str(choice['message']['content'])):
                raise ReviewError('回复含凭据信息，已丢弃，不生成模型报告。')
            self.reply_model=reported if isinstance(reported,str) and re.fullmatch(r'[A-Za-z0-9_./:@+-]{1,160}',reported) else None
            if choice.get('finish_reason')!='stop' or not isinstance(choice['message']['content'],str):
                raise ReviewError('云端回复不完整，不采用。')
            return choice['message']['content']
        except (OSError,http.client.HTTPException) as exc:
            raise ReviewError('未能连接 API；请检查接口地址和网络，无需关闭证书验证。') from exc
        except (KeyError,TypeError,IndexError,AttributeError,UnicodeError,json.JSONDecodeError) as exc:
            raise ReviewError('API 回复格式不符，需要兼容 Chat Completions 的接口。') from exc
        finally:
            connection.close()

    def review(self, selected):
        request_id='jiaoqian-'+uuid4().hex
        raw=self.chat(REVIEW_INSTRUCTION,{'request_id':request_id,'clauses':selected})
        return {'title':self.provider+' · '+(self.reply_model or self.model),'provider':self.provider,'model':self.reply_model,'request_id':request_id,
                'items':checked_reply(raw,request_id,selected)}
