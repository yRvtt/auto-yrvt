"""Introspecção de runtime UE4 via ReadProcessMemory (UE 4.23-4.27).

Encontra GNames e GObjects do Unreal Engine na memória do processo,
resolve nomes de classes/propriedades, e enumera objetos do jogador
com seus offsets — tudo read-only, sem injeção.
"""

import struct
import logging

from . import memory

log = logging.getLogger(__name__)

# ── UObject layout (UE4 4.27 x64) ──────────────────────────────
UOBJ_VFTABLE = 0x00
UOBJ_FLAGS   = 0x08
UOBJ_INDEX   = 0x0C
UOBJ_CLASS   = 0x10
UOBJ_FNAME   = 0x18   # ComparisonIndex(u32) + Number(u32)
UOBJ_OUTER   = 0x20
UOBJ_SIZE    = 0x28

# UStruct (extends UField extends UObject)
# UField: +0x28 Next (UField*)
# UStruct: +0x30 SuperStruct, ... +0x40 Children (UField*), +0x48 ChildProperties
# offsets variam por versão; estes são p/ 4.27 x64
UFIELD_NEXT = 0x28
USTRUCT_SUPER = 0x30
USTRUCT_CHILDREN = 0x38  # FField* (UE4.25+) or UField* (older)

# FField (UE 4.25+): substitui UField pra properties
FFIELD_CLASS  = 0x00  # FFieldClass*
FFIELD_OWNER  = 0x08  # variant (FFieldVariant)
FFIELD_NEXT   = 0x20  # FField*
FFIELD_FNAME  = 0x28  # FName (8 bytes)

# FProperty extends FField
FPROP_OFFSET  = 0x44  # int32: offset do campo dentro do objeto
FPROP_SIZE    = 0x48  # int32: tamanho do campo

# FNameEntryAllocator
ALLOC_LOCK    = 0x00   # SRWLOCK (8 bytes)
ALLOC_CUR_BLK = 0x08  # uint32
ALLOC_CUR_CUR = 0x0C  # uint32
ALLOC_BLOCKS  = 0x10  # Blocks[0] starts here (array of pointers)

FNAME_BLOCK_BITS = 16
FNAME_MAX_BLOCKS = 8192

# FUObjectItem (x64): UObject*(8) + Flags(4) + ClusterRoot(4) + Serial(4) + pad(4)
FUOBJ_ITEM_SIZE = 24
FUOBJ_ITEM_OBJ  = 0x00

# ── padrão conhecido das primeiras FNameEntries ─────────────────
# UE4 4.27: header = Len<<6 | ProbeHash<<1 | bIsWide
# "None" (4 chars): Len=4 → header high bits = 4<<6 = 0x0100, low 6 bits = probe<<1
# Não podemos prever o probe hash, então buscamos o nome ASCII depois de um header
# cujo Len (header>>6) == 4, seguido de "None"
_PROBE_STRINGS = [b'ByteProperty', b'IntProperty', b'FloatProperty',
                  b'BoolProperty', b'ObjectProperty']


def _read_u16(handle, addr):
    data = memory._read_bytes(handle, addr, 2)
    return struct.unpack_from("<H", data)[0] if data and len(data) == 2 else None


def _read_u32(handle, addr):
    data = memory._read_bytes(handle, addr, 4)
    return struct.unpack_from("<I", data)[0] if data and len(data) == 4 else None


def _read_i32(handle, addr):
    data = memory._read_bytes(handle, addr, 4)
    return struct.unpack_from("<i", data)[0] if data and len(data) == 4 else None


def _read_ptr(handle, addr):
    data = memory._read_bytes(handle, addr, 8)
    return struct.unpack_from("<Q", data)[0] if data and len(data) == 8 else None


# ── GNames ──────────────────────────────────────────────────────

