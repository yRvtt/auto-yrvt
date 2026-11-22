"""
Sniffer de pacotes MIR4 — lê XP, Power e HP/MP em tempo real.

Intercepta pacotes S→C (server port 33000) via pktmon (Windows 10+).
Ciclos de captura curtos: ETL → pcapng → decodifica protobuf.
Requer admin.
"""

import logging
import math
import os
import struct
import subprocess
import tempfile
import threading
import time

log = logging.getLogger(__name__)

SERVER_PORT = 33000

OP_XP_GAIN = 0x5F02
OP_POWER   = 0x6102
OP_HP_MP   = 0x6402

_CAPTURE_INTERVAL = 5

# XP needed to level up, derived from MIR4 Wiki data points.
# Verified: lv100=1_818_638_670, lv120=6_264_805_340.
_XP_A = 3_737_117
_XP_B = 0.06185


def xp_for_level(level):
    return round(_XP_A * math.exp(_XP_B * level))


def _decode_varint(data, pos):
    result = 0; shift = 0
    while pos < len(data):
        b = data[pos]; result |= (b & 0x7F) << shift; pos += 1
        if not (b & 0x80): break
        shift += 7
    return result, pos


def _decode_pb_fields(data):
    fields = {}; pos = 0
    while pos < len(data):
        try: tag_val, pos = _decode_varint(data, pos)
        except: break
        fn = tag_val >> 3; wt = tag_val & 7
        if fn == 0 or fn > 500: break
        if wt == 0:
            val, pos = _decode_varint(data, pos)
            fields[fn] = val
        elif wt == 2:
            l, pos = _decode_varint(data, pos)
            if l < 0 or l > len(data) - pos: break
            fields[fn] = data[pos:pos+l]
            pos += l
        elif wt == 5:
            if pos + 4 > len(data): break
            fields[fn] = struct.unpack_from("<f", data, pos)[0]
            pos += 4
        elif wt == 1:
            if pos + 8 > len(data): break
            fields[fn] = struct.unpack_from("<Q", data, pos)[0]
            pos += 8
        else:
            break
    return fields


def _find_pb_start(sub):
    idx = sub.find(b'\x01\x08', 6)
    if idx >= 0:
        return sub[idx + 1:]
    idx = sub.find(b'\x08', 6)
    if idx >= 0:
        return sub[idx:]
    return None


def _parse_subpackets(tcp_data, server_port_src):
    if not server_port_src:
        return
    results = []
    pos = 0
    while pos + 2 < len(tcp_data):
        pkt_len = struct.unpack_from("<H", tcp_data, pos)[0]
        if pkt_len <= 0 or pkt_len > len(tcp_data) - pos:
            break
        sub = tcp_data[pos:pos + pkt_len]
        if len(sub) >= 6:
            opcode = struct.unpack_from("<H", sub, 4)[0]
            if opcode in (OP_XP_GAIN, OP_POWER, OP_HP_MP):
                pb = _find_pb_start(sub)
                if pb:
                    fields = _decode_pb_fields(pb)
                    results.append((opcode, fields))
        pos += pkt_len
    return results


class GameData:
    __slots__ = (
        'xp_base', 'xp_bonus', 'xp_total', 'xp_last_time',
        'power', 'hp', 'hp_max', 'mp_max',
        'xp_history', '_lock',
    )

    def __init__(self):
        self.xp_base = 0
        self.xp_bonus = 0
        self.xp_total = 0
        self.xp_last_time = 0.0
        self.power = 0
        self.hp = 0
        self.hp_max = 0
        self.mp_max = 0
        self.xp_history = []
        self._lock = threading.Lock()

    def update(self, opcode, fields):
        with self._lock:
            if opcode == OP_XP_GAIN:
                self.xp_base = fields.get(1, 0)
                self.xp_bonus = fields.get(2, 0)
                self.xp_total = self.xp_base + self.xp_bonus
                self.xp_last_time = time.time()
                self.xp_history.append(
                    (self.xp_last_time, self.xp_base, self.xp_bonus))
                if len(self.xp_history) > 500:
                    self.xp_history = self.xp_history[-500:]
            elif opcode == OP_POWER:
                self.power = fields.get(1, 0)
            elif opcode == OP_HP_MP:
                self.hp_max = fields.get(2, 0)
                self.hp = fields.get(3, 0)
                self.mp_max = fields.get(9, 0)

    def accumulated_base_xp(self):
        with self._lock:
            return sum(b for _, b, _ in self.xp_history)

    def snapshot(self):
        with self._lock:
            return {
                'xp_base': self.xp_base,
                'xp_bonus': self.xp_bonus,
                'xp_total': self.xp_total,
                'xp_last_time': self.xp_last_time,
                'power': self.power,
                'hp': self.hp,
                'hp_max': self.hp_max,
                'mp_max': self.mp_max,
                'xp_count': len(self.xp_history),
                'xp_accumulated': sum(b for _, b, _ in self.xp_history),
            }

    def xp_rate_per_hour(self):
        with self._lock:
            if len(self.xp_history) < 2:
                return 0.0
            dt = self.xp_history[-1][0] - self.xp_history[0][0]
            if dt < 5:
                return 0.0
            total_xp = sum(b + bn for _, b, bn in self.xp_history)
            return total_xp / dt * 3600


