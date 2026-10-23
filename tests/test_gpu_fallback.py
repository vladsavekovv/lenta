"""What happens when the graphics card cannot do what it was asked.

Reported as "some movies don't want to play", with a photograph of the player sitting at 0x0 with
nothing buffered. The film was AV1. His card is a **GTX 1070**, and FFmpeg says it plainly:

    [av1] Hardware is lacking required capabilities
    [av1] Your platform doesn't support hardware accelerated AV1 decoding.

AV1 decoding arrived with the RTX 30 series. `NVDEC_FORMATS` in transcode.py lists av1 anyway,
because it is a reading of NVIDIA's published table — **what NVDEC supports in general, not what the
card in this machine supports**. So `gpu_pipeline_possible()` said yes, the session ran with
`-hwaccel cuda -hwaccel_output_format cuda`, and FFmpeg could not produce a frame.

Three things were wrong, and the second is the one worth remembering:

  **It assumed.** A static table decided what the hardware could do. Now the first failure is
  remembered (`nvdec_refused`) and never repeated, and Detect hardware forgets it again — which is
  the button you press when you change the card.

  **The recovery existed and could not be reached.** `_read_progress` retries on the safe command
  when FFmpeg *exits*, and on a file the card cannot decode FFmpeg does not exit quickly: it retries
  the decode, throttled by `-readrate`, for fifteen seconds or more. The player gives up first and
  stops the session, `stop_session` sets `stopped`, and the retry is skipped. **It had never once
  run on his server.** `_watch_start` now acts after `STARTUP_GRACE` whatever the client does.

  **The menu promised what the server would not do.** A 720x544 film was offered "1080p · 10 Mbps";
  the server correctly saw the file already fits that budget and repackaged it untouched, while the
  panel reported "quality 1080p" over a 544-line picture. It read as a broken encoder. The menu now
  offers only the qualities that would actually re-encode something.

Section 2 runs the real threads against a command that produces nothing, which is the only way to
show that the switch happens without a client involved.
"""
import json
import os
import re
import subprocess
import sys
import tempfile
import threading
import time
from pathlib import Path

ROOT = Path("/home/claude/lenta")
os.environ.setdefault("LENTA_DATA_DIR", tempfile.mkdtemp())
sys.path.insert(0, str(ROOT / "server"))

fails = []


def check(name, ok):
    print(f"  {'ok  ' if ok else 'FAIL'}  {name}")
    if not ok:
        fails.append(name)


def src(rel):
    return (ROOT / rel).read_text()


def missing(mod, *names):
    """Report absent machinery as red checks rather than a traceback.

    Run against an older tree this file has nothing to import, and a test that dies on an
    AttributeError says "1 error" where it should say "nine things are not there yet" — which is how
    `test_genre_tiles.py` once hid twenty failures behind one stack trace.
    """
    gone = [n for n in names if not hasattr(mod, n)]
    for n in gone:
        check(f"{mod.__name__.split('.')[-1]}.{n} exists", False)
    return bool(gone)


from lenta import db as lentadb, transcode as T          # noqa: E402
lentadb.init_db()

# ---------------------------------------------------------------------------------------------------
print("1. the card is asked once, and the answer is remembered")

T.hw.update(accel="nvenc", encoder="h264_nvenc", scale_cuda=True, tonemap_cuda=False)
AV1 = {"video_codec": "av1", "pix_fmt": "yuv420p10le"}      # exactly his file
H264 = {"video_codec": "h264", "pix_fmt": "yuv420p"}

lentadb.set_setting("nvdec_refused", "")
check("the table still lists av1, because some cards really can decode it",
      "yuv420p10le" in T.NVDEC_FORMATS.get("av1", set()))
check("so the first attempt does take the all-GPU path", T.gpu_pipeline_possible(AV1, None)[0] is True)

if not missing(T, "remember_nvdec_failure", "nvdec_refused", "_nvdec_key"):
    T.remember_nvdec_failure("av1", "yuv420p10le")
    gpu, why = T.gpu_pipeline_possible(AV1, None)
    check(f"after one failure it never takes it again ({why})", gpu is False)
    check("and the reason says so rather than blaming the file", "already failed" in why)
    check("it is remembered across a restart, not held in memory",
          json.loads(lentadb.get_setting("nvdec_refused") or "[]") == ["av1/yuv420p10le"])
    check("a codec that works is unaffected", T.gpu_pipeline_possible(H264, None)[0] is True)
    T.remember_nvdec_failure("av1", "yuv420p10le")
    check("remembering it twice does not write it twice",
          json.loads(lentadb.get_setting("nvdec_refused") or "[]") == ["av1/yuv420p10le"])
check("**Detect hardware forgets it**, which is what you press after changing the card",
      'set_setting("nvdec_refused", "")' in src("server/lenta/transcode.py"))