def find_gnames(handle, progress_fn=None):
    """Encontra GNames (FNameEntryAllocator) no processo.

    Retorna dict com 'allocator', 'blocks' (lista de endereços dos blocos),
    ou None se não encontrar.
    """
    block0 = _find_names_block0(handle, progress_fn)
    if not block0:
        log.warning("GNames block0 não encontrado")
        return None

    allocator = _find_allocator(handle, block0)
    if not allocator:
        log.warning("FNameEntryAllocator não encontrado")
        return None

    blocks = _read_blocks(handle, allocator)
    if not blocks or blocks[0] != block0:
        log.warning("Blocks[0] não bate com block0 encontrado")
        return None

    log.info("GNames encontrado: allocator=0x%X, %d blocos",
             allocator, sum(1 for b in blocks if b))
    return {"allocator": allocator, "blocks": blocks}


def _find_names_block0(handle, progress_fn=None):
    """Varre memória procurando o início do FNamePool Block[0] (formato 4.27)."""
    regions = list(memory._readable_regions(handle))
    total = sum(s for _, s in regions)
    scanned = 0
    # Busca "None" em ASCII; o header u16 antes tem Len>>6 == 4
    probe = b'None'
    for base, size in regions:
        block_sz = min(size, 1 << 20)
        overlap = 20
        offset = 0
        while offset < size:
            chunk_sz = min(block_sz, size - offset)
            data = memory._read_bytes(handle, base + offset, chunk_sz)
            if data:
                start = 0
                while True:
                    pos = data.find(probe, start)
                    if pos < 0 or pos < 2:
                        break
                    # verifica header antes de "None"
                    hdr = struct.unpack_from("<H", data, pos - 2)[0]
                    if (hdr >> 6) == 4 and (hdr & 1) == 0:
                        addr = base + offset + pos - 2
                        if _verify_block0_427(handle, addr):
                            log.info("GNames block0 em 0x%X", addr)
                            return addr
                    start = pos + 1
            scanned += chunk_sz
            offset += max(1, chunk_sz - overlap)
            if progress_fn and total > 0:
                progress_fn(min(100, int(scanned * 100 / total)))
    return None


def _verify_block0_427(handle, addr):
    """Verifica block0 no formato 4.27 (header = Len<<6 | probe<<1 | wide)."""
    # entry 0 = "None" (4 chars), caminha pelas próximas entries e verifica nomes UE4
    # entry size = 2 (header) + len, padded to 2
    off = 0
    expected = ["None", "ByteProperty", "IntProperty"]
    for name in expected:
        h = _read_u16(handle, addr + off)
        if h is None:
            return False
        length = h >> 6
        if length != len(name):
            return False
        data = memory._read_bytes(handle, addr + off + 2, length)
        if data != name.encode('ascii'):
            return False
        entry_sz = 2 + length
        if entry_sz % 2:
            entry_sz += 1
        off += entry_sz
    return True


def _count_plaintext_probe(handle):
    """Conta quantas vezes 'ByteProperty' aparece em plaintext na memória."""
    probe = b'ByteProperty'
    count = 0
    for base, size in memory._readable_regions(handle):
        block_sz = min(size, 1 << 20)
        offset = 0
        while offset < size:
            chunk_sz = min(block_sz, size - offset)
            data = memory._read_bytes(handle, base + offset, chunk_sz)
            if data:
                count += data.count(probe)
                if count > 0:
                    return count  # basta saber se existe
            offset += chunk_sz
    return count


def _verify_block0_flexible(handle, addr):
    """Verifica block0 sem depender do formato exato do header."""
    # Lê 100 bytes a partir do candidato e procura nomes UE4 conhecidos
    data = memory._read_bytes(handle, addr, 200)
    if not data:
        return False
    found = 0
    for name in [b'None', b'ByteProperty', b'IntProperty', b'BoolProperty']:
        if name in data:
            found += 1
    return found >= 3