def _run_quiet(args):
    return subprocess.run(
        args, capture_output=True, text=True,
        creationflags=subprocess.CREATE_NO_WINDOW,
    )


def _process_pcap(pcap_path, data, callback):
    try:
        import dpkt
    except ImportError:
        log.warning("dpkt não instalado — pip install dpkt")
        return 0
    packets = 0
    try:
        with open(pcap_path, "rb") as f:
            reader = dpkt.pcapng.Reader(f)
            for _ts, buf in reader:
                try:
                    eth = dpkt.ethernet.Ethernet(buf)
                    tcp = eth.data.data
                    if tcp.sport == SERVER_PORT and len(tcp.data) > 0:
                        packets += 1
                        events = _parse_subpackets(tcp.data, True)
                        if events:
                            for opcode, fields in events:
                                data.update(opcode, fields)
                                if callback:
                                    callback(opcode, fields)
                except Exception:
                    continue
    except Exception as e:
        log.debug("Erro ao ler pcap: %s", e)
    return packets


class Sniffer:
    """Captura pacotes MIR4 via pktmon em ciclos curtos."""

    def __init__(self, callback=None, interval=_CAPTURE_INTERVAL):
        self._running = False
        self._thread = None
        self._callback = callback
        self._interval = interval
        self.data = GameData()
        self._packets = 0
        self._errors = 0
        self._etl = os.path.join(tempfile.gettempdir(), "mir4_sniff.etl")
        self._pcap = os.path.join(tempfile.gettempdir(), "mir4_sniff.pcapng")

    @property
    def running(self):
        return self._running

    @property
    def stats(self):
        return {'packets': self._packets, 'errors': self._errors}

    def start(self):
        if self._running:
            return
        self._running = True
        self._thread = threading.Thread(target=self._run, daemon=True)
        self._thread.start()
        log.info("Sniffer pktmon iniciado")

    def stop(self):
        self._running = False
        _run_quiet(["pktmon", "stop"])
        _run_quiet(["pktmon", "filter", "remove"])
        for p in (self._etl, self._pcap):
            try:
                os.remove(p)
            except OSError:
                pass
        if self._thread:
            self._thread.join(timeout=self._interval + 5)
            self._thread = None
        log.info("Sniffer parado (pkts=%d)", self._packets)

    def _run(self):
        _run_quiet(["pktmon", "stop"])
        _run_quiet(["pktmon", "filter", "remove"])
        r = _run_quiet(["pktmon", "filter", "add", "-p", str(SERVER_PORT)])
        if r.returncode != 0:
            log.error("pktmon filter falhou: %s", r.stderr)
            self._running = False
            return

        while self._running:
            for p in (self._etl, self._pcap):
                try:
                    os.remove(p)
                except OSError:
                    pass

            r = _run_quiet([
                "pktmon", "start", "--capture",
                "--pkt-size", "0",
                "--file-name", self._etl,
            ])
            if r.returncode != 0:
                log.error("pktmon start falhou: %s", r.stderr)
                self._errors += 1
                self._running = False
                break

            deadline = time.time() + self._interval
            while self._running and time.time() < deadline:
                time.sleep(0.5)

            _run_quiet(["pktmon", "stop"])

            if not os.path.exists(self._etl):
                continue

            r = _run_quiet([
                "pktmon", "etl2pcap", self._etl, "-o", self._pcap,
            ])
            if not os.path.exists(self._pcap):
                self._errors += 1
                continue

            n = _process_pcap(self._pcap, self.data, self._callback)
            self._packets += n

        _run_quiet(["pktmon", "filter", "remove"])
        for p in (self._etl, self._pcap):
            try:
                os.remove(p)
            except OSError:
                pass
        self._running = False
