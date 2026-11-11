"""Bring watch history over from a Plex Media Server: what was watched, where each film was left, how
many times, and when.

Run by hand, once, on the machine that can read both databases:

    python3 server/lenta/plex_import.py --plex-db PLEX.db --lenta-db LENTA.db \\
        --map saveka1=savek --map Alex=alex            # a dry run: prints what would change
    ... the same --apply                                # writes it

Matching is by **file path**, because Plex and LENTA normally read the same folders: a Plex record
belongs to whichever LENTA title owns the same file. A film renamed since falls back to its TMDB number,
which Plex keeps in its own guid tags. Episodes have no fallback — a TMDB number there names the episode
in TMDB's numbering, which LENTA does not keep — so a renamed episode is reported, not guessed at.

Nothing here can surprise the two servers:

  * **Plex is only ever read from a copy.** Its database is copied (with its write-ahead log) into a
    temporary folder and the copy is opened. Plex's own text-search tables use a tokenizer standard
    SQLite does not have, so those tables are never touched.
  * **LENTA wins whenever it knows better.** A title LENTA has a newer record for keeps LENTA's record.
  * **A dry run is the default.** With --apply, a copy of the LENTA database is taken first with
    SQLite's own backup method, and every write happens in one transaction: all of it, or none.
  * **This file imports nothing from LENTA**, so it cannot reach the configured live database by
    accident: it works on the paths it is given and on nothing else.
"""
from __future__ import annotations

import argparse
import shutil
import sqlite3
import sys
import tempfile
import time
from dataclasses import dataclass, field
from pathlib import Path

MOVIE, EPISODE = 1, 4                     # Plex's metadata_type numbers
GUID_TAG = 314                            # Plex's tag_type for external ids (imdb://, tmdb://, tvdb://)


@dataclass
class PlexRecord:
    account: str
    guid: str
    kind: int
    view_count: int
    offset_ms: int
    when: float
    files: list[str] = field(default_factory=list)
    tmdb: int | None = None


@dataclass
class Change:
    user_id: int
    item_id: int
    position: float
    duration: float | None
    completed: int
    play_count: int
    updated_at: float
    action: str                           # new | update | keep
    source: str                           # path | tmdb


@dataclass
class Plan:
    changes: list[Change] = field(default_factory=list)
    unmatched: list[tuple[str, str, list[str]]] = field(default_factory=list)   # account, guid, files
    accounts: dict[str, str] = field(default_factory=dict)                     # plex name -> lenta name
    ignored_accounts: list[str] = field(default_factory=list)
    # LENTA titles that more than one Plex record landed on — LENTA has merged things Plex keeps apart
    # (two shows with the same name before " - ", a folder of films grouped as one). Newest record used.
    shared: dict[tuple[int, int], list[str]] = field(default_factory=dict)            # (user, item) -> files

    def counts(self) -> dict[str, dict[str, int]]:
        out: dict[str, dict[str, int]] = {}
        for c in self.changes:
            d = out.setdefault(str(c.user_id), {"new": 0, "update": 0, "keep": 0, "by_tmdb": 0})
            d[c.action] += 1
            d["by_tmdb"] += c.source == "tmdb"
        return out


def copy_plex(src: Path, into: Path) -> Path:
    """Copy Plex's database and its write-ahead log; the copy is what gets opened."""
    dst = into / "plex.db"
    shutil.copy2(src, dst)
    for ext in ("-wal", "-shm"):
        side = Path(str(src) + ext)
        if side.exists():
            try:
                shutil.copy2(side, Path(str(dst) + ext))
            except PermissionError:
                pass                      # -shm is an index SQLite rebuilds; -wal is readable where it matters
    return dst