def _find_allocator(handle, block0_addr):
    """Acha FNameEntryAllocator procurando um ponteiro para Block[0]."""
    packed = struct.pack("<Q", block0_addr)
    regions = list(memory._readable_regions(handle))

    for base, size in regions:
        block_sz = min(size, 1 << 20)
        offset = 0
        while offset < size:
            chunk_sz = min(block_sz, size - offset)
            data = memory._read_bytes(handle, base + offset, chunk_sz)
            if data:
                start = 0
                while True:
                    pos = data.find(packed, start)
                    if pos < 0:
                        break
                    ptr_addr = base + offset + pos
                    # Blocks[0] fica em allocator + 0x10
                    candidate = ptr_addr - ALLOC_BLOCKS
                    if _verify_allocator(handle, candidate, block0_addr):
                        return candidate
                    start = pos + 1
            offset += max(1, chunk_sz - len(packed))
    return None


def _verify_allocator(handle, addr, block0_addr):
    """Verifica que o endereço é FNameEntryAllocator válido."""
    cur_block = _read_u32(handle, addr + ALLOC_CUR_BLK)
    cur_cursor = _read_u32(handle, addr + ALLOC_CUR_CUR)
    if cur_block is None or cur_cursor is None:
        return False
    if cur_block > 100 or cur_cursor > 0x100000:
        return False
    b0 = _read_ptr(handle, addr + ALLOC_BLOCKS)
    return b0 == block0_addr


# FNamePool estático em Mir4G.exe (build de 2025-11-06): RVA do singleton.
# Não há criptografia: layout 4.27 padrão (ver re_analysis.md).
MIR4_GNAMES_RVA = 0x81B5180


def module_base(handle):
    """Base do módulo principal (o .exe) do processo."""
    import ctypes
    from ctypes import wintypes
    psapi = ctypes.windll.psapi
    hMods = (ctypes.c_void_p * 1024)()
    cbNeeded = wintypes.DWORD()
    ok = psapi.EnumProcessModulesEx(
        ctypes.c_void_p(handle), hMods, ctypes.sizeof(hMods),
        ctypes.byref(cbNeeded), 0x03)
    if not ok:
        err = ctypes.get_last_error() if hasattr(ctypes, 'get_last_error') else 0
        log.warning("EnumProcessModulesEx falhou (err=%d)", err)
        return None
    if cbNeeded.value < 8:
        return None
    return hMods[0]


def find_gnames_static(handle, base=None, rva=MIR4_GNAMES_RVA):
    """GNames via RVA fixo do FNamePool (sem scan). Mesmo formato de find_gnames."""
    base = base or module_base(handle)
    pool = base + rva
    blocks = _read_blocks(handle, pool)
    if not blocks or not blocks[0]:
        return None
    g = {"allocator": pool, "blocks": blocks}
    return g if resolve_name(handle, g, 0) == "None" else None


def _read_blocks(handle, allocator_addr):
    """Lê os ponteiros dos blocos do FNameEntryAllocator."""
    cur_block = _read_u32(handle, allocator_addr + ALLOC_CUR_BLK)
    if cur_block is None or cur_block > FNAME_MAX_BLOCKS:
        return None
    blocks = []
    for i in range(cur_block + 1):
        ptr = _read_ptr(handle, allocator_addr + ALLOC_BLOCKS + i * 8)
        blocks.append(ptr if ptr else 0)
    return blocks


def resolve_name(handle, gnames, name_index):
    """Resolve FName ComparisonIndex → string."""
    if not gnames or name_index < 0:
        return None
    blocks = gnames["blocks"]
    block_idx = name_index >> FNAME_BLOCK_BITS
    entry_off = (name_index & ((1 << FNAME_BLOCK_BITS) - 1)) * 2
    if block_idx >= len(blocks) or not blocks[block_idx]:
        return None
    base = blocks[block_idx] + entry_off
    header = _read_u16(handle, base)
    if header is None:
        return None
    # 4.27: bIsWide:1 | LowercaseProbeHash:5 | Len:10  (Len = header >> 6)
    is_wide = header & 1
    length = header >> 6
    if length <= 0 or length > 1024:
        return None
    name_addr = base + 2
    if is_wide:
        data = memory._read_bytes(handle, name_addr, length * 2)
        return data.decode("utf-16-le", errors="replace") if data else None
    else:
        data = memory._read_bytes(handle, name_addr, length)
        return data.decode("ascii", errors="replace") if data else None


