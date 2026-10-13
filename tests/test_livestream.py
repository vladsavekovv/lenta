"""The live path end to end, against a stand-in tuner.

A real multiplex carrying two programmes is fed through a FIFO standing in for the adapter's dvr
device. Everything above that — the tuner allocation, the programme choice, FFmpeg, the sliding HLS
window, viewers joining and leaving — is the code that runs against the real stick.

Needs ffmpeg. Build the multiplex first (see MUX below); the test says so if it is missing.
"""
import os
import struct
import subprocess
import sys
import tempfile
import threading
import time
import zlib
from pathlib import Path

os.environ.setdefault("LENTA_DATA_DIR", tempfile.mkdtemp())
sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "server"))

from lenta import db, dvb, livestream, livetv          # noqa: E402


def _section(table_id: int, ident: int, body: bytes) -> bytes:
    """Wrap a table body in the section header and CRC the demux would have checked.

    table_id, then a 12-bit length counting everything after it, then the table's identifier (the
    transport stream id for a PAT, the service id for a PMT), version and section numbers, the body,
    and the CRC.
    """
    length = 5 + len(body) + 4
    sec = (bytes([table_id]) + struct.pack(">H", 0xB000 | length) + struct.pack(">H", ident)
           + bytes([0xC1, 0x00, 0x00]) + body)
    return sec + struct.pack(">I", zlib.crc32(sec) & 0xFFFFFFFF)


def build_pat(programmes: dict[int, int]) -> bytes:
    body = b"".join(struct.pack(">HH", num, 0xE000 | pid) for num, pid in programmes.items())
    return _section(0x00, 1, body)


def build_pmt(service_id: int, pcr_pid: int, streams: list[tuple[int, int]]) -> bytes:
    body = struct.pack(">HH", 0xE000 | pcr_pid, 0xF000)
    for stype, pid in streams:
        body += bytes([stype]) + struct.pack(">HH", 0xE000 | pid, 0xF000)
    return _section(0x02, service_id, body)


MUX = Path(os.environ.get("LENTA_TEST_MUX", "/tmp/lenta-test-mux.ts"))
HD, SD = 1001, 1002                     # the two services in that multiplex


def build_mux(path: Path) -> None:
    """Two programmes in one multiplex: H.264/AAC (copyable) and MPEG-2/MP2 (must be re-encoded)."""
    subprocess.run([
        "ffmpeg", "-hide_banner", "-loglevel", "error", "-y",
        "-f", "lavfi", "-i", "testsrc2=size=640x360:rate=25:duration=20",
        "-f", "lavfi", "-i", "sine=frequency=440:duration=20",
        "-f", "lavfi", "-i", "smptebars=size=704x576:rate=25:duration=20",
        "-f", "lavfi", "-i", "sine=frequency=880:duration=20",
        "-map", "0:v", "-map", "1:a", "-map", "2:v", "-map", "3:a",
        "-c:v:0", "libx264", "-preset", "ultrafast", "-g", "25", "-c:a:0", "aac", "-b:a:0", "64k",
        "-c:v:1", "mpeg2video", "-g", "25", "-b:v:1", "1500k", "-c:a:1", "mp2", "-b:a:1", "64k",
        "-program", f"title=HD Test:program_num={HD}:st=0:st=1",
        "-program", f"title=SD Test:program_num={SD}:st=2:st=3",
        "-muxrate", "8M", "-f", "mpegts", str(path)], check=True)


class FakeAdapter:
    """A FIFO in place of /dev/dvb/adapterN/dvr0, fed the multiplex on a loop at roughly real speed."""

    def __init__(self, root: Path, adapter: int):
        self.dir = root / f"adapter{adapter}"
        self.dir.mkdir(parents=True, exist_ok=True)
        self.path = self.dir / "dvr0"
        os.mkfifo(self.path)
        self.stop = threading.Event()
        self.thread = threading.Thread(target=self._pump, daemon=True)
        self.thread.start()

    def _pump(self) -> None:
        data = MUX.read_bytes()
        chunk = 188 * 350                                 # ~1 Mbit at a time
        while not self.stop.is_set():
            try:
                fd = os.open(self.path, os.O_WRONLY | os.O_NONBLOCK)
            except OSError:
                time.sleep(0.05)
                continue
            try:
                os.set_blocking(fd, True)
                for i in range(0, len(data), chunk):
                    if self.stop.is_set():
                        break
                    os.write(fd, data[i:i + chunk])
                    time.sleep(0.02)
            except OSError:
                pass                                      # the reader went away; wait for the next one
            finally:
                os.close(fd)

    def close(self) -> None:
        self.stop.set()


