import ctypes
import ctypes.wintypes as wt
import os
import sys
import tempfile
import threading
import time
import traceback
import winreg
import tkinter as tk
from PIL import Image, ImageDraw
import pystray

# Windows API 설정
pdh = ctypes.WinDLL("pdh.dll")
mpr = ctypes.WinDLL("mpr.dll")
kernel32 = ctypes.windll.kernel32
user32 = ctypes.windll.user32

PDH_HQUERY = wt.HANDLE
PDH_HCOUNTER = wt.HANDLE

PDH_FMT_DOUBLE = 0x00000200
PDH_MORE_DATA = 0x800007D2
PDH_CSTATUS_VALID_DATA = 0x00000000
PDH_CSTATUS_NEW_DATA = 0x00000001
ERROR_SUCCESS = 0
ERROR_CONNECTION_UNAVAIL = 1201

DRIVE_REMOVABLE = 2
DRIVE_FIXED = 3
DRIVE_REMOTE = 4

# Windows Style 상수
GWL_EXSTYLE = -20
WS_EX_TOOLWINDOW = 0x00000080
WS_EX_APPWINDOW = 0x00040000


class PDH_FMT_COUNTERVALUE_DOUBLE(ctypes.Structure):
    _fields_ = [("CStatus", wt.DWORD), ("doubleValue", ctypes.c_double)]


class PDH_FMT_COUNTERVALUE_ITEM_W(ctypes.Structure):
    _fields_ = [("szName", wt.LPWSTR), ("FmtValue", PDH_FMT_COUNTERVALUE_DOUBLE)]


pdh.PdhOpenQueryW.argtypes = [wt.LPCWSTR, ctypes.c_void_p, ctypes.POINTER(PDH_HQUERY)]
pdh.PdhOpenQueryW.restype = wt.LONG

pdh.PdhAddEnglishCounterW.argtypes = [
    PDH_HQUERY, wt.LPCWSTR, ctypes.c_size_t, ctypes.POINTER(PDH_HCOUNTER),
]
pdh.PdhAddEnglishCounterW.restype = wt.LONG

pdh.PdhAddCounterW.argtypes = [
    PDH_HQUERY, wt.LPCWSTR, ctypes.c_size_t, ctypes.POINTER(PDH_HCOUNTER),
]
pdh.PdhAddCounterW.restype = wt.LONG

pdh.PdhCollectQueryData.argtypes = [PDH_HQUERY]
pdh.PdhCollectQueryData.restype = wt.LONG

pdh.PdhGetFormattedCounterValue.argtypes = [
    PDH_HCOUNTER, wt.DWORD, ctypes.POINTER(wt.DWORD),
    ctypes.POINTER(PDH_FMT_COUNTERVALUE_DOUBLE),
]
pdh.PdhGetFormattedCounterValue.restype = wt.LONG

pdh.PdhGetFormattedCounterArrayW.argtypes = [
    PDH_HCOUNTER, wt.DWORD, ctypes.POINTER(wt.DWORD),
    ctypes.POINTER(wt.DWORD), ctypes.c_void_p,
]
pdh.PdhGetFormattedCounterArrayW.restype = wt.LONG

pdh.PdhRemoveCounter.argtypes = [PDH_HCOUNTER]
pdh.PdhRemoveCounter.restype = wt.LONG

pdh.PdhCloseQuery.argtypes = [PDH_HQUERY]
pdh.PdhCloseQuery.restype = wt.LONG

kernel32.GetDriveTypeW.argtypes = [wt.LPCWSTR]
kernel32.GetDriveTypeW.restype = wt.UINT

mpr.WNetGetConnectionW.argtypes = [wt.LPCWSTR, wt.LPWSTR, ctypes.POINTER(wt.DWORD)]
mpr.WNetGetConnectionW.restype = wt.DWORD


DEBUG = "--debug" in sys.argv
ERROR_LOG = os.path.join(tempfile.gettempdir(), "disk_mini_error.log")
DEBUG_LOG = os.path.join(tempfile.gettempdir(), "disk_mini_debug.log")
_last_error = None