# ── GObjects ────────────────────────────────────────────────────

def find_gobjects(handle, gnames, progress_fn=None):
    """Encontra GUObjectArray por padrão de instrução ou busca heurística.

    Retorna dict com 'base', 'num_elements', 'chunks' (lista de ptrs),
    ou None.
    """
    # Abordagem: procura arrays de ponteiros onde os objetos apontados
    # têm FName válido que resolve via GNames
    # Heurística: GObjects.ObjObjects é um TChunkedArray de FUObjectItem
    # O primeiro chunk contém objetos cujos nomes são conhecidos:
    # index 0 = CoreUObject.Object (ou package)

    # Tenta padrão de instrução primeiro (mais rápido)
    result = _find_gobjects_by_sig(handle, gnames)
    if result:
        return result

    log.info("Sig scan falhou, tentando busca heurística pra GObjects...")
    return _find_gobjects_heuristic(handle, gnames, progress_fn)


def _find_gobjects_by_sig(handle, gnames):
    """Procura por padrões de instrução x86-64 que referenciam GObjects."""
    # Padrões comuns pro GUObjectArray em UE4 4.27:
    # 48 8B 05 ?? ?? ?? ??  = MOV RAX, [RIP+disp32]  (load GObjects pointer)
    # seguido de 48 8B 0C C8 = MOV RCX, [RAX+RCX*8]  (index into chunks)
    sigs = [
        # Pattern, offset to RIP-relative disp32, instruction length
        (b'\x48\x8B\x05', 3, 7),   # MOV RAX, [RIP+??]
        (b'\x4C\x8B\x0D', 3, 7),   # MOV R9, [RIP+??]
        (b'\x48\x8D\x0D', 3, 7),   # LEA RCX, [RIP+??]
    ]

    regions = list(memory._readable_regions(handle))
    for base, size in regions:
        # Só varre regiões executáveis (código)
        if size < 0x1000 or size > 0x10000000:
            continue
        block_sz = min(size, 1 << 20)
        offset = 0
        while offset < size:
            chunk_sz = min(block_sz, size - offset)
            data = memory._read_bytes(handle, base + offset, chunk_sz)
            if not data:
                offset += chunk_sz
                continue
            for sig, disp_off, instr_len in sigs:
                start = 0
                while True:
                    pos = data.find(sig, start)
                    if pos < 0 or pos + instr_len > len(data):
                        break
                    rip = base + offset + pos + instr_len
                    disp = struct.unpack_from("<i", data, pos + disp_off)[0]
                    target = rip + disp
                    result = _try_gobjects_at(handle, gnames, target)
                    if result:
                        return result
                    start = pos + 1
            offset += chunk_sz
    return None