lentadb.set_setting("nvdec_refused", "")

# ---------------------------------------------------------------------------------------------------
print("\n2. the watchdog, run for real against a command that produces nothing")

# The point of this section: no client, no HTTP, no player. A session is started with a command that
# will never write a segment, and the switch has to happen anyway. That is the whole bug — the old
# retry needed FFmpeg to exit *and* the client to still be waiting, and neither was true.
WATCHDOG = ["a session that produces nothing is switched to the safe command by itself",
            "and it says which pipeline it ended up on — CPU decode, since the card refused",
            "the failure is remembered as part of switching",
            "the fallback is spent, so it cannot loop",
            "the watchdog waits well under the 40s the client waits in `wait_for`"]

if missing(T, "_watch_start", "STARTUP_GRACE"):
    for n in WATCHDOG:
        check(n, False)
else:
    work = Path(tempfile.mkdtemp())
    session = None
    grace_was = T.STARTUP_GRACE
    T.STARTUP_GRACE = 2                                  # keep the test short; the mechanism is the same
    try:
        session = T.Session(
            id="watchdog-test", user_id=1, username="t", item_id=1, file_id=1,
            title="A film that will not decode", mode="transcode", quality="original", start=0,
            duration=100, directory=work, cmd=["sleep", "30"], pipeline="gpu")
        session.codec, session.pix_fmt = "av1", "yuv420p10le"
        # The safe command writes a segment, the way the CPU-filter path would.
        session.fallback_cmd = ["sh", "-c", f"printf x > {work}/seg00000.ts; sleep 20"]
        T._launch(session, session.cmd)
        threading.Thread(target=T._read_progress, args=(session,), daemon=True).start()
        threading.Thread(target=T._watch_start, args=(session,), daemon=True).start()

        began, deadline = time.time(), time.time() + 15
        while time.time() < deadline and session.produced_segments() == 0:
            time.sleep(0.2)
        check(f"{WATCHDOG[0]} ({round(time.time() - began, 1)}s)", session.produced_segments() > 0)
        check(f"{WATCHDOG[1]} ({session.pipeline})", session.pipeline == "swdec-nvenc")
        check(WATCHDOG[2], json.loads(lentadb.get_setting("nvdec_refused") or "[]") == ["av1/yuv420p10le"])
        check(WATCHDOG[3], session.fallback_cmd is None)
    finally:
        T.STARTUP_GRACE = grace_was
        if session:
            session.stopped = True
            try:
                session.proc.kill()
            except (OSError, AttributeError):
                pass
        subprocess.run(["rm", "-rf", str(work)], check=False)
        lentadb.set_setting("nvdec_refused", "")
    check(WATCHDOG[4], T.STARTUP_GRACE <= 20)

src_t = src("server/lenta/transcode.py")
check("a kill by the watchdog is not mistaken for 'we stopped it on purpose'",
      "and not session.force_fallback" in src_t)
check("but a real stop still ends it, so stopping a film does not relaunch it",
      "if session.stopped:\n                return" in src_t)
check("and a session with nothing safer to fall back to is left alone",
      "if session.stopped or session.produced_segments() or not session.fallback_cmd" in src_t)

# ---------------------------------------------------------------------------------------------------
print("\n3. the quality menu offers only what would change something")

player = src("web/js/player.js")
limits = re.search(r"const QUALITY_LIMITS = \{(.+?)\};", player, re.S)
check("the client states the ceilings it is choosing against", limits is not None)
if limits:
    client = {m[0]: (int(m[1]), int(m[2].replace("_", "")))
              for m in re.findall(r"'(\w+)':\s*\[(\d+),\s*([\d_]+)\]", limits.group(1))}
    server = {k: v for k, v in T.QUALITIES.items() if v}
    same = all(client[k] == tuple(v) for k, v in server.items() if k in client)
    check(f"and they are the server's own numbers ({sorted(client)})",
          sorted(client) == sorted(server) and same)
check("the menu filters out a quality the file already fits inside",
      "QUALITIES.filter(([v]) => v === P.quality || !fitsQuality(v, currentFile()))" in player)
check("the test for that is the same one `decide()` applies, 1.3x allowance and all",
      "(f.bitrate || 0) <= t[1] * 1.3" in player
      and "(f[\"bitrate\"] or 0) <= target[1] * 1.3" in src("server/lenta/api_playback.py"))
check("the quality you are already on is always still shown, so the tick has somewhere to sit",
      "v === P.quality ||" in player)

# His film, and the three shapes that must keep their options. Evaluated the way the client does it.
LADDER = [("1080p", 1080, 10_000_000), ("720p", 720, 4_000_000), ("480p", 480, 1_500_000)]


def offered(height, bitrate):
    return ["original"] + [q for q, h, b in LADDER if not (height <= h and bitrate <= b * 1.3)]