def setup_debug_console():
    """--noconsole 로 빌드된 exe 에서도 --debug 출력이 보이도록 콘솔 창 할당"""
    if not DEBUG:
        return
    if sys.stdout is None or not kernel32.GetConsoleWindow():
        kernel32.AllocConsole()
        try:
            sys.stdout = open("CONOUT$", "w", encoding="utf-8", buffering=1)
            sys.stderr = sys.stdout
        except OSError:
            pass
    try:
        open(DEBUG_LOG, "w", encoding="utf-8").close()
    except OSError:
        pass


def dbg(*args):
    """--debug 시 콘솔과 %TEMP%\\disk_mini_debug.log 에 함께 기록"""
    if not DEBUG:
        return
    msg = " ".join(str(a) for a in args)
    try:
        if sys.stdout:
            print(msg, flush=True)
    except Exception:
        pass
    try:
        with open(DEBUG_LOG, "a", encoding="utf-8") as f:
            f.write(msg + "\n")
    except OSError:
        pass


def log_error(where):
    """예외를 파일(및 --debug 시 콘솔)에 기록. 같은 오류가 반복되면 한 번만 기록"""
    global _last_error
    tb = traceback.format_exc()
    if tb == _last_error:
        return
    _last_error = tb
    msg = f"[{time.strftime('%Y-%m-%d %H:%M:%S')}] {where}\n{tb}\n"
    dbg(msg)
    try:
        with open(ERROR_LOG, "a", encoding="utf-8") as f:
            f.write(msg)
    except OSError:
        pass


def drive_type(d):
    return kernel32.GetDriveTypeW(d + "\\")


def is_admin():
    try:
        return bool(ctypes.windll.shell32.IsUserAnAdmin())
    except Exception:
        return False


def registry_network_drives():
    """HKCU\\Network 의 영구 네트워크 드라이브 매핑 {"Z:": "\\\\server\\share"}

    관리자 권한으로 실행하면 일반 권한에서 연결한 드라이브 문자가 보이지 않으므로
    이 정보로 보완한다. (SMB 성능 카운터는 시스템 전체 값이라 그대로 읽을 수 있음)
    """
    result = {}
    try:
        with winreg.OpenKey(winreg.HKEY_CURRENT_USER, "Network") as key:
            i = 0
            while True:
                try:
                    name = winreg.EnumKey(key, i)
                except OSError:
                    break
                i += 1
                if len(name) != 1 or not name.isalpha():
                    continue
                try:
                    with winreg.OpenKey(key, name) as sub:
                        remote, _ = winreg.QueryValueEx(sub, "RemotePath")
                    if remote:
                        result[f"{name.upper()}:"] = remote
                except OSError:
                    pass
    except OSError:
        pass
    return result


_registry_net = {}   # drives() 호출 시 갱신


def is_network_drive(d):
    return drive_type(d) == DRIVE_REMOTE or d in _registry_net


def drives():
    """현재 시스템에 마운트된 로지컬 드라이브 목록 조회 (드라이브 문자 순)"""
    global _registry_net
    mask = kernel32.GetLogicalDrives()
    result = set()
    for i in range(26):
        if mask & (1 << i):
            d = f"{chr(65 + i)}:"
            if drive_type(d) in (DRIVE_REMOVABLE, DRIVE_FIXED, DRIVE_REMOTE):
                result.add(d)

    # 이 프로세스에서 보이지 않는 영구 네트워크 드라이브 보완
    reg = registry_network_drives()
    _registry_net = {d: r for d, r in reg.items() if d not in result}
    result.update(_registry_net)
    return sorted(result)


def dump_drive_diag():
    """--debug: 드라이브 인식 과정 상세 출력"""
    if not DEBUG:
        return
    dbg("=" * 60)
    dbg(f"python={sys.executable}  frozen={getattr(sys, 'frozen', False)}  admin={is_admin()}")
    mask = kernel32.GetLogicalDrives()
    dbg(f"GetLogicalDrives mask=0x{mask:08X}")
    for i in range(26):
        if mask & (1 << i):
            d = f"{chr(65 + i)}:"
            buf = ctypes.create_unicode_buffer(1024)
            size = wt.DWORD(len(buf))
            rc = mpr.WNetGetConnectionW(d, buf, ctypes.byref(size))
            dbg(f"  {d} type={drive_type(d)}  WNetGetConnection rc={rc} remote={buf.value!r}")
    dbg(f"HKCU\\Network: {registry_network_drives()}")
    dbg(f"drives() -> {drives()}")
    dbg("=" * 60)