def read_plex(path: Path) -> tuple[dict[int, str], list[PlexRecord]]:
    con = sqlite3.connect(path)
    accounts = {r[0]: r[1] for r in con.execute("SELECT id, name FROM accounts") if r[1]}
    recs: dict[tuple[str, str], PlexRecord] = {}
    for acc, guid, vc, vo, last, upd, kind, mid in con.execute(
            "SELECT s.account_id, s.guid, s.view_count, s.view_offset, s.last_viewed_at, s.updated_at, "
            "mi.metadata_type, mi.id FROM metadata_item_settings s "
            "JOIN metadata_items mi ON mi.guid = s.guid "
            "WHERE mi.metadata_type IN (?, ?) AND (s.view_count > 0 OR s.view_offset > 0)", (MOVIE, EPISODE)):
        name = accounts.get(acc)
        if not name:
            continue
        r = recs.get((name, guid))
        if r is None:
            r = recs[(name, guid)] = PlexRecord(name, guid, kind, int(vc or 0), int(vo or 0),
                                                float(last or upd or 0))
        r.files += [f for (f,) in con.execute(
            "SELECT mp.file FROM media_items m JOIN media_parts mp ON mp.media_item_id = m.id "
            "WHERE m.metadata_item_id = ? AND mp.file <> ''", (mid,)) if f not in r.files]
        if r.tmdb is None:
            row = con.execute("SELECT t.tag FROM taggings tg JOIN tags t ON t.id = tg.tag_id "
                              "WHERE tg.metadata_item_id = ? AND t.tag_type = ? AND t.tag LIKE 'tmdb://%'",
                              (mid, GUID_TAG)).fetchone()
            if row and row[0][7:].isdigit():
                r.tmdb = int(row[0][7:])
    con.close()
    return accounts, list(recs.values())


def remap(path: str, path_map: list[tuple[str, str]]) -> str:
    for old, new in path_map:
        if path == old or path.startswith(old.rstrip("/") + "/"):
            return new.rstrip("/") + path[len(old.rstrip("/")):]
    return path


def plan_import(plex_db: Path, lenta: sqlite3.Connection, mapping: dict[str, str],
                path_map: list[tuple[str, str]] | None = None) -> Plan:
    path_map = path_map or []
    with tempfile.TemporaryDirectory(prefix="lenta-plex-") as tmp:
        accounts, recs = read_plex(copy_plex(plex_db, Path(tmp)))

    users = {r[1].lower(): r[0] for r in lenta.execute("SELECT id, username FROM users")}
    plex_names = {n.lower(): n for n in accounts.values()}
    plan = Plan()
    target: dict[str, int] = {}
    for p, l in mapping.items():
        if p.lower() not in plex_names:
            raise SystemExit(f"Plex has no account called {p!r}. It has: {', '.join(sorted(accounts.values()))}")
        if l.lower() not in users:
            raise SystemExit(f"LENTA has no account called {l!r}.")
        target[plex_names[p.lower()]] = users[l.lower()]
        plan.accounts[plex_names[p.lower()]] = l
    plan.ignored_accounts = sorted(n for n in accounts.values() if n not in target)

    by_path = {r[0]: (r[1], r[2]) for r in lenta.execute(
        "SELECT f.path, f.item_id, f.duration FROM files f JOIN items i ON i.id = f.item_id "
        "WHERE i.kind IN ('movie', 'episode')")}
    by_tmdb: dict[int, tuple[int, float | None]] = {}
    for tid, iid, dur in lenta.execute(
            "SELECT i.tmdb_id, i.id, MAX(f.duration) FROM items i LEFT JOIN files f ON f.item_id = i.id "
            "WHERE i.kind = 'movie' AND i.tmdb_id IS NOT NULL GROUP BY i.id"):
        by_tmdb.setdefault(tid, (iid, dur))
    existing = {(r[0], r[1]): r[2:] for r in lenta.execute(
        "SELECT user_id, item_id, position, completed, play_count, updated_at FROM watch_state")}

    best: dict[tuple[int, int], Change] = {}
    for r in recs:
        uid = target.get(r.account)
        if uid is None:
            continue
        hit, source = None, "path"
        for f in r.files:
            hit = by_path.get(remap(f, path_map))
            if hit:
                break
        if not hit and r.kind == MOVIE and r.tmdb is not None:
            hit, source = by_tmdb.get(r.tmdb), "tmdb"
        if not hit:
            plan.unmatched.append((r.account, r.guid, r.files))
            continue
        item_id, duration = hit
        # Plex keeps a resume point in milliseconds, and keeps it even after a film was watched once
        # and started again — exactly LENTA's "watched before, part way through now".
        position = r.offset_ms / 1000 if r.offset_ms > 0 else 0.0
        completed = 0 if position > 0 else int(r.view_count > 0)
        ch = Change(uid, item_id, position, duration, completed, r.view_count, r.when, "new", source)
        old = existing.get((uid, item_id))
        if old is not None:
            _pos, _done, plays, upd = old
            if (upd or 0) >= r.when:
                ch.action = "keep"
            else:
                ch.action, ch.play_count = "update", max(int(plays or 0), r.view_count)
        plan.shared.setdefault((uid, item_id), []).append(r.files[0] if r.files else r.guid)
        prev = best.get((uid, item_id))
        if prev is None or ch.updated_at > prev.updated_at:      # two Plex records, one LENTA title
            best[(uid, item_id)] = ch
    plan.changes = sorted(best.values(), key=lambda c: (c.user_id, c.item_id))
    plan.shared = {k: v for k, v in plan.shared.items() if len(v) > 1}
    return plan