check("his 720x544 2.6 Mbps film offers only Original and 480p — the two that mean anything",
      offered(544, 2_600_000) == ["original", "480p"])
check("a 1080p 25 Mbps remux still offers 1080p, because there it halves the bitrate",
      "1080p" in offered(1080, 25_000_000))
check("a 1080p 8 Mbps file does not, because it is already inside that budget",
      "1080p" not in offered(1080, 8_000_000))
check("a 4K film offers all three", offered(2160, 40_000_000) == ["original", "1080p", "720p", "480p"])

# ---------------------------------------------------------------------------------------------------
print("\n4. the safe command has to be safe about the decoder too (1.21.3)")

# 1.21.2 shipped and his AV1 film still would not play. The pipeline choice was right by then — the
# log said "this card has already failed to decode av1/yuv420p10le" — but the command it ran still
# carried `-hwaccel cuda`, because only `gpu_pipeline_possible()` had been taught to ask. FFmpeg does
# not degrade to software when that fails; measured on his server, every frame said:
#     [av1] Failed setup for format cuda: hwaccel initialisation returned error.
#     [dec:av1] Error submitting packet to decoder: Function not implemented
work4 = Path(tempfile.mkdtemp())
UNDERWATER = {"id": 1, "video_codec": "av1", "pix_fmt": "yuv420p10le", "height": 1080,
              "bitrate": 8_000_000, "hdr": 0, "duration": 5400, "path": "/m/Underwater.mkv",
              "container": "mkv", "audio": [], "subtitles": []}


def hwaccels(f, gpu=False):
    """Which hardware decoders the built command asks for."""
    try:
        cmd = T.build_command(f, work4, 0, "transcode", "original", None, None, gpu=gpu)
    except Exception as e:                       # an older tree may not accept this file at all
        return [f"build failed: {e}"]
    return [cmd[i + 1] for i, a in enumerate(cmd) if a == "-hwaccel"]


if missing(T, "nvdec_can_decode", "without_hwaccel"):
    for n in ("a refused codec is decoded on the processor, with no -hwaccel at all",
              "the fallback built before the failure has the flag taken off it",
              "and the flag's value goes with it, not just the flag",
              "an H.264 film still gets the hardware decoder",
              "the panel says CPU decode rather than implying the card is helping"):
        check(n, False)
else:
    lentadb.set_setting("nvdec_refused", "")
    before = hwaccels(UNDERWATER)
    check(f"before anything is learned the card is still tried ({before or 'none'})", before == ["cuda"])
    T.remember_nvdec_failure("av1", "yuv420p10le")
    after = hwaccels(UNDERWATER)
    check(f"a refused codec is decoded on the processor, with no -hwaccel at all ({after or 'none'})",
          after == [])
    check("an H.264 film still gets the hardware decoder",
          hwaccels(dict(UNDERWATER, video_codec="h264", pix_fmt="yuv420p")) == ["cuda"])
    check("a codec NVDEC never claimed is not asked for either, learned or not",
          hwaccels(dict(UNDERWATER, video_codec="theora", pix_fmt="yuv420p")) == [])

    # The first film of a newly-refused codec is the awkward one: `start_session` builds the fallback
    # before the failure happens, so it cannot have consulted the learned list.
    stripped = T.without_hwaccel(["ffmpeg", "-hwaccel", "cuda", "-copyts", "-i", "u.mkv"])
    check("the fallback built before the failure has the flag taken off it",
          "-hwaccel" not in stripped)
    check(f"and the flag's value goes with it, not just the flag ({stripped[:3]})",
          "cuda" not in stripped and stripped[:2] == ["ffmpeg", "-copyts"])
    check("a full GPU command loses all three of its hardware flags",
          T.without_hwaccel(["ffmpeg", "-hwaccel", "cuda", "-hwaccel_output_format", "cuda",
                             "-extra_hw_frames", "4", "-i", "u.mkv"]) == ["ffmpeg", "-i", "u.mkv"])
    check("and a command with none of them is returned unchanged",
          T.without_hwaccel(["ffmpeg", "-copyts", "-i", "u.mkv"]) == ["ffmpeg", "-copyts", "-i", "u.mkv"])
    check("the fallback branch strips it rather than reusing the command as built",
          "cmd = without_hwaccel(cmd)" in src_t)

    check("the panel says CPU decode rather than implying the card is helping",
          T.PIPELINE_LABELS.get("swdec-nvenc", "").startswith("CPU decode"))
    check("and that is the pipeline the fallback reports",
          'session.pipeline = "swdec-nvenc"' in src_t)
    lentadb.set_setting("nvdec_refused", "")
    subprocess.run(["rm", "-rf", str(work4)], check=False)

print()
if fails:
    print(f"{len(fails)} failed:")
    for f in fails:
        print(f"  - {f}")
    raise SystemExit(1)
print("all good")