def _try_gobjects_at(handle, gnames, addr):
    """Testa se addr aponta para GUObjectArray válido."""
    # TUObjectArray layout: Objects**(8) + PreAlloc(8) + Max(4) + Num(4) + MaxChunks(4) + NumChunks(4)
    # Tenta duas interpretações: addr é o array, ou addr é ponteiro pro array
    for candidate in [addr, _read_ptr(handle, addr)]:
        if not candidate or candidate < 0x10000:
            continue
        # Lê NumElements e NumChunks
        num_elements = _read_i32(handle, candidate + 16)
        num_chunks = _read_i32(handle, candidate + 24)
        if (num_elements is None or num_chunks is None
                or num_elements < 1000 or num_elements > 5000000
                or num_chunks < 1 or num_chunks > 500):
            continue
        objects_ptr = _read_ptr(handle, candidate)
        if not objects_ptr or objects_ptr < 0x10000:
            continue
        # Lê primeiro chunk
        chunk0 = _read_ptr(handle, objects_ptr)
        if not chunk0 or chunk0 < 0x10000:
            continue
        # Valida: objeto[0] ou [1] deve ter FName resolvível
        for idx in range(min(5, num_elements)):
            obj_ptr = _read_ptr(handle, chunk0 + idx * FUOBJ_ITEM_SIZE)
            if not obj_ptr or obj_ptr < 0x10000:
                continue
            fname_idx = _read_u32(handle, obj_ptr + UOBJ_FNAME)
            if fname_idx is None:
                continue
            name = resolve_name(handle, gnames, fname_idx)
            if name and len(name) > 2:
                log.info("GObjects em 0x%X (%d objetos, %d chunks)",
                         candidate, num_elements, num_chunks)
                return {
                    "base": candidate,
                    "num_elements": num_elements,
                    "num_chunks": num_chunks,
                    "objects_ptr": objects_ptr,
                }
    return None


def _find_gobjects_heuristic(handle, gnames, progress_fn=None):
    """Busca GObjects por força bruta (lento, último recurso)."""
    # shortcut: too slow for full scan, skip for now
    return None


# ── Enumeration ─────────────────────────────────────────────────

def iter_objects(handle, gobjects, limit=None):
    """Itera UObjects: gera (index, obj_ptr)."""
    num = gobjects["num_elements"]
    objects_ptr = gobjects["objects_ptr"]
    num_chunks = gobjects["num_chunks"]
    elements_per_chunk = 65536

    if limit:
        num = min(num, limit)

    for chunk_idx in range(num_chunks):
        chunk_ptr = _read_ptr(handle, objects_ptr + chunk_idx * 8)
        if not chunk_ptr:
            continue
        start_idx = chunk_idx * elements_per_chunk
        count = min(elements_per_chunk, num - start_idx)
        if count <= 0:
            break
        for i in range(count):
            obj_ptr = _read_ptr(handle, chunk_ptr + i * FUOBJ_ITEM_SIZE)
            if obj_ptr and obj_ptr > 0x10000:
                yield start_idx + i, obj_ptr


def get_object_name(handle, gnames, obj_ptr):
    """Lê o nome de um UObject."""
    fname_idx = _read_u32(handle, obj_ptr + UOBJ_FNAME)
    if fname_idx is None:
        return None
    return resolve_name(handle, gnames, fname_idx)


def get_class_name(handle, gnames, obj_ptr):
    """Lê o nome da classe de um UObject."""
    class_ptr = _read_ptr(handle, obj_ptr + UOBJ_CLASS)
    if not class_ptr:
        return None
    return get_object_name(handle, gnames, class_ptr)


def find_objects_by_class(handle, gnames, gobjects, class_name, limit=100):
    """Encontra UObjects cuja classe tem o nome dado."""
    results = []
    for idx, obj_ptr in iter_objects(handle, gobjects):
        cname = get_class_name(handle, gnames, obj_ptr)
        if cname == class_name:
            oname = get_object_name(handle, gnames, obj_ptr)
            results.append({"index": idx, "address": obj_ptr, "name": oname})
            if len(results) >= limit:
                break
    return results


