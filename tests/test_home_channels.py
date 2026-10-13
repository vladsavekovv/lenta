"""A Home section that carries live channels rather than library titles.

Channels are not items: no file, no year, no poster, nothing to open a details page for. So a section
set to "Channels" is built from the tuner's own list instead of the items table, and the questions
worth asking are about the edges — a channel that was ticked and has since been rescanned away, a
section whose settings arrive half-broken, and whether the order is the one a viewer recognises.
"""
import os, sys, tempfile, json

os.environ["LENTA_DATA_DIR"] = tempfile.mkdtemp()
sys.path.insert(0, "/home/claude/lenta/server")
from lenta import db, livetv                                              # noqa: E402
from lenta import api_library as lib                                      # noqa: E402

db.init_db()

fails = []


def check(name, ok):
    print(f"  {'ok  ' if ok else 'FAIL'}  {name}")
    if not ok:
        fails.append(name)


# A provider's line-up, deliberately not in the order they are added.
sid = livetv.add_source("tuner", "dualHD", {"device": "usb:2040:8265:T", "inputs": []})
livetv._save_channels(sid, [
    {"uid": "1:1:3", "name": "Nova TV", "number": 3, "kind": "tv", "provider": "Unics TV",
     "tune": {"delsys": "DVB-C", "frequency": 530_000_000, "service_id": 3}},
    {"uid": "1:1:1", "name": "BNT 1 HD", "number": 1, "kind": "tv", "provider": "Unics TV",
     "tune": {"delsys": "DVB-C", "frequency": 530_000_000, "service_id": 1}},
    {"uid": "1:1:9", "name": "Radio Nova", "number": 9, "kind": "radio", "provider": "Unics TV",
     "tune": {"delsys": "DVB-C", "frequency": 538_000_000, "service_id": 9}},
])
chans = {c["name"]: c["id"] for c in livetv.channels()}
print(f"a line-up of {len(chans)} channels: {', '.join(chans)}")

print("\n1. the rule exists and survives being stored")
check("'channels' is a rule a section can use", "channels" in lib.RULES)


def config(custom):
    """What _sections_config makes of these settings, without going near a real user's preferences."""
    saved = json.dumps({"custom": custom})
    import lenta.api_prefs as ap
    ap.get_prefs = lambda uid: {"home_sections": saved}           # read from inside _sections_config
    return lib._sections_config({"id": 1})


cfg = config([{"id": "c-1", "title": "My channels", "rule": "channels",
               "libs": [7], "channels": [chans["BNT 1 HD"], chans["Nova TV"]]}])
check("the chosen channels are kept", cfg["custom"][0]["channels"] == [chans["BNT 1 HD"], chans["Nova TV"]])
check("the rule is kept", cfg["custom"][0]["rule"] == "channels")

print("\n2. settings that arrive damaged do not bring the page down")
cfg = config([{"id": "c-2", "title": "Rubbish", "rule": "channels", "libs": [],
               "channels": ["seven", None, 3, {"id": 4}]}])
check("only the numbers are kept", cfg["custom"][0]["channels"] == [3])
cfg = config([{"id": "c-3", "title": "No list at all", "rule": "channels", "libs": []}])
check("a section with no channels at all reads as empty", cfg["custom"][0]["channels"] == [])
cfg = config([{"id": "c-4", "title": "Bad rule", "rule": "nonsense", "libs": [], "channels": [1]}])
check("an unknown rule falls back to Recently added", cfg["custom"][0]["rule"] == "recent")

print("\n3. the cards are built from the line-up, in the provider's order")
cards = lib._channel_cards([chans["Nova TV"], chans["BNT 1 HD"]])
check("both are there", len(cards) == 2)
check(f"numbered order, not the order ticked: {[c['title'] for c in cards]}",
      [c["title"] for c in cards] == ["BNT 1 HD", "Nova TV"])
one = cards[0]
check("it says it is a channel", one["kind"] == "channel")
check(f"the artwork is the channel's own: {one['art']}",
      one["art"] == f"/api/livetv/channels/{chans['BNT 1 HD']}/logo")
check("the number is carried", one["number"] == 1)
check("so is who carries it", one["provider"] == "Unics TV")

print("\n4. a channel that has gone away is left out, not drawn broken")
cards = lib._channel_cards([chans["BNT 1 HD"], 99999])
check("the missing one is dropped", [c["title"] for c in cards] == ["BNT 1 HD"])
check("nothing chosen means no row at all", lib._channel_cards([]) == [])

print("\n5. radio is marked as radio, so the card can say so")
cards = lib._channel_cards([chans["Radio Nova"]])
check("a radio station knows it is one", cards[0]["radio"] is True)
check("television does not", lib._channel_cards([chans["Nova TV"]])[0]["radio"] is False)

print()
if fails:
    print(f"{len(fails)} failed: {', '.join(fails)}")
    raise SystemExit(1)
print("channel sections ok")