def normalize_unc(path):
    """'\\\\Server\\Share\\sub' -> 'server\\share' 형태로 정규화 (SMB 인스턴스 비교용)"""
    p = (path or "").strip().strip("\\").lower()
    parts = [x for x in p.split("\\") if x]
    if len(parts) >= 2:
        return f"{parts[0]}\\{parts[1]}"
    return p


def network_unc(d):
    """네트워크 드라이브 문자에 매핑된 UNC 경로 조회"""
    buf = ctypes.create_unicode_buffer(1024)
    size = wt.DWORD(len(buf))
    rc = mpr.WNetGetConnectionW(d, buf, ctypes.byref(size))
    if rc in (ERROR_SUCCESS, ERROR_CONNECTION_UNAVAIL) and buf.value:
        return normalize_unc(buf.value)
    # 관리자 권한 등으로 WNet 조회가 안 되면 레지스트리 매핑 사용
    remote = _registry_net.get(d) or registry_network_drives().get(d)
    return normalize_unc(remote) if remote else None


def read_counter_array(h):
    """와일드카드 카운터의 인스턴스별 값을 {정규화된 인스턴스명: 값} 으로 반환"""
    for _ in range(3):
        size = wt.DWORD(0)
        count = wt.DWORD(0)
        rc = pdh.PdhGetFormattedCounterArrayW(
            h, PDH_FMT_DOUBLE, ctypes.byref(size), ctypes.byref(count), None
        )
        if (rc & 0xFFFFFFFF) != PDH_MORE_DATA or size.value == 0:
            return {}

        buf = (ctypes.c_byte * size.value)()
        rc = pdh.PdhGetFormattedCounterArrayW(
            h, PDH_FMT_DOUBLE, ctypes.byref(size), ctypes.byref(count), buf
        )
        if (rc & 0xFFFFFFFF) == PDH_MORE_DATA:
            continue  # 그 사이 인스턴스가 늘어난 경우 재시도
        if rc != ERROR_SUCCESS:
            return {}

        items = ctypes.cast(buf, ctypes.POINTER(PDH_FMT_COUNTERVALUE_ITEM_W))
        result = {}
        for i in range(count.value):
            item = items[i]
            name = item.szName or ""
            if not name or name.lower() == "_total":
                continue
            if item.FmtValue.CStatus not in (PDH_CSTATUS_VALID_DATA, PDH_CSTATUS_NEW_DATA):
                continue
            key = normalize_unc(name)
            result[key] = result.get(key, 0.0) + item.FmtValue.doubleValue
        return result
    return {}


def read_single_counter(h):
    """단일 인스턴스 카운터 값 읽기"""
    status = wt.DWORD()
    value = PDH_FMT_COUNTERVALUE_DOUBLE()
    rc = pdh.PdhGetFormattedCounterValue(
        h, PDH_FMT_DOUBLE, ctypes.byref(status), ctypes.byref(value)
    )
    if rc != ERROR_SUCCESS or value.CStatus not in (PDH_CSTATUS_VALID_DATA, PDH_CSTATUS_NEW_DATA):
        return 0.0
    return value.doubleValue


def lookup_share(table, unc):
    """SMB 인스턴스 테이블에서 UNC 에 해당하는 값을 찾음"""
    if not unc:
        return 0.0
    if unc in table:
        return table[unc]
    # 서버 이름 표기(FQDN / 짧은 이름 / IP)가 다른 경우: 공유 이름이 유일하게 일치하면 사용
    if "\\" in unc:
        share = unc.split("\\", 1)[1]
        cands = [v for k, v in table.items() if "\\" in k and k.split("\\", 1)[1] == share]
        if len(cands) == 1:
            return cands[0]
    return 0.0


