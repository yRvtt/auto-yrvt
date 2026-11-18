"""Leitura de XP pela tela usando o OCR nativo do Windows.

O módulo é opcional: se `winocr`/Pillow não estiverem instalados, a UI continua
funcionando com o valor digitado manualmente.
"""

import asyncio
import re

import win32gui

from .core import crop, grab

_OCR_OK = None


def ocr_available():
    global _OCR_OK
    if _OCR_OK is None:
        try:
            import winocr  # noqa: F401
            from PIL import Image  # noqa: F401
            _OCR_OK = True
        except Exception:
            _OCR_OK = False
    return _OCR_OK


def parse_percent(text):
    """'12,34%' -> 12.34. Retorna None se não houver porcentagem válida."""
    if not text:
        return None
    match = re.search(r"(\d{1,4}(?:[.,]\d+)?)\s*%", text)
    if not match:
        # OCR às vezes come o símbolo %; usa o último número do recorte.
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
    """Tenta PrintWindow; se falhar, captura a tela só se o jogo estiver na frente."""
    frame = grab(hwnd)
    if frame:
        fw, fh, data = frame
        from PIL import Image
        return Image.frombytes("RGBA", (w, h),
                               crop(fw, fh, data, x, y, w, h),
                               "raw", "BGRA")
    try:
        if win32gui.GetForegroundWindow() != hwnd:
            return None
        from PIL import ImageGrab
        left, top = win32gui.ClientToScreen(hwnd, (x, y))
        return ImageGrab.grab(bbox=(left, top, left + w, top + h),
                              all_screens=True).convert("RGBA")
    except Exception:
        return None


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
        return None, "winocr/Pillow não instalados"

    import winocr
    from PIL import ImageOps

    gray = image.convert("L")
    variants = [image.convert("RGB"), gray, ImageOps.autocontrast(gray)]
    last_text = ""
    languages = []
    for language in (lang, "en", "pt-BR", "pt"):
        if language and language not in languages:
            languages.append(language)
    for language in languages:
        for variant in variants:
            try:
                variant = variant.resize((max(1, variant.width * 4),
                                          max(1, variant.height * 4)))
                result = asyncio.run(
                    winocr.to_coroutine(winocr.recognize_pil(variant, language)))
                last_text = result.text or ""
                value = parse_percent(last_text)
                if value is not None:
                    return value, last_text.strip()
            except Exception:
                continue
    return None, last_text.strip() or "OCR não encontrou número"