def backup(lenta_db: Path, into: Path | None = None) -> Path:
    into = into or lenta_db.parent
    dst = into / f"{lenta_db.name}.pre-plex-import-{time.strftime('%Y%m%d-%H%M%S')}"
    src = sqlite3.connect(lenta_db)
    out = sqlite3.connect(dst)
    with out:
        src.backup(out)
    out.close(); src.close()
    return dst


def apply(lenta: sqlite3.Connection, plan: Plan) -> int:
    written = 0
    lenta.execute("BEGIN IMMEDIATE")
    try:
        for c in plan.changes:
            if c.action == "keep":
                continue
            lenta.execute(
                "INSERT INTO watch_state(user_id, item_id, position, duration, completed, play_count, updated_at) "
                "VALUES(?, ?, ?, ?, ?, ?, ?) ON CONFLICT(user_id, item_id) DO UPDATE SET "
                "position = excluded.position, duration = COALESCE(excluded.duration, duration), "
                "completed = excluded.completed, play_count = excluded.play_count, "
                "updated_at = excluded.updated_at",
                (c.user_id, c.item_id, c.position, c.duration, c.completed, c.play_count, c.updated_at))
            written += 1
        lenta.execute("COMMIT")
    except BaseException:
        lenta.execute("ROLLBACK")
        raise
    return written


def report(plan: Plan, names: dict[int, str]) -> str:
    lines = ["Accounts: " + (", ".join(f"Plex {p} -> LENTA {l}" for p, l in plan.accounts.items()) or "none"),
             "Not imported (no --map): " + (", ".join(plan.ignored_accounts) or "none")]
    for uid, c in plan.counts().items():
        lines.append(f"  {names.get(int(uid), uid)}: {c['new']} new, {c['update']} updated, "
                     f"{c['keep']} kept (LENTA's record is newer), {c['by_tmdb']} matched by TMDB number")
    if plan.shared:
        lines.append(f"LENTA titles given more than one Plex record (LENTA has merged them; the newest is used): "
                     f"{len(plan.shared)}")
        for (uid, iid), files in list(plan.shared.items())[:20]:
            lines.append(f"  {names.get(uid, uid)}, LENTA item {iid}: " + " | ".join(Path(f).name for f in files))
    lines.append(f"Unmatched Plex records: {len(plan.unmatched)}")
    for acc, guid, files in plan.unmatched[:20]:
        lines.append(f"  {acc}: {files[0] if files else guid}")
    return "\n".join(lines)


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description="Import watch history from Plex into LENTA (dry run unless --apply).")
    ap.add_argument("--plex-db", required=True, type=Path, help="Plex's com.plexapp.plugins.library.db")
    ap.add_argument("--lenta-db", required=True, type=Path, help="LENTA's lenta.db")
    ap.add_argument("--map", action="append", default=[], metavar="PLEX=LENTA",
                    help="a Plex account and the LENTA account it becomes; repeat for each")
    ap.add_argument("--path-map", action="append", default=[], metavar="PLEX_PATH=LENTA_PATH",
                    help="when the two servers see the media under different folders")
    ap.add_argument("--apply", action="store_true", help="write the changes (a backup is taken first)")
    a = ap.parse_args(argv)
    if not a.map:
        ap.error("give at least one --map PLEX=LENTA (a dry run with a wrong name lists the accounts)")
    mapping = dict(m.split("=", 1) for m in a.map)
    path_map = [tuple(m.split("=", 1)) for m in a.path_map]
    for p in (a.plex_db, a.lenta_db):
        if not p.is_file():
            ap.error(f"{p} does not exist")

    lenta = sqlite3.connect(a.lenta_db, timeout=30, isolation_level=None)
    plan = plan_import(a.plex_db, lenta, mapping, path_map)
    names = {r[0]: r[1] for r in lenta.execute("SELECT id, username FROM users")}
    print(report(plan, names))
    if not a.apply:
        print("\nDry run: nothing was written. Add --apply to write it.")
        return 0
    lenta.close()
    saved = backup(a.lenta_db)
    print(f"\nBackup of the LENTA database: {saved}")
    lenta = sqlite3.connect(a.lenta_db, timeout=30, isolation_level=None)
    print(f"Written: {apply(lenta, plan)} watch records.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
