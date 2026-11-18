"""Leitura de memória do processo MIR4 (só leitura, sem injeção).

Fluxo:
1. scan_float(pid, valor_atual) → lista de endereços candidatos
2. Usuário ganha XP, chama scan_float(pid, novo_valor, candidatos)
3. Repete até sobrar 1-5 endereços
4. read_float(pid, endereço) → valor atual direto da RAM
"""

import ctypes
import ctypes.wintypes
import logging
import struct

log = logging.getLogger(__name__)

kernel32 = ctypes.windll.kernel32

PROCESS_VM_READ = 0x0010
PROCESS_QUERY_INFORMATION = 0x0400
MEM_COMMIT = 0x1000
PAGE_READABLE = {0x02, 0x04, 0x06, 0x20, 0x40, 0x80}


class MEMORY_BASIC_INFORMATION(ctypes.Structure):
    _fields_ = [
        ("BaseAddress", ctypes.c_void_p),
        ("AllocationBase", ctypes.c_void_p),
        ("AllocationProtect", ctypes.wintypes.DWORD),
        ("RegionSize", ctypes.c_size_t),
        ("State", ctypes.wintypes.DWORD),
        ("Protect", ctypes.wintypes.DWORD),
        ("Type", ctypes.wintypes.DWORD),
    ]


def open_process(pid):
    """Abre o processo pra leitura. Retorna handle ou None."""
    handle = kernel32.OpenProcess(
        PROCESS_VM_READ | PROCESS_QUERY_INFORMATION, False, int(pid))
    return handle if handle else None


def close_process(handle):
    if handle:
        kernel32.CloseHandle(handle)


def _readable_regions(handle):
    """Gera (base, size) de cada região legível."""
    mbi = MEMORY_BASIC_INFORMATION()
    addr = 0
    max_addr = (1 << 47) - 1  # usermode limit x64
    while addr < max_addr:
        result = kernel32.VirtualQueryEx(
            handle, ctypes.c_void_p(addr),
            ctypes.byref(mbi), ctypes.sizeof(mbi))
        if not result:
            break
        if (mbi.State == MEM_COMMIT
                and mbi.Protect in PAGE_READABLE
                and mbi.RegionSize > 0):
            yield mbi.BaseAddress, mbi.RegionSize
        addr = (mbi.BaseAddress or 0) + mbi.RegionSize
        if addr <= (mbi.BaseAddress or 0):
            break


def _read_bytes(handle, address, size):
    buf = ctypes.create_string_buffer(size)
    read = ctypes.c_size_t(0)
    ok = kernel32.ReadProcessMemory(
        handle, ctypes.c_void_p(address), buf, size, ctypes.byref(read))
    if not ok or read.value == 0:
        return None
    return buf.raw[:read.value]


def read_float(handle, address):
    """Lê um float (4 bytes) do endereço. Retorna None se falhar."""
    data = _read_bytes(handle, address, 4)
    if data and len(data) == 4:
        return struct.unpack("<f", data)[0]
    return None


def read_double(handle, address):
    """Lê um double (8 bytes) do endereço."""
    data = _read_bytes(handle, address, 8)
    if data and len(data) == 8:
        return struct.unpack("<d", data)[0]
    return None


def read_int32(handle, address):
    data = _read_bytes(handle, address, 4)
    if data and len(data) == 4:
        return struct.unpack("<i", data)[0]
    return None


def read_wstring(handle, address, max_chars=256):
    """Lê string UTF-16LE (UE4 usa wchar_t)."""
    data = _read_bytes(handle, address, max_chars * 2)
    if not data:
        return None
    try:
        null = data.find(b"\x00\x00")
        if null >= 0:
            # alinhar no limite de 2 bytes
            null = null - (null % 2)
            data = data[:null]
        return data.decode("utf-16-le")
    except Exception:
        return None


def scan_float(handle, target, tolerance=0.01, candidates=None,
               progress_fn=None):
    """Varre memória procurando float == target (±tolerance).

    Se candidates for None, faz scan completo (~2-10s).
    Se candidates for uma lista de endereços, filtra só esses (instantâneo).
    progress_fn(pct) é chamado com 0-100 durante scan completo.
    """
    target_f = float(target)
    results = []

    if candidates is not None:
        for addr in candidates:
            val = read_float(handle, addr)
            if val is not None and abs(val - target_f) <= tolerance:
                results.append(addr)
        return results

    # scan completo
    regions = list(_readable_regions(handle))
    total_bytes = sum(size for _, size in regions)
    scanned = 0
    packed = struct.pack("<f", target_f)

    for base, size in regions:
        # ler em blocos de 1MB pra não alocar demais
        block_size = min(size, 1 << 20)
        offset = 0
        while offset < size:
            chunk_size = min(block_size, size - offset)
            data = _read_bytes(handle, base + offset, chunk_size)
            if data:
                # busca por float alinhado a 4 bytes
                for i in range(0, len(data) - 3, 4):
                    val = struct.unpack_from("<f", data, i)[0]
                    if abs(val - target_f) <= tolerance:
                        results.append(base + offset + i)
            scanned += chunk_size
            offset += chunk_size
            if progress_fn and total_bytes > 0:
                progress_fn(min(100, int(scanned * 100 / total_bytes)))

    return results


def scan_int(handle, target, candidates=None, progress_fn=None):
    """Varre memória procurando int32 == target."""
    target_i = int(target)
    results = []

    if candidates is not None:
        for addr in candidates:
            val = read_int32(handle, addr)
            if val is not None and val == target_i:
                results.append(addr)
        return results

    regions = list(_readable_regions(handle))
    total_bytes = sum(size for _, size in regions)
    scanned = 0
    packed = struct.pack("<i", target_i)

    for base, size in regions:
        block_size = min(size, 1 << 20)
        offset = 0
        while offset < size:
            chunk_size = min(block_size, size - offset)
            data = _read_bytes(handle, base + offset, chunk_size)
            if data:
                start = 0
                while True:
                    pos = data.find(packed, start)
                    if pos < 0:
                        break
                    if pos % 4 == 0:
                        results.append(base + offset + pos)
                    start = pos + 1
            scanned += chunk_size
            offset += chunk_size
            if progress_fn and total_bytes > 0:
                progress_fn(min(100, int(scanned * 100 / total_bytes)))

    return results


def scan_wstring(handle, text, candidates=None, progress_fn=None):
    """Varre memória procurando string UTF-16LE."""
    encoded = text.encode("utf-16-le")
    results = []

    if candidates is not None:
        for addr in candidates:
            data = _read_bytes(handle, addr, len(encoded) + 2)
            if data and encoded in data:
                results.append(addr)
        return results

    regions = list(_readable_regions(handle))
    total_bytes = sum(size for _, size in regions)
    scanned = 0

    for base, size in regions:
        block_size = min(size, 1 << 20)
        overlap = len(encoded)
        offset = 0
        while offset < size:
            chunk_size = min(block_size, size - offset)
            data = _read_bytes(handle, base + offset, chunk_size)
            if data:
                start = 0
                while True:
                    pos = data.find(encoded, start)
                    if pos < 0:
                        break
                    results.append(base + offset + pos)
                    start = pos + 1
            scanned += chunk_size
            # overlap pra não perder matches na fronteira
            offset += max(1, chunk_size - overlap)
            if progress_fn and total_bytes > 0:
                progress_fn(min(100, int(scanned * 100 / total_bytes)))

    return results