class DiskMonitor:
    def __init__(self, drive_list):
        self.query = PDH_HQUERY()
        rc = pdh.PdhOpenQueryW(None, 0, ctypes.byref(self.query))
        if rc != ERROR_SUCCESS:
            raise OSError(f"PdhOpenQueryW failed: 0x{rc & 0xFFFFFFFF:08X}")

        self.counters = {}     # 로컬 드라이브: {drive: {kind: hCounter}}
        self.net_drives = {}   # 네트워크 드라이브: {drive: 정규화된 UNC}

        # 네트워크 드라이브용 SMB 클라이언트 카운터 (와일드카드, 모든 공유)
        self.smb_counters = {}
        for kind, path in [
            ("read", "\\SMB Client Shares(*)\\Read Bytes/sec"),
            ("write", "\\SMB Client Shares(*)\\Write Bytes/sec"),
            ("meta", "\\SMB Client Shares(*)\\Metadata Requests/sec"),  # 공유별 활동 판단용
        ]:
            h = PDH_HCOUNTER()
            rc = pdh.PdhAddEnglishCounterW(self.query, path, 0, ctypes.byref(h))
            if rc == ERROR_SUCCESS:
                self.smb_counters[kind] = h

        # 일부 서버(Samba 등)는 SMB Client Shares 에 쓰기가 집계되지 않으므로
        # 리디렉터(전체 네트워크 드라이브 합계) 쓰기 카운터로 보완
        # (기존 SMB/LogicalDisk 카운터에 영향이 없도록 별도 쿼리 사용)
        self.redir_query = PDH_HQUERY()
        self.redir_write = None
        if pdh.PdhOpenQueryW(None, 0, ctypes.byref(self.redir_query)) == ERROR_SUCCESS:
            for path in (
                "\\Redirector\\Write Bytes Network/sec",
                "\\Redirector\\Bytes Transmitted/sec",
            ):
                h = PDH_HCOUNTER()
                if pdh.PdhAddEnglishCounterW(self.redir_query, path, 0, ctypes.byref(h)) == ERROR_SUCCESS:
                    self.redir_write = h
                    break
            pdh.PdhCollectQueryData(self.redir_query)
        else:
            self.redir_query = None

        for d in drive_list:
            self.add_drive(d)

        pdh.PdhCollectQueryData(self.query)

    def add_drive(self, d):
        if d in self.counters or d in self.net_drives:
            return

        if is_network_drive(d):
            self.net_drives[d] = network_unc(d)
            return

        self.counters[d] = {}
        for kind, counter in [
            ("read", f"\\LogicalDisk({d})\\Disk Read Bytes/sec"),
            ("write", f"\\LogicalDisk({d})\\Disk Write Bytes/sec"),
        ]:
            h = PDH_HCOUNTER()
            rc = pdh.PdhAddEnglishCounterW(self.query, counter, 0, ctypes.byref(h))
            if rc != ERROR_SUCCESS:
                # 한국어 로케일 명칭으로 재시도
                ko_counter = counter.replace("LogicalDisk", "논리 디스크") \
                    .replace("Disk Read Bytes/sec", "디스크 읽기 바이트/초") \
                    .replace("Disk Write Bytes/sec", "디스크 쓰기 바이트/초")
                rc = pdh.PdhAddCounterW(self.query, ko_counter, 0, ctypes.byref(h))
            if rc == ERROR_SUCCESS:
                self.counters[d][kind] = h

    def remove_drive(self, d):
        if d in self.counters:
            for h in self.counters[d].values():
                pdh.PdhRemoveCounter(h)
            del self.counters[d]
        self.net_drives.pop(d, None)

    def refresh_network_mappings(self):
        """같은 드라이브 문자가 다른 공유로 재매핑된 경우 대비"""
        for d in list(self.net_drives):
            self.net_drives[d] = network_unc(d)

    def sample(self):
        if not self.counters and not self.net_drives:
            return {}
        pdh.PdhCollectQueryData(self.query)
        result = {}

        # 로컬 드라이브: LogicalDisk 카운터
        for d, kinds in list(self.counters.items()):
            result[d] = {}
            for kind, h in kinds.items():
                status = wt.DWORD()
                value = PDH_FMT_COUNTERVALUE_DOUBLE()
                rc = pdh.PdhGetFormattedCounterValue(
                    h, PDH_FMT_DOUBLE, ctypes.byref(status), ctypes.byref(value)
                )
                result[d][kind] = value.doubleValue if rc == ERROR_SUCCESS else 0.0

        # 리디렉터 쿼리는 네트워크 드라이브 유무와 관계없이 매번 수집 (rate 계산 간격 유지)
        global_write = 0.0
        if self.redir_query:
            try:
                pdh.PdhCollectQueryData(self.redir_query)
                if self.redir_write:
                    global_write = read_single_counter(self.redir_write)
            except Exception:
                log_error("redirector sample")

        # 네트워크 드라이브: SMB Client Shares 카운터
        if self.net_drives:
            tables = {}
            try:
                tables = {kind: read_counter_array(h) for kind, h in self.smb_counters.items()}
            except Exception:
                log_error("SMB counter array")

            for d, unc in self.net_drives.items():
                result[d] = {
                    "read": lookup_share(tables.get("read", {}), unc),
                    "write": lookup_share(tables.get("write", {}), unc),
                }

            # SMB 공유 카운터에 쓰기가 전혀 잡히지 않으면 리디렉터 값으로 보완
            try:
                smb_write_total = sum(result[d]["write"] for d in self.net_drives)
                if smb_write_total <= 0 and global_write > 0:
                    for d in self._write_targets(tables):
                        result[d]["write"] = global_write
            except Exception:
                log_error("redirector write fallback")

            if DEBUG:
                dbg("-" * 60)
                dbg(f"[debug] net_drives={self.net_drives}")
                for kind, table in tables.items():
                    dbg(f"[debug]   {kind}: {table}")
                dbg(f"[debug]   redirector write={global_write:,.0f}")
                dbg(f"[debug]   result={ {d: result[d] for d in self.net_drives} }")
        return result

    def _write_targets(self, tables):
        """리디렉터 쓰기 값(전체 합계)을 표시할 네트워크 드라이브 결정"""
        uncs = {u for u in self.net_drives.values() if u}
        if len(uncs) <= 1:
            return list(self.net_drives)

        # 여러 공유가 있으면 메타데이터 요청(파일 열기/닫기 등)이 있는 공유를 우선
        meta = tables.get("meta", {})
        active = {u for u in uncs if lookup_share(meta, u) > 0}
        if len(active) == 1:
            return [d for d, u in self.net_drives.items() if u in active]

        # 구분할 수 없으면 모든 네트워크 드라이브에 표시
        return list(self.net_drives)

    def close(self):
        if self.query:
            pdh.PdhCloseQuery(self.query)
            self.query = None
        if self.redir_query:
            pdh.PdhCloseQuery(self.redir_query)
            self.redir_query = None


