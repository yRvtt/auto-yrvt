"""Leitura de XP pela tela usando OCR (Tesseract ou Windows OCR).

Ordem: Tesseract (se instalado) → winocr (Windows OCR) → manual.
O módulo é opcional: a UI funciona com valor digitado manualmente.
"""

import asyncio
import logging
import os
import re
import shutil
import subprocess
import sys

import win32gui

from .core import crop, grab

log = logging.getLogger(__name__)

_TESSERACT_PATH = None
_WINOCR_OK = None


def _tesseract_dir():
    """Pasta onde o Tesseract fica dentro do APPDATA do app."""
    base = (os.environ.get("AUTO_YRVT_CONFIG_DIR")
            or os.environ.get("APPDATA")
            or os.path.expanduser("~"))
    return os.path.join(base, "AutoYrvt", "tesseract")


def tesseract_available():
    global _TESSERACT_PATH
    if _TESSERACT_PATH is not None:
        return bool(_TESSERACT_PATH)
    # 1) no PATH do sistema
    path = shutil.which("tesseract")
    if path:
        _TESSERACT_PATH = path
        return True
    # 2) na pasta do app
    local = os.path.join(_tesseract_dir(), "tesseract.exe")
    if os.path.isfile(local):
        _TESSERACT_PATH = local
        return True
    # 3) instalação padrão do Windows
    for prog in (os.environ.get("ProgramFiles", r"C:\Program Files"),
                 os.environ.get("ProgramFiles(x86)", r"C:\Program Files (x86)")):
        candidate = os.path.join(prog, "Tesseract-OCR", "tesseract.exe")
        if os.path.isfile(candidate):
            _TESSERACT_PATH = candidate
            return True
    _TESSERACT_PATH = ""
    return False


def install_tesseract(log_fn=None):
    """Baixa o instalador do Tesseract e instala silenciosamente."""
    log_fn = log_fn or log.info
    url = ("https://github.com/UB-Mannheim/tesseract/releases/download/"
           "v5.5.0.20241111/tesseract-ocr-w64-setup-5.5.0.20241111.exe")
    dest_dir = _tesseract_dir()
    os.makedirs(dest_dir, exist_ok=True)
    installer = os.path.join(dest_dir, "tesseract_setup.exe")

    log_fn("Baixando Tesseract OCR (~65MB)...")
    try:
        import urllib.request
        req = urllib.request.Request(url, headers={"User-Agent": "auto-yrvt"})
        with urllib.request.urlopen(req, timeout=120) as r, \
                open(installer, "wb") as f:
            while True:
                chunk = r.read(65536)
                if not chunk:
                    break
                f.write(chunk)
    except Exception as exc:
        log_fn(f"Erro ao baixar Tesseract: {exc}")
        return False

    log_fn("Instalando Tesseract...")
    try:
        subprocess.run(
            [installer, "/S", f"/D={dest_dir}"],
            timeout=120, check=False)
    except Exception as exc:
        log_fn(f"Erro na instalação: {exc}")
        return False

    global _TESSERACT_PATH
    exe = os.path.join(dest_dir, "tesseract.exe")
    if os.path.isfile(exe):
        _TESSERACT_PATH = exe
        log_fn("Tesseract instalado com sucesso.")
        return True
    log_fn("Instalação concluída mas tesseract.exe não encontrado.")
    return False


def winocr_available():
    global _WINOCR_OK
    if _WINOCR_OK is None:
        try:
            import winocr  # noqa: F401
            from PIL import Image  # noqa: F401
            _WINOCR_OK = True
        except Exception:
            _WINOCR_OK = False
    return _WINOCR_OK


def ocr_available():
    return tesseract_available() or winocr_available()


def parse_percent(text):
    """'12,34%' -> 12.34. Retorna None se não houver porcentagem válida."""
    if not text:
        return None
    match = re.search(r"(\d{1,4}(?:[.,]\d+)?)\s*%", text)
    if not match:
        numbers = re.findall(r"\d{1,4}(?:[.,]\d+)?", text)
        if not numbers:
            return None
        match_text = numbers[-1]
    else:
        match_text = match.group(1)
    try:
        value = float(match_text.replace(",", "."))
    except ValueError:
        return None
    if (value > 100 and "." not in match_text and "," not in match_text
            and len(match_text) == 4):
        value /= 100.0
    return value if 0.0 <= value <= 100.0 else None