def fake_tuners(root: Path, count: int) -> list[dvb.Frontend]:
    out = []
    for i in range(count):
        fe = dvb.Frontend(adapter=i, index=0, name="Stand-in Si2168", delsys=["DVB-C"],
                          product="dualHD", usb_id="2040:8265", serial="TEST0001")
        out.append(fe)
    return out


def main() -> int:
    if not MUX.exists():
        print(f"building the test multiplex at {MUX} …")
        build_mux(MUX)

    root = Path(tempfile.mkdtemp())
    adapters = [FakeAdapter(root, 0), FakeAdapter(root, 1)]
    fes = fake_tuners(root, 2)
    for fe, a in zip(fes, adapters):
        fe.__dict__["_dvr"] = str(a.path)

    # The frontend's device paths point at the FIFOs; tuning and tapping are the parts a real
    # kernel does, so they are stood in for. Everything else below is the real code.
    dvb.Frontend.dvr = property(lambda self: self.__dict__["_dvr"])
    dvb.adapters = lambda: list(fes)

    # Real PAT and PMT sections, so the copy-or-re-encode decision is made the way it is on air:
    # service 1001 is H.264/AAC and should be copied, 1002 is MPEG-2/MP2 and must be re-encoded.
    pat = build_pat({HD: 0x100, SD: 0x200})
    pmts = {0x100: build_pmt(HD, 0x101, [(0x1B, 0x101), (0x0F, 0x102)]),
            0x200: build_pmt(SD, 0x201, [(0x02, 0x201), (0x03, 0x202)])}

    taps = []

    class FakeTuner:
        def __init__(self, fe):
            self.fe, self.closed = fe, False
        def tune(self, delsys, freq, wait=5.0, **kw):
            self.freq = freq
            return True
        def tap_mux(self, buffer_bytes=0):
            taps.append("whole multiplex")
            return os.open(os.devnull, os.O_RDONLY)          # tap_mux still returns one fd
        def tap_service(self, pids, buffer_bytes=0):
            taps.append(tuple(pids))
            return [os.open(os.devnull, os.O_RDONLY) for _ in pids]
        def section(self, pid, table_id, timeout=5.0, want=1):
            if pid == 0x00:
                return [pat]
            return [pmts[pid]] if pid in pmts else []
        def close(self):
            self.closed = True
    livestream.dvb.Tuner = FakeTuner

    db.init_db()
    sid = livetv.add_source("tuner", "dualHD", {"device": "usb:2040:8265:TEST0001", "inputs": []})
    livetv._save_channels(sid, [
        {"uid": "1:1:1001", "name": "HD Test", "number": 1, "kind": "tv", "provider": "Stand-in",
         "tune": {"delsys": "DVB-C", "frequency": 530_000_000, "service_id": HD}},
        {"uid": "1:1:1002", "name": "SD Test", "number": 2, "kind": "tv", "provider": "Stand-in",
         "tune": {"delsys": "DVB-C", "frequency": 538_000_000, "service_id": SD}},
    ])
    rows = {c["name"]: c for c in livetv.channels()}
    hd, sd = livetv.channel(rows["HD Test"]["id"]), livetv.channel(rows["SD Test"]["id"])

    failures = []

    def check(label, ok, detail=""):
        print(f"  {'ok  ' if ok else 'FAIL'}  {label}{'  — ' + detail if detail and not ok else ''}")
        if not ok:
            failures.append(label)

    try:
        print("\n1. watching a channel")
        feed_hd, token_a = livestream.watch(hd)
        started = livestream.wait_for_start(feed_hd, timeout=40)
        check("the stream starts and writes a playlist", started, feed_hd.error)
        if started:
            playlist = (feed_hd.directory / "index.m3u8").read_text()
            segs = sorted(feed_hd.directory.glob("seg*.ts"))
            check("the playlist names real segments", ".ts" in playlist and bool(segs))
            check("segments hold data", bool(segs) and segs[0].stat().st_size > 10_000,
                  f"{segs[0].stat().st_size if segs else 0} bytes")
            check("it is a live playlist, not a finished one", "#EXT-X-ENDLIST" not in playlist)
            probe = subprocess.run(["ffprobe", "-hide_banner", "-v", "error", "-show_entries",
                                    "stream=codec_name,codec_type", "-of", "compact", str(segs[0])],
                                   capture_output=True, text=True)
            print("        first segment:", probe.stdout.strip().replace("\n", " | ") or probe.stderr.strip())
            check("the segment carries video", "codec_type=video" in probe.stdout)
            check("H.264 and AAC are copied, not re-encoded", feed_hd.mode == "copy",
                  f"mode={feed_hd.mode} video={feed_hd.video} audio={feed_hd.audio}")
            print(f"        read from the PMT: {feed_hd.video}/{feed_hd.audio} -> {feed_hd.mode}")

        check("only this programme's pids are taken, not the whole multiplex",
              taps and taps[0] != "whole multiplex", str(taps[:1]))
        if taps and taps[0] != "whole multiplex":
            print(f"        asked the demux for pids {', '.join(str(p) for p in taps[0])}"
                  " — the PAT, the PMT, the video and the audio")

        print("\n2. a second viewer of the same channel shares the tuner")
        feed_again, token_b = livestream.watch(hd)
        check("the same feed is joined", feed_again.id == feed_hd.id)
        check("both viewers are counted", len(feed_again.viewers) == 2, str(len(feed_again.viewers)))
        check("only one input is in use", len(livestream.active()) == 1)

        print("\n3. a different channel takes the other input")
        feed_sd, token_c = livestream.watch(sd)
        check("a second feed starts", feed_sd.id != feed_hd.id)
        check("it took the other input", (feed_sd.adapter, feed_sd.index) != (feed_hd.adapter, feed_hd.index),
              f"{feed_sd.adapter}.{feed_sd.index}")
        ok_sd = livestream.wait_for_start(feed_sd, timeout=40)
        check("the MPEG-2 channel starts too", ok_sd, feed_sd.error)
        check("MPEG-2 and MPEG audio are re-encoded", feed_sd.mode == "transcode",
              f"mode={feed_sd.mode} video={feed_sd.video} audio={feed_sd.audio}")
        print(f"        read from the PMT: {feed_sd.video}/{feed_sd.audio} -> {feed_sd.mode}")
        if ok_sd:
            seg = sorted(feed_sd.directory.glob("seg*.ts"))[0]
            out = subprocess.run(["ffprobe", "-hide_banner", "-v", "error", "-show_entries",
                                  "stream=codec_name", "-of", "csv=p=0", str(seg)],
                                 capture_output=True, text=True).stdout
            check("it reaches the player as H.264 the browser can play", "h264" in out, out.strip())

        print("\n4. a third channel is refused, in words")
        try:
            livestream.watch(sd if False else hd | {"id": 999, "name": "Third"})
            check("refused", False, "it started anyway")
        except livestream.LiveError as ex:
            check("refused with a message naming what is on", "in use" in str(ex).lower(), str(ex))
            print("        said:", ex)
        except TypeError:
            third = dict(hd, id=999, name="Third")
            try:
                livestream.watch(third)
                check("refused", False, "it started anyway")
            except livestream.LiveError as ex:
                check("refused with a message naming what is on", "in use" in str(ex).lower(), str(ex))
                print("        said:", ex)

        print("\n5. a scan will not steal a tuner someone is watching")
        res = livetv.scan_tuner(sid)
        check("the scan refuses", "error" in res, str(res))
        check("and says who is watching", "watching" in res.get("error", "").lower(), res.get("error", ""))
        print("        said:", res.get("error"))

        print("\n6. leaving")
        livestream.leave(feed_hd, token_a)
        check("one viewer left, the feed stays", feed_hd.alive() and len(feed_hd.viewers) == 1)
        livestream.leave(feed_hd, token_b)
        check("no viewers left, the feed lingers for flipping", feed_hd.alive() and not feed_hd.viewers)
        livestream.stop(feed_hd.id)
        livestream.stop(feed_sd.id)
        check("stopping frees every input", not livestream.active())
        check("the segments are cleaned up", not feed_hd.directory.exists())
    finally:
        livestream.stop_all()
        for a in adapters:
            a.close()

    print()
    if failures:
        print(f"{len(failures)} check(s) failed: {', '.join(failures)}")
        return 1
    print("the live path works end to end")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