class MEMORYSTATUSEX(ctypes.Structure):
    _fields_ = [
        ("dwLength", wt.DWORD),
        ("dwMemoryLoad", wt.DWORD),
        ("ullTotalPhys", ctypes.c_ulonglong),
        ("ullAvailPhys", ctypes.c_ulonglong),
        ("ullTotalPageFile", ctypes.c_ulonglong),
        ("ullAvailPageFile", ctypes.c_ulonglong),
        ("ullTotalVirtual", ctypes.c_ulonglong),
        ("ullAvailVirtual", ctypes.c_ulonglong),
        ("ullAvailExtendedVirtual", ctypes.c_ulonglong),
    ]


kernel32.GlobalMemoryStatusEx.argtypes = [ctypes.POINTER(MEMORYSTATUSEX)]
kernel32.GlobalMemoryStatusEx.restype = wt.BOOL


class SystemMonitor:
    """CPU 사용률(전체 코어 평균)과 가용 메모리 조회"""

    def __init__(self):
        self.query = PDH_HQUERY()
        self.cpu = None
        if pdh.PdhOpenQueryW(None, 0, ctypes.byref(self.query)) != ERROR_SUCCESS:
            self.query = None
            return
        # 작업 관리자와 같은 기준(% Processor Utility)을 우선 사용, 없으면 % Processor Time
        for path in (
            "\\Processor Information(_Total)\\% Processor Utility",
            "\\Processor(_Total)\\% Processor Time",
        ):
            h = PDH_HCOUNTER()
            if pdh.PdhAddEnglishCounterW(self.query, path, 0, ctypes.byref(h)) == ERROR_SUCCESS:
                self.cpu = h
                dbg(f"[debug] CPU counter: {path}")
                break
        pdh.PdhCollectQueryData(self.query)

    def sample(self):
        """(cpu_percent, free_bytes, total_bytes) 반환"""
        cpu = 0.0
        if self.query and self.cpu:
            pdh.PdhCollectQueryData(self.query)
            cpu = max(0.0, min(100.0, read_single_counter(self.cpu)))

        mem = MEMORYSTATUSEX()
        mem.dwLength = ctypes.sizeof(MEMORYSTATUSEX)
        if kernel32.GlobalMemoryStatusEx(ctypes.byref(mem)):
            return cpu, mem.ullAvailPhys, mem.ullTotalPhys
        return cpu, 0, 0

    def close(self):
        if self.query:
            pdh.PdhCloseQuery(self.query)
            self.query = None