def _capture(hwnd, x, y, w, h):
    """Captura região da janela via PrintWindow; fallback pra ImageGrab."""
    frame = grab(hwnd)
    if frame:
        fw, fh, data = frame
        # clampar pra não estourar a borda
        actual_w = min(w, fw - x)
        actual_h = min(h, fh - y)
        if actual_w <= 0 or actual_h <= 0:
            log.warning("região de XP fora da janela (%dx%d em %dx%d)",
                        w, h, fw, fh)
            return None
        from PIL import Image
        raw = crop(fw, fh, data, x, y, actual_w, actual_h)
        if len(raw) != actual_w * actual_h * 4:
            log.warning("crop retornou %d bytes, esperava %d",
                        len(raw), actual_w * actual_h * 4)
            return None
        return Image.frombytes("RGBA", (actual_w, actual_h), raw, "raw", "BGRA")
    try:
        if win32gui.GetForegroundWindow() != hwnd:
            return None
        from PIL import ImageGrab
        left, top = win32gui.ClientToScreen(hwnd, (x, y))
        return ImageGrab.grab(bbox=(left, top, left + w, top + h),
                              all_screens=True).convert("RGBA")
    except Exception as exc:
        log.warning("ImageGrab fallback falhou: %s", exc)
        return None


def _preprocess(image):
    """Gera variantes pré-processadas pra OCR."""
    from PIL import ImageOps, ImageFilter
    gray = image.convert("L")
    # binarização Otsu simulada: threshold no meio do histograma
    hist = gray.histogram()
    total = sum(hist)
    cum = 0
    threshold = 128
    for i, count in enumerate(hist):
        cum += count
        if cum >= total * 0.5:
            threshold = i
            break
    binary = gray.point(lambda p: 255 if p > threshold else 0)
    variants = [
        image.convert("RGB"),
        gray,
        ImageOps.autocontrast(gray),
        binary,
        ImageOps.invert(binary),
    ]
    return variants


def _ocr_tesseract(image, lang="por"):
    """OCR via Tesseract CLI. Retorna texto ou ''."""
    if not tesseract_available():
        return ""
    import tempfile
    with tempfile.NamedTemporaryFile(suffix=".png", delete=False) as tmp:
        tmp_path = tmp.name
    try:
        image.save(tmp_path)
        tess_lang = {"en": "eng", "pt-BR": "por", "pt": "por"}.get(lang, lang)
        result = subprocess.run(
            [_TESSERACT_PATH, tmp_path, "stdout",
             "-l", tess_lang, "--psm", "7",
             "-c", "tessedit_char_whitelist=0123456789.,% "],
            capture_output=True, text=True, timeout=10)
        return (result.stdout or "").strip()
    except Exception as exc:
        log.debug("Tesseract falhou: %s", exc)
        return ""
    finally:
        try:
            os.unlink(tmp_path)
        except Exception:
            pass


def _ocr_winocr(image, lang="en"):
    """OCR via Windows OCR (winocr). Retorna texto ou ''."""
    if not winocr_available():
        return ""
    try:
        import winocr
        result = asyncio.run(
            winocr.to_coroutine(winocr.recognize_pil(image, lang)))
        return (result.text or "").strip()
    except Exception as exc:
        log.debug("winocr falhou: %s", exc)
        return ""


def read_percent(hwnd, region, lang="en"):
    """(valor, texto_ocr) da região. valor é None se não conseguiu ler."""
    if not region:
        return None, "região não calibrada"
    x, y, w, h = [int(v) for v in region]
    if w <= 0 or h <= 0:
        return None, "região inválida"
    image = _capture(hwnd, x, y, w, h)
    if image is None:
        return None, "captura falhou (jogo minimizado ou atrás?)"
    if not ocr_available():
        return None, "nenhum OCR disponível (instale Tesseract ou winocr+Pillow)"

    variants = _preprocess(image)
    last_text = ""
    languages = []
    for language in (lang, "pt-BR", "en", "pt"):
        if language and language not in languages:
            languages.append(language)

    # Tesseract primeiro (mais robusto com fontes de jogo)
    if tesseract_available():
        for variant in variants:
            scaled = variant.resize((max(1, variant.width * 4),
                                     max(1, variant.height * 4)))
            for language in languages:
                text = _ocr_tesseract(scaled, language)
                if text:
                    last_text = text
                    value = parse_percent(text)
                    if value is not None:
                        return value, text

    # winocr fallback
    if winocr_available():
        for language in languages:
            for variant in variants:
                try:
                    scaled = variant.resize((max(1, variant.width * 4),
                                             max(1, variant.height * 4)))
                    text = _ocr_winocr(scaled, language)
                    if text:
                        last_text = text
                        value = parse_percent(text)
                        if value is not None:
                            return value, text
                except Exception as exc:
                    log.debug("variante OCR falhou: %s", exc)
                    continue

    return None, last_text or "OCR não encontrou número"
