import shutil
import struct
from pathlib import Path

from checker import GOOD, ROOT


def fault_sample(temp):
    folder = Path(temp)
    shutil.copyfile(ROOT/'testdata'/'21-pages.pptx', folder/'项目方案.pptx')
    shutil.copyfile(GOOD/'项目文档.docx', folder/'项目文档.docx')
    movie = folder/'项目演示.mp4'
    shutil.copyfile(GOOD/movie.name, movie)
    with movie.open('ab') as stream:
        length = 100_000_001 - stream.tell()
        stream.write(struct.pack('>I4s', length, b'free'))
        stream.truncate(100_000_001)
    return folder