def get_class_properties(handle, gnames, class_ptr):
    """Enumera FProperty/UProperty de uma UClass → lista de (nome, offset, tamanho)."""
    props = []

    # UE4 4.25+ usa FField (ChildProperties em UStruct)
    # Tenta ChildProperties primeiro (offset 0x48 ou 0x50 dependendo da versão)
    for children_off in [0x48, 0x50, 0x38]:
        child = _read_ptr(handle, class_ptr + children_off)
        if child and child > 0x10000:
            visited = set()
            while child and child > 0x10000 and child not in visited:
                visited.add(child)
                fname_idx = _read_u32(handle, child + FFIELD_FNAME)
                if fname_idx is not None:
                    name = resolve_name(handle, gnames, fname_idx)
                    offset = _read_i32(handle, child + FPROP_OFFSET)
                    size = _read_i32(handle, child + FPROP_SIZE)
                    if name and offset is not None and 0 <= offset < 0x10000:
                        props.append({"name": name, "offset": offset,
                                      "size": size or 0})
                child = _read_ptr(handle, child + FFIELD_NEXT)
            if props:
                return props

    # Fallback: UField chain (UE4 < 4.25)
    child = _read_ptr(handle, class_ptr + USTRUCT_CHILDREN)
    visited = set()
    while child and child > 0x10000 and child not in visited:
        visited.add(child)
        fname_idx = _read_u32(handle, child + UOBJ_FNAME)
        if fname_idx is not None:
            name = resolve_name(handle, gnames, fname_idx)
            # UProperty.Offset_Internal está em offsets diferentes
            for prop_off in [0x44, 0x4C, 0x50]:
                offset = _read_i32(handle, child + prop_off)
                if offset is not None and 0 < offset < 0x10000:
                    props.append({"name": name, "offset": offset, "size": 4})
                    break
        child = _read_ptr(handle, child + UFIELD_NEXT)

    return props


def dump_player_data(handle, gnames, gobjects):
    """Encontra classes do jogador e lista suas propriedades com offsets.

    Retorna dict: {class_name: {prop_name: offset, ...}, ...}
    """
    # Classes que provavelmente contêm dados do jogador (do SDK dump)
    target_classes = [
        "MM_CharacterPawnBase",
        "MM_InGameState",
        "MM_CharacterMovementComponent",
        "MM_CombatPoint",
        "MM_ContentsState",
        "MM_Map_Player",
    ]

    result = {}
    for idx, obj_ptr in iter_objects(handle, gobjects):
        oname = get_object_name(handle, gnames, obj_ptr)
        if oname in target_classes:
            class_ptr = obj_ptr  # é a própria classe
            props = get_class_properties(handle, gnames, class_ptr)
            if props:
                result[oname] = {p["name"]: p["offset"] for p in props}
                log.info("Classe %s: %d propriedades", oname, len(props))

    # Se não achou como classe, procura por instâncias
    if not result:
        for cname in target_classes:
            instances = find_objects_by_class(
                handle, gnames, gobjects, cname, limit=1)
            if instances:
                inst = instances[0]
                class_ptr = _read_ptr(handle, inst["address"] + UOBJ_CLASS)
                if class_ptr:
                    props = get_class_properties(handle, gnames, class_ptr)
                    if props:
                        result[cname] = {p["name"]: p["offset"] for p in props}
    return result


def full_scan(handle, progress_fn=None):
    """Scan completo: encontra GNames, GObjects, e dados do jogador.

    progress_fn(phase, pct): phase é "gnames", "gobjects", ou "props".
    Retorna dict com 'gnames', 'gobjects', 'player_data', ou None.
    """
    def gn_progress(pct):
        if progress_fn:
            progress_fn("gnames", pct)

    # tenta RVA estático primeiro (instantâneo)
    gnames = find_gnames_static(handle)
    if gnames:
        log.info("GNames via RVA estático OK")
    else:
        gnames = find_gnames(handle, gn_progress)
    if not gnames:
        count = _count_plaintext_probe(handle)
        log.warning("GNames falhou. 'ByteProperty' plaintext encontrado %d vezes", count)
        return {"error": "gnames_not_found", "probe_count": count}

    if progress_fn:
        progress_fn("gobjects", 0)

    gobjects = find_gobjects(handle, gnames)
    if not gobjects:
        return None

    if progress_fn:
        progress_fn("props", 0)

    player_data = dump_player_data(handle, gnames, gobjects)

    if progress_fn:
        progress_fn("done", 100)

    return {
        "gnames": gnames,
        "gobjects": gobjects,
        "player_data": player_data,
    }
