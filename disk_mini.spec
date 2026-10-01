# -*- mode: python ; coding: utf-8 -*-
# 빌드: python -m PyInstaller --clean --noconfirm disk_mini.spec
import os
# disk_mini 경량 빌드: 사용하지 않는 표준 라이브러리 / PIL 플러그인 / Tcl 데이터 제외

EXCLUDES = [
    # 네트워크/암호화 (libcrypto, libssl 제거)
    'ssl', '_ssl', '_hashlib', 'hashlib', 'asyncio', 'http', 'urllib.request',
    'ftplib', 'smtplib', 'email', 'xmlrpc', 'socketserver',
    # 기타 무거운 표준 모듈
    'unittest', 'pydoc', 'pdb', 'doctest', 'xml', 'pyexpat', '_elementtree',
    'lzma', '_lzma', 'bz2', '_bz2', 'decimal', '_decimal', 'sqlite3',
    'multiprocessing', 'concurrent', 'distutils', 'setuptools', 'pkg_resources',
    'numpy', 'tkinter.test', 'lib2to3',
    # 사용하지 않는 PIL 기능
    'PIL.ImageTk', 'PIL._imagingtk', 'PIL._webp', 'PIL._avif', 'PIL._imagingcms',
    'PIL.ImageCms', 'PIL._imagingmath', 'PIL.ImageMath', 'PIL.ImageQt',
    'PIL.ImageShow', 'PIL.ImageGrab',
]

# Tcl/Tk 데이터 중 사용하지 않는 것 (시간대, 메시지 번역, 테스트/http 패키지, 데모 이미지)
DROP_DATA = ('\\tzdata\\', '\\msgs\\', 'tcltest', 'http-', '\\demos\\', '\\images\\')
KEEP_ENC = ('ascii', 'cp949', 'cp1252', 'iso8859-1', 'utf-8', 'unicode')

def keep(entry):
    name = entry[0].replace('/', '\\')
    if any(p in name for p in DROP_DATA):
        return False
    if '\\encoding\\' in name and not any(e in name for e in KEEP_ENC):
        return False
    return True

a = Analysis(
    [os.path.join(SPECPATH, 'disk_mini.py')],
	datas=[('my_icon.ico', '.')],
    hiddenimports=['pystray._win32'],
    excludes=EXCLUDES,
    noarchive=False,
    optimize=2,
)
a.datas = [d for d in a.datas if keep(d)]

pyz = PYZ(a.pure)

exe = EXE(
    pyz,
    a.scripts,
    a.binaries,
    a.datas,
    [],
    name='disk_mini',
    debug=False,
    strip=False,
    upx=False,
    console=False,
	icon='my_icon.ico',
)
