"""Captura de tela: grab, crop, diff de pixels BGRA."""

import ctypes
import ctypes.wintypes

user32 = ctypes.windll.user32
gdi32 = ctypes.windll.gdi32

ROOM_W, ROOM_H = 340, 26
ROOM_MATCH = 30


class BMI(ctypes.Structure):
    _fields_ = [("biSize", ctypes.wintypes.DWORD),
                ("biWidth", ctypes.wintypes.LONG),
                ("biHeight", ctypes.wintypes.LONG),
                ("biPlanes", ctypes.wintypes.WORD),
                ("biBitCount", ctypes.wintypes.WORD),
                ("biCompression", ctypes.wintypes.DWORD),
                ("biSizeImage", ctypes.wintypes.DWORD),
                ("biXPelsPerMeter", ctypes.wintypes.LONG),
                ("biYPelsPerMeter", ctypes.wintypes.LONG),
                ("biClrUsed", ctypes.wintypes.DWORD),
                ("biClrImportant", ctypes.wintypes.DWORD)]


def grab(hwnd):
    """(w, h, pixels BGRA bottom-up) da área cliente, mesmo coberta."""
    rect = ctypes.wintypes.RECT()
    if not user32.GetClientRect(hwnd, ctypes.byref(rect)):
        return None
    w, h = rect.right, rect.bottom
    if w <= 0 or h <= 0:
        return None
    hdc = user32.GetDC(hwnd)
    mem = gdi32.CreateCompatibleDC(hdc)
    bmp = gdi32.CreateCompatibleBitmap(hdc, w, h)
    if not mem or not bmp:
        if bmp:
            gdi32.DeleteObject(bmp)
        if mem:
            gdi32.DeleteDC(mem)
        user32.ReleaseDC(hwnd, hdc)
        return None
    gdi32.SelectObject(mem, bmp)
    ok = user32.PrintWindow(hwnd, mem, 2)  # PW_RENDERFULLCONTENT
    bmi = BMI()
    bmi.biSize = ctypes.sizeof(BMI)
    bmi.biWidth, bmi.biHeight = w, h
    bmi.biPlanes, bmi.biBitCount = 1, 32
    buf = ctypes.create_string_buffer(w * h * 4)
    gdi32.GetDIBits(mem, bmp, 0, h, buf, ctypes.byref(bmi), 0)
    gdi32.DeleteObject(bmp)
    gdi32.DeleteDC(mem)
    user32.ReleaseDC(hwnd, hdc)
    return (w, h, buf.raw) if ok else None


def crop(w, h, data, x, y, cw=ROOM_W, ch=ROOM_H):
    x = max(0, min(int(x), w - 1))
    y = max(0, min(int(y), h - 1))
    cw, ch = min(cw, w - x), min(ch, h - y)
    if cw <= 0 or ch <= 0:
        return b""
    out = bytearray(cw * ch * 4)
    row_bytes = cw * 4
    for row in range(ch):
        src = (h - 1 - (y + row)) * w * 4 + x * 4
        out[row * row_bytes:(row + 1) * row_bytes] = data[src:src + row_bytes]
    return bytes(out)


def diff(a, b):
    """Diferença média RGB euclidiana (0-441), amostrando cada 4° pixel."""
    if not a or not b:
        return 999.0
    if len(a) != len(b):
        try:
            from PIL import Image
            wa, ha = _guess_bgra_dims(a)
            wb, hb = _guess_bgra_dims(b)
            if wa and wb and wa != wb:
                img_b = Image.frombytes("RGBA", (wb, hb), b, "raw", "BGRA")
                img_b = img_b.resize((wa, ha), Image.LANCZOS)
                b = img_b.tobytes("raw", "BGRA")
        except Exception:
            return 999.0
    if len(a) != len(b):
        return 999.0
    total = 0.0
    n = 0
    for i in range(0, len(a) - 3, 16):
        dr = a[i + 2] - b[i + 2]
        dg = a[i + 1] - b[i + 1]
        db = a[i] - b[i]
        total += (dr * dr + dg * dg + db * db) ** 0.5
        n += 1
    return total / n if n else 999.0


def _guess_bgra_dims(data):
    """Estima w,h de dados BGRA assumindo proporção ~13:1 (ROOM_W:ROOM_H)."""
    total_px = len(data) // 4
    if total_px <= 0:
        return None, None
    for w in range(ROOM_W + 100, max(50, ROOM_W - 200), -1):
        if total_px % w == 0:
            return w, total_px // w
    return None, None


def is_black(data):
    if not data:
        return True
    for i in range(0, len(data) - 3, 400):
        if data[i] or data[i + 1] or data[i + 2]:
            return False
    return True