def fmt_rate(v):
    if v >= 1024**3:
        return f"{int(round(v / 1024**3))} GB/s"
    if v >= 1024**2:
        return f"{int(round(v / 1024**2))} MB/s"
    if v >= 1024:
        return f"{int(round(v / 1024))} KB/s"
    return f"{int(v)} B/s"


def get_dynamic_max(rate):
    kb100 = 100 * 1024
    mb1   = 1 * 1024**2
    mb10  = 10 * 1024**2
    mb100 = 100 * 1024**2
    mb300 = 300 * 1024**2

    if rate <= kb100:
        return kb100
    if rate <= mb1:
        return mb1
    if rate <= mb10:
        return mb10
    if rate <= mb100:
        return mb100
    if rate <= mb300:
        return mb300
    return rate * 1.2


BAR_WIDTH = 85
BAR_HEIGHT = 5
CANVAS_HEIGHT = 11


class MiniDiskBar:
    def __init__(self, parent, fill_color="#3a86ff", bg_color="#2b2d42"):
        self.canvas = tk.Canvas(
            parent,
            width=BAR_WIDTH,
            height=CANVAS_HEIGHT,
            bg="#18181b",
            highlightthickness=0,
        )
        self.fill_color = fill_color
        self.bar_bg_color = bg_color

        self.y0 = (CANVAS_HEIGHT - BAR_HEIGHT) // 2
        self.y1 = self.y0 + BAR_HEIGHT

        self.track = self.canvas.create_rectangle(
            0, self.y0, BAR_WIDTH, self.y1, fill=self.bar_bg_color, width=0
        )
        self.bar = self.canvas.create_rectangle(
            0, self.y0, 0, self.y1, fill=self.fill_color, width=0
        )
        self.text = self.canvas.create_text(
            BAR_WIDTH // 2,
            CANVAS_HEIGHT // 2,
            text="0 B/s",
            fill="#ffffff",
            font=("Segoe UI", 7, "bold"),
        )

    def pack(self, **kwargs):
        self.canvas.pack(**kwargs)

    def set(self, ratio, text):
        """막대 비율(0~1)과 표시 문자열 직접 지정"""
        ratio = max(0.0, min(1.0, ratio))
        fill_w = max(1, int(BAR_WIDTH * ratio)) if ratio > 0 else 0
        self.canvas.coords(self.bar, 0, self.y0, fill_w, self.y1)
        self.canvas.itemconfig(self.text, text=text)

    def update(self, current_rate):
        max_scale = get_dynamic_max(current_rate)
        ratio = current_rate / max_scale if max_scale > 0 else 0.0
        self.set(ratio if current_rate > 0 else 0.0, fmt_rate(current_rate))


def create_tray_image():
    """트레이 아이콘에 표시할 심플한 디스크 아이콘 동적 생성"""
    img = Image.new("RGBA", (64, 64), (0, 0, 0, 0))
    draw = ImageDraw.Draw(img)
    draw.rectangle((8, 12, 56, 52), fill="#27272a", outline="#3b82f6", width=4)
    draw.ellipse((40, 20, 48, 28), fill="#10b981")
    draw.rectangle((16, 40, 48, 44), fill="#94a3b8")
    return img


