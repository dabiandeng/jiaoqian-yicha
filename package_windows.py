import argparse
import hashlib
import io
import subprocess
import urllib.request
import zipfile
from pathlib import Path


ROOT = Path(__file__).resolve().parent
VERSION = '3.13.17'
RUNTIME_SHA = '9e35319bffedab8b36ad44d8f377cb4f1733bcec257a0b6d04d94f01486ddb38'
MODULES = ('checker', 'cloud_api', 'dsh_review', 'file_audit', 'gui_worker', 'notice_plan', 'notice_scope', 'run')
NOTICES = ('创意展-合成通知.txt', '校园短片展-合成通知.txt', '分阶段路演-合成通知.txt', '研究海报说明-合成通知.txt')


def build(runtime):
    if runtime:
        raw = runtime.read_bytes()
    else:
        url = f'https://www.python.org/ftp/python/{VERSION}/python-{VERSION}-embed-amd64.zip'
        with urllib.request.urlopen(url, timeout=120) as response:
            raw = response.read()
    if hashlib.sha256(raw).hexdigest() != RUNTIME_SHA:
        raise ValueError('Python runtime SHA-256 mismatch')
    subprocess.run(['powershell.exe', '-NoProfile', '-ExecutionPolicy', 'RemoteSigned', '-File', str(ROOT/'desktop'/'build.ps1')], check=True)
    target = ROOT/'dist'/'jiaoqian-yicha-windows-x64.zip'
    target.parent.mkdir(exist_ok=True)
    files = ['README.md', '交前一查.exe', *(module+'.py' for module in MODULES),
             *('示例通知/'+name for name in NOTICES), *('样包/02-修正版/'+name for name in ('项目文档.docx','项目方案.pptx','项目演示.mp4'))]
    with zipfile.ZipFile(io.BytesIO(raw)) as python, zipfile.ZipFile(target.with_suffix('.tmp'), 'w', zipfile.ZIP_DEFLATED) as archive:
        for name in python.namelist():
            data = b'python313.zip\r\n.\r\n..\r\n' if name == 'python313._pth' else python.read(name)
            archive.writestr('runtime/'+name,data)
        for name in files:
            archive.write(ROOT/name,name)
    target.with_suffix('.tmp').replace(target)
    print(target,hashlib.sha256(target.read_bytes()).hexdigest())


if __name__ == '__main__':
    parser = argparse.ArgumentParser();parser.add_argument('--runtime',type=Path);build(parser.parse_args().runtime)