class App:
    def __init__(self, root):
        self.root = root
        self.root.configure(bg="#18181b")

        # 타이틀바 제거
        self.root.overrideredirect(True)

        # 항상 위 기본 활성화
        self.always_on_top = True
        self.root.attributes("-topmost", self.always_on_top)

        # Taskbar에서 숨기기 (Tool Window 확장 스타일 적용)
        self.hide_from_taskbar()

        # 창 드래그 이동을 위한 오프셋 변수
        self.drag_x = 0
        self.drag_y = 0

        # 우클릭 컨텍스트 메뉴
        self.context_menu = tk.Menu(
            self.root, tearoff=0,
            bg="#27272a", fg="#e4e4e7",
            activebackground="#3b82f6", activeforeground="#ffffff",
            bd=0, font=("Segoe UI", 9),
        )
        self.topmost_var = tk.BooleanVar(value=self.always_on_top)
        self.context_menu.add_checkbutton(
            label="Always on top",
            variable=self.topmost_var,
            command=self.on_context_topmost,
            selectcolor="#e4e4e7",
        )
        self.context_menu.add_separator()
        self.context_menu.add_command(label="종료", command=self.close)

        initial = drives()
        self.monitor = DiskMonitor(initial)
        self.rows = {}

        self.body = tk.Frame(root, bg="#18181b", padx=4, pady=3)
        self.body.pack()

        # 맨 위: CPU / 메모리
        self.sysmon = SystemMonitor()
        self.add_system_ui()

        # UI 생성 및 최초 드라이브 등록
        for d in initial:
            self.add_drive_ui(d)

        # 전체 위젯 드래그 이벤트 바인딩
        self.bind_drag(self.root)

        # 트레이 아이콘 설정 및 백그라운드 구동
        self.tray_icon = None
        self.setup_tray()

        self.update()

    def hide_from_taskbar(self):
        """Windows API를 호출하여 Taskbar 아이콘 숨김 처리"""
        self.root.update_idletasks()
        hwnd = user32.GetParent(self.root.winfo_id())
        if not hwnd:
            hwnd = self.root.winfo_id()
        style = user32.GetWindowLongW(hwnd, GWL_EXSTYLE)
        style = (style | WS_EX_TOOLWINDOW) & ~WS_EX_APPWINDOW
        user32.SetWindowLongW(hwnd, GWL_EXSTYLE, style)

    def bind_drag(self, widget):
        """위젯 및 모든 자식 위젯에 드래그 이동 / 우클릭 메뉴 바인딩"""
        widget.bind("<Button-1>", self.start_drag, add="+")
        widget.bind("<B1-Motion>", self.do_drag, add="+")
        widget.bind("<Button-3>", self.show_context_menu, add="+")
        for child in widget.winfo_children():
            self.bind_drag(child)

    def show_context_menu(self, event):
        try:
            self.context_menu.tk_popup(event.x_root, event.y_root)
        finally:
            self.context_menu.grab_release()
        return "break"  # 부모 위젯으로 이벤트가 전파되어 메뉴가 중복 호출되는 것 방지

    def start_drag(self, event):
        self.drag_x = event.x_root - self.root.winfo_x()
        self.drag_y = event.y_root - self.root.winfo_y()

    def do_drag(self, event):
        x = event.x_root - self.drag_x
        y = event.y_root - self.drag_y
        self.root.geometry(f"+{x}+{y}")

    def add_system_ui(self):
        """CPU 사용률 / 가용 메모리 행 (드라이브 행의 R/W 열과 정렬)"""
        row = tk.Frame(self.body, bg="#18181b")
        row.pack(fill="x", pady=1)

        tk.Label(
            row, text="SYS", font=("Segoe UI", 8, "bold"),
            fg="#a1a1aa", bg="#18181b", width=3, anchor="w"
        ).pack(side="left", padx=(0, 2))

        tk.Label(
            row, text="C", font=("Segoe UI", 7, "bold"),
            fg="#fbbf24", bg="#18181b", width=1
        ).pack(side="left", padx=(0, 1))

        self.cpu_bar = MiniDiskBar(row, fill_color="#d97706", bg_color="#27272a")
        self.cpu_bar.set(0, "CPU 0%")
        self.cpu_bar.pack(side="left", padx=(0, 4))

        tk.Label(
            row, text="M", font=("Segoe UI", 7, "bold"),
            fg="#60a5fa", bg="#18181b", width=1
        ).pack(side="left", padx=(0, 1))

        self.mem_bar = MiniDiskBar(row, fill_color="#2563eb", bg_color="#27272a")
        self.mem_bar.set(0, "- GB free")
        self.mem_bar.pack(side="left")

        # 드라이브 목록과 구분선
        tk.Frame(self.body, bg="#3f3f46", height=1).pack(fill="x", pady=(2, 1))

    def update_system_ui(self):
        cpu, free, total = self.sysmon.sample()
        self.cpu_bar.set(cpu / 100.0, f"CPU {int(round(cpu))}%")
        if total:
            used_ratio = 1.0 - free / total   # 막대 = 사용 중인 비율
            self.mem_bar.set(used_ratio, f"{free / 1024**3:.1f} GB free")

    def add_drive_ui(self, d):
        row = tk.Frame(self.body, bg="#18181b")

        # 드라이브 문자 순서를 유지하도록 삽입 위치 결정
        later = sorted(x for x in self.rows if x > d)
        if later:
            row.pack(fill="x", pady=1, before=self.rows[later[0]]["frame"])
        else:
            row.pack(fill="x", pady=1)

        # 네트워크 드라이브는 드라이브 문자 색상으로 구분
        label_color = "#60a5fa" if is_network_drive(d) else "#e4e4e7"
        tk.Label(
            row, text=d, font=("Segoe UI", 8, "bold"),
            fg=label_color, bg="#18181b", width=3, anchor="w"
        ).pack(side="left", padx=(0, 2))

        tk.Label(
            row, text="R", font=("Segoe UI", 7, "bold"),
            fg="#06d6a0", bg="#18181b", width=1
        ).pack(side="left", padx=(0, 1))

        read_bar = MiniDiskBar(row, fill_color="#059669", bg_color="#27272a")
        read_bar.pack(side="left", padx=(0, 4))

        tk.Label(
            row, text="W", font=("Segoe UI", 7, "bold"),
            fg="#f72585", bg="#18181b", width=1
        ).pack(side="left", padx=(0, 1))

        write_bar = MiniDiskBar(row, fill_color="#dc2626", bg_color="#27272a")
        write_bar.pack(side="left")

        self.rows[d] = {
            "frame": row,
            "read_bar": read_bar,
            "write_bar": write_bar
        }
        self.bind_drag(row)

    def remove_drive_ui(self, d):
        if d in self.rows:
            self.rows[d]["frame"].destroy()
            del self.rows[d]

    def sync_drives(self):
        """드라이브 추가/제거 감지 및 동적 갱신"""
        current = set(drives())
        monitored = set(self.rows.keys())
        if current != monitored:
            dbg(f"[debug] drives changed: {sorted(monitored)} -> {sorted(current)}")

        # 추가된 드라이브 반영 (정렬 위치에 삽입)
        for d in sorted(current - monitored):
            self.monitor.add_drive(d)
            self.add_drive_ui(d)

        # 제거된 드라이브 반영
        for d in monitored - current:
            self.monitor.remove_drive(d)
            self.remove_drive_ui(d)

        # 네트워크 드라이브 재매핑 반영
        self.monitor.refresh_network_mappings()

    def update(self):
        try:
            self.update_system_ui()
        except Exception:
            log_error("system update")

        try:
            self.sync_drives()
            data = self.monitor.sample()
            for d, vals in data.items():
                if d not in self.rows:
                    continue
                r = vals.get("read", 0.0)
                w = vals.get("write", 0.0)
                self.rows[d]["read_bar"].update(r)
                self.rows[d]["write_bar"].update(w)
        except Exception:
            log_error("update")

        self.root.after(500, self.update)

    # 트레이 아이콘 구성
    def setup_tray(self):
        menu = pystray.Menu(
            pystray.MenuItem(
                "Always on top",
                self.toggle_topmost,
                checked=lambda item: self.always_on_top,
            ),
            pystray.MenuItem("Exit", self.close),
        )
        self.tray_icon = pystray.Icon("DiskMini", create_tray_image(), "Disk Monitor", menu)
        threading.Thread(target=self.tray_icon.run, daemon=True).start()

    def set_topmost(self, value):
        """항상 위 상태 변경 (Tk 메인 스레드에서만 호출)"""
        self.always_on_top = value
        self.topmost_var.set(value)
        self.root.attributes("-topmost", value)
        if self.tray_icon:
            try:
                self.tray_icon.update_menu()  # 트레이 메뉴 체크 표시 갱신
            except Exception:
                pass

    def on_context_topmost(self):
        # 체크버튼이 이미 변수 값을 뒤집어 놓은 상태
        self.set_topmost(self.topmost_var.get())

    def toggle_topmost(self, icon, item):
        # 트레이 스레드에서 호출되므로 Tk 작업은 메인 스레드로 넘김
        self.root.after(0, lambda: self.set_topmost(not self.always_on_top))

    def close(self, icon=None, item=None):
        if self.tray_icon:
            self.tray_icon.stop()
        self.monitor.close()
        self.sysmon.close()
        self.root.after(0, self.root.destroy)


if __name__ == "__main__":
    if sys.platform != "win32":
        raise SystemExit("Windows 환경에서 실행해야 합니다.")

    setup_debug_console()
    dump_drive_diag()

    root = tk.Tk()
    app = App(root)
    root.mainloop()
