"""SQLite state, ownership checks, atomic accounting and idempotent evidence."""
from __future__ import annotations

import hashlib
import json
import sqlite3
import threading
import time
import uuid
from contextlib import contextmanager
from pathlib import Path
from typing import Any

from .archives import ARCHIVES
from .config import Settings
from .errors import ResearchError
from .models import SearchQuery


def dump(v: Any) -> str:
    return json.dumps(v, ensure_ascii=False, sort_keys=True, separators=(",", ":"))


def uid(prefix: str) -> str:
    return prefix + "_" + uuid.uuid4().hex


class Store:
    def __init__(self, settings: Settings):
        self.cfg = settings
        settings.data_dir.mkdir(parents=True, exist_ok=True, mode=0o700)
        self.lock = threading.RLock()
        self.db = sqlite3.connect(settings.data_dir / "research.sqlite", check_same_thread=False,
                                  isolation_level=None)
        self.db.row_factory = sqlite3.Row
        self.db.execute("PRAGMA foreign_keys=ON")
        self.db.execute("PRAGMA journal_mode=WAL")
        self.db.execute("PRAGMA busy_timeout=5000")
        version = self.db.execute("PRAGMA user_version").fetchone()[0]
        if version not in (0, 1):
            raise RuntimeError(f"Unsupported database schema {version}; refusing destructive migration")
        self.db.executescript('''
        CREATE TABLE IF NOT EXISTS searches (
            id TEXT PRIMARY KEY, owner TEXT NOT NULL, query TEXT NOT NULL,
            revision INTEGER NOT NULL, created REAL NOT NULL, touched REAL NOT NULL,
            status TEXT NOT NULL, report TEXT);
        CREATE TABLE IF NOT EXISTS revisions (
            sid TEXT REFERENCES searches(id) ON DELETE CASCADE, revision INTEGER,
            query TEXT NOT NULL, created REAL NOT NULL, PRIMARY KEY(sid,revision));
        CREATE TABLE IF NOT EXISTS entities (
            kind TEXT, id TEXT, payload TEXT NOT NULL, fetched REAL NOT NULL,
            PRIMARY KEY(kind,id));
        CREATE TABLE IF NOT EXISTS search_entities (
            sid TEXT REFERENCES searches(id) ON DELETE CASCADE,
            kind TEXT, id TEXT, payload TEXT, PRIMARY KEY(sid,kind,id));
        CREATE TABLE IF NOT EXISTS counters (
            sid TEXT REFERENCES searches(id) ON DELETE CASCADE,
            metric TEXT, value INTEGER NOT NULL, PRIMARY KEY(sid,metric));
        CREATE TABLE IF NOT EXISTS daily (
            scope TEXT, day INTEGER, metric TEXT, value INTEGER NOT NULL,
            PRIMARY KEY(scope,day,metric));
        CREATE TABLE IF NOT EXISTS images (
            id TEXT PRIMARY KEY, sid TEXT REFERENCES searches(id) ON DELETE CASCADE,
            payload TEXT NOT NULL, file TEXT NOT NULL, created REAL NOT NULL);
        CREATE TABLE IF NOT EXISTS observations (
            id TEXT PRIMARY KEY, sid TEXT REFERENCES searches(id) ON DELETE CASCADE,
            revision INTEGER NOT NULL, idem TEXT NOT NULL, body_hash TEXT NOT NULL,
            payload TEXT NOT NULL, created REAL NOT NULL, UNIQUE(sid,idem));
        CREATE TABLE IF NOT EXISTS meta (key TEXT PRIMARY KEY,value TEXT NOT NULL);
        CREATE INDEX IF NOT EXISTS idx_search_owner ON searches(owner,touched);
        PRAGMA user_version=1;
        ''')

    @contextmanager
    def tx(self):
        with self.lock:
            self.db.execute("BEGIN IMMEDIATE")
            try:
                yield self.db
                self.db.execute("COMMIT")
            except BaseException:
                self.db.execute("ROLLBACK")
                raise

    def close(self):
        self.db.close()

    def _search(self, db, sid: str, owner: str, touch: bool = True) -> dict:
        row = db.execute("SELECT * FROM searches WHERE id=? AND owner=?", (sid, owner)).fetchone()
        if row is None:
            raise ResearchError("NOT_FOUND", "Hledání není dostupné tomuto účtu.")
        if self.cfg.retention_days and row["touched"] < time.time() - self.cfg.retention_days * 86400:
            raise ResearchError("SEARCH_EXPIRED", "Vypršela doba uchování hledání.")
        if touch:
            db.execute("UPDATE searches SET touched=? WHERE id=?", (time.time(), sid))
        result = dict(row)
        result["query"] = json.loads(row["query"])
        result["report"] = json.loads(row["report"]) if row["report"] else None
        return result

    def search(self, sid: str, owner: str) -> dict:
        with self.tx() as db:
            return self._search(db, sid, owner)

    def create(self, owner: str, query: SearchQuery) -> dict:
        now, sid = time.time(), uid("search")
        body = dump(query.model_dump())
        with self.tx() as db:
            db.execute("INSERT INTO searches VALUES(?,?,?,?,?,?,?,?)",
                       (sid, owner, body, 1, now, now, "active", None))
            db.execute("INSERT INTO revisions VALUES(?,?,?,?)", (sid, 1, body, now))
        return {"research_id": sid, "revision": 1, "query": query.model_dump(),
                "checkpoint_images": self.cfg.checkpoint_images, "status": "active"}

    def update(self, sid: str, owner: str, query: SearchQuery, expected_revision: int) -> dict:
        with self.tx() as db:
            s = self._search(db, sid, owner)
            if s["revision"] != expected_revision:
                raise ResearchError("REVISION_CONFLICT", "Nejprve obnovte stav hledání.",
                                    actual_revision=s["revision"])
            revision = expected_revision + 1
            db.execute("UPDATE searches SET query=?,revision=?,status='active',report=NULL WHERE id=?",
                       (dump(query.model_dump()), revision, sid))
            db.execute("INSERT INTO revisions VALUES(?,?,?,?)",
                       (sid, revision, dump(query.model_dump()), time.time()))
        return {"research_id": sid, "revision": revision, "query": query.model_dump(),
                "warning": "Starší pozorování zůstala zachována; znovu posuďte relevanci podle nového zadání."}

    def entity(self, kind: str, eid: str, ttl: int | None = None) -> dict | None:
        with self.lock:
            r = self.db.execute("SELECT * FROM entities WHERE kind=? AND id=?", (kind, eid)).fetchone()
        if r is None or (ttl is not None and r["fetched"] < time.time() - ttl):
            return None
        return json.loads(r["payload"])

    def put_entity(self, kind: str, eid: str, payload: dict):
        with self.tx() as db:
            db.execute("INSERT OR REPLACE INTO entities VALUES(?,?,?,?)",
                       (kind, eid, dump(payload), time.time()))

    def link(self, sid: str, owner: str, kind: str, eid: str, payload: dict | None = None):
        with self.tx() as db:
            self._search(db, sid, owner)
            db.execute("INSERT OR REPLACE INTO search_entities VALUES(?,?,?,?)",
                       (sid, kind, eid, dump(payload or {})))

    def links(self, sid: str, owner: str, kind: str) -> list[dict]:
        with self.tx() as db:
            self._search(db, sid, owner)
            return [{"id": r["id"], **json.loads(r["payload"])} for r in db.execute(
                "SELECT id,payload FROM search_entities WHERE sid=? AND kind=? ORDER BY id", (sid, kind))]

    def has_link(self, sid: str, owner: str, kind: str, eid: str) -> bool:
        with self.tx() as db:
            self._search(db, sid, owner)
            return db.execute("SELECT 1 FROM search_entities WHERE sid=? AND kind=? AND id=?",
                              (sid, kind, eid)).fetchone() is not None

    def _source_daily_cap(self, source: str | None) -> int | None:
        """Daily cap on the requests of one MZA application (the other archives have one cap each, see ARCHIVES)."""
        setting = {"aron": "aron_global_daily_requests", "census": "census_global_daily_requests",
                   "sketches": "sketches_global_daily_requests", "acta_publica": "acta_global_daily_requests",
                   "mza_images": "mza_images_global_daily_requests"}.get(source)
        return getattr(self.cfg, setting) if setting else None

    def _daily_caps(self, source: str | None, archive: str | None) -> list[tuple[str, int]]:
        scopes = []
        if cap := self._source_daily_cap(source):
            scopes.append(("global:" + source, cap))
        spec = ARCHIVES.get(archive) if archive else None
        if spec and spec.daily_requests_setting:
            scopes.append(("archive:" + spec.code, getattr(self.cfg, spec.daily_requests_setting)))
        return scopes

    def scope_cap(self, scope: str) -> int | None:
        """The cap of a daily scope as stored in the table: "global:<source>", "archive:<code>" or "book:<id>"."""
        kind, _, name = scope.partition(":")
        if kind == "global":
            return self._source_daily_cap(name)
        if kind == "archive" and (spec := ARCHIVES.get(name)) and spec.daily_requests_setting:
            return getattr(self.cfg, spec.daily_requests_setting)
        if kind == "book":
            return self.cfg.book_daily_images
        return None

    @staticmethod
    def _daily_add(db, day: int, scope: str, cap: int | None, metric: str, amount: int):
        if cap is None:
            return
        r = db.execute("SELECT value FROM daily WHERE scope=? AND day=? AND metric=?",
                       (scope, day, metric)).fetchone()
        value = (r[0] if r else 0) + amount
        if value > cap:
            raise ResearchError("BUDGET_EXCEEDED", "Archiv dnes víc nedá: byl vyčerpán jeho denní strop, zítra se obnoví.",
                                scope=scope, metric=metric, limit=cap)
        db.execute("INSERT OR REPLACE INTO daily VALUES(?,?,?,?)", (scope, day, metric, max(0, value)))

    def check_book_image(self, book_id: str):
        """Refuse early when the per-book cap is spent; nothing is charged (charge only after a successful render)."""
        with self.tx() as db:
            r = db.execute("SELECT value FROM daily WHERE scope=? AND day=? AND metric='images'",
                           ("book:" + book_id, int(time.time() // 86400))).fetchone()
            if r and r[0] >= self.cfg.book_daily_images:
                raise ResearchError("BUDGET_EXCEEDED", "Z této knihy dnes víc snímků nedostanete: denní strop na knihu se zítra obnoví.", scope="book:" + book_id,
                                    metric="images", limit=self.cfg.book_daily_images)

    def charge_book_image(self, owner: str, book_id: str):
        """Daily cap on image results per book, shared by all searches and owners of this data directory."""
        with self.tx() as db:
            self._daily_add(db, int(time.time() // 86400), "book:" + book_id, self.cfg.book_daily_images, "images", 1)

    def full_res_used(self, page_id: str) -> int:
        """Source pixels of this page ever reserved at the full-resolution tier (lifetime, all owners, never pruned)."""
        return int(self.meta_get("fullres:" + page_id, 0))

    def reserve_full_res(self, page_id: str, pixels: int, cap: int):
        """Atomically add `pixels` to the page's lifetime full-resolution total or refuse when that passes `cap`.
        The total does not reset with the day: otherwise the whole scan would be reachable over a few days."""
        with self.tx() as db:
            r = db.execute("SELECT value FROM meta WHERE key=?", ("fullres:" + page_id,)).fetchone()
            used = int(json.loads(r[0])) if r else 0
            if used + pixels > cap:
                raise ResearchError("BUDGET_EXCEEDED", "Celý snímek v plném rozlišení archiv vydává jen za captchou; "
                                    "Rubrika ho neskládá. Menší výřezy nebo přehled jsou v pořádku, celý snímek otevřete v prohlížeči.",
                                    scope="fullres:" + page_id, metric="pixels", limit=cap, used=used)
            db.execute("INSERT OR REPLACE INTO meta VALUES(?,?)", ("fullres:" + page_id, dump(used + pixels)))

    def release_full_res(self, page_id: str, pixels: int):
        """Give back a reservation whose render failed."""
        with self.tx() as db:
            r = db.execute("SELECT value FROM meta WHERE key=?", ("fullres:" + page_id,)).fetchone()
            used = int(json.loads(r[0])) if r else 0
            db.execute("INSERT OR REPLACE INTO meta VALUES(?,?)", ("fullres:" + page_id, dump(max(0, used - pixels))))

    def count_request(self, sid: str, owner: str, source: str | None = None, archive: str | None = None):
        """One archive request: refused when it would pass a daily cap of its archive; counted in the research for its status."""
        day = int(time.time() // 86400)
        with self.tx() as db:
            self._search(db, sid, owner)
            for scope, cap in self._daily_caps(source, archive):
                self._daily_add(db, day, scope, cap, "requests", 1)
            self._count(db, sid, "requests", 1)

    @staticmethod
    def _count(db, sid: str, metric: str, amount: int):
        db.execute("INSERT INTO counters VALUES(?,?,?) ON CONFLICT(sid,metric) DO UPDATE SET value=value+excluded.value",
                   (sid, metric, amount))

    def _counters(self, db, sid: str) -> dict:
        return dict(db.execute("SELECT metric,value FROM counters WHERE sid=?", (sid,)).fetchall())

    def check_checkpoint(self, sid: str, owner: str):
        """Pause before the next archive image once `checkpoint_images` came since the user last agreed to go on."""
        with self.tx() as db:
            self._search(db, sid, owner)
            c = self._counters(db, sid)
            since = c.get("images", 0) - c.get("checkpoint", 0)
            if since >= self.cfg.checkpoint_images:
                raise ResearchError("CHECKPOINT", "Kontrolní bod: od posledního potvrzení přišlo "
                                    f"{since} snímků z archivů. Shrňte uživateli, co je hotové a co zbývá, a zeptejte se, "
                                    "zda pokračovat; teprve na jeho souhlas zavolejte continue_research.",
                                    images_since_checkpoint=since, checkpoint_images=self.cfg.checkpoint_images)

    def confirm_checkpoint(self, sid: str, owner: str) -> dict:
        with self.tx() as db:
            self._search(db, sid, owner)
            images = self._counters(db, sid).get("images", 0)
            db.execute("INSERT OR REPLACE INTO counters VALUES(?,?,?)", (sid, "checkpoint", images))
        return {"images_total": images, "next_checkpoint_after": self.cfg.checkpoint_images}

    def usage(self, sid: str, owner: str) -> dict:
        """What this research used, when the next checkpoint comes and what the archives still give today."""
        day = int(time.time() // 86400)
        with self.tx() as db:
            self._search(db, sid, owner)
            c = self._counters(db, sid)
            rows = db.execute("SELECT scope,metric,value FROM daily WHERE day=?", (day,)).fetchall()
            books = {r["id"] for r in db.execute("SELECT id FROM search_entities WHERE sid=? AND kind='opened_book'", (sid,))}
        today = {}
        for r in rows:
            cap = self.scope_cap(r["scope"])
            if cap is None or (r["scope"].startswith("book:") and r["scope"][5:] not in books):
                continue
            today[r["scope"]] = {"metric": r["metric"], "used": r["value"], "limit": cap, "remaining": max(0, cap - r["value"])}
        since = c.get("images", 0) - c.get("checkpoint", 0)
        return {"archive_requests": c.get("requests", 0), "archive_images": c.get("images", 0),
                "images_until_checkpoint": max(0, self.cfg.checkpoint_images - since), "today": today}

    def save_image(self, sid: str, owner: str, payload: dict, data: bytes, archive_image: bool = True):
        from .models import ImageRef
        ref = ImageRef.model_validate(payload)
        directory = self.cfg.data_dir / "private" / sid
        directory.mkdir(parents=True, exist_ok=True, mode=0o700)
        path = directory / (ref.image_id + ".jpg")
        tmp = path.with_suffix(".tmp")
        tmp.write_bytes(data)
        tmp.replace(path)
        try:
            with self.tx() as db:
                self._search(db, sid, owner)
                if archive_image: self._count(db, sid, "images", 1)
                db.execute("INSERT INTO images VALUES(?,?,?,?,?)",
                           (ref.image_id, sid, dump(payload), str(path), time.time()))
        except BaseException:
            path.unlink(missing_ok=True)
            raise

    def image(self, sid: str, owner: str, iid: str) -> dict:
        with self.tx() as db:
            self._search(db, sid, owner)
            r = db.execute("SELECT payload,file FROM images WHERE id=? AND sid=?", (iid, sid)).fetchone()
            if r is None:
                raise ResearchError("NOT_FOUND", "Obraz není součástí tohoto hledání.")
            return {"ref": json.loads(r["payload"]), "file": r["file"]}

    def observations(self, sid: str, owner: str) -> list[dict]:
        with self.tx() as db:
            self._search(db, sid, owner)
            return [{"observation_id": r["id"], "query_revision": r["revision"],
                     "created_at": r["created"], **json.loads(r["payload"])} for r in db.execute(
                "SELECT * FROM observations WHERE sid=? ORDER BY created", (sid,))]

    @staticmethod
    def _same_but_version(old, body: dict) -> bool:
        """A finding saved by an older skill_version default is the same finding when only that field differs."""
        stored = json.loads(old["payload"]).get("skill_version")
        if stored is None or stored == body.get("skill_version"):
            return False
        return hashlib.sha256(dump({**body, "skill_version": stored}).encode()).hexdigest() == old["body_hash"]

    def record_finding(self, sid: str, owner: str, body: dict, idem: str) -> dict:
        digest = hashlib.sha256(dump(body).encode()).hexdigest()
        with self.tx() as db:
            s = self._search(db, sid, owner)
            old = db.execute("SELECT id,body_hash,payload FROM observations WHERE sid=? AND idem=?", (sid, idem)).fetchone()
            if old:
                if old["body_hash"] != digest and not self._same_but_version(old, body):
                    raise ResearchError("IDEMPOTENCY_CONFLICT", "Klíč již označuje jiný obsah.")
                return {"observation_id": old["id"], "deduplicated": True}
            count = db.execute("SELECT COUNT(*) FROM observations WHERE sid=?", (sid,)).fetchone()[0]
            if count >= 500:
                raise ResearchError("BUDGET_EXCEEDED", "Limit 500 pozorování na hledání.")
            oid = uid("obs")
            db.execute("INSERT INTO observations VALUES(?,?,?,?,?,?,?)",
                       (oid, sid, s["revision"], idem, digest, dump(body), time.time()))
            db.execute("UPDATE searches SET report=NULL WHERE id=?", (sid,))
            return {"observation_id": oid, "deduplicated": False}

    def save_report(self, sid: str, owner: str, report: dict):
        with self.tx() as db:
            self._search(db, sid, owner)
            db.execute("UPDATE searches SET report=?,status=? WHERE id=?",
                       (dump(report), report["execution_status"], sid))

    def claim_host(self, host: str, token: str, interval: float, lease: float, groups: list[str]) -> float:
        """Try to start a request to `host`: 0.0 = go (lease written), else seconds to wait before asking again.
        One request in flight per host across processes; spacing is measured from the actual start.
        Raises RATE_LIMITED when any group is in cooldown. No transaction outlives this call."""
        with self.tx() as db:
            now = time.time()
            def get(key, default=None):
                row = db.execute("SELECT value FROM meta WHERE key=?", (key,)).fetchone()
                return json.loads(row[0]) if row else default
            # The v0.1 global key is still honoured until it expires.
            blocked = max([get("source_block_until", 0)] + [get("source_block_until:" + g, 0) for g in groups])
            if blocked > now:
                raise ResearchError("RATE_LIMITED", "Přístup ke zdroji je dočasně pozastaven.", retry_after=max(1, round(blocked - now)))
            held = get("host_lease:" + host)
            if held and held.get("until", 0) > now and held.get("token") != token:
                return min(held["until"] - now, 1.0)
            start = get("host_last_start:" + host, 0) + interval
            if start > now:
                return start - now
            db.execute("INSERT OR REPLACE INTO meta VALUES(?,?)",
                       ("host_lease:" + host, dump({"token": token, "until": now + lease})))
            db.execute("INSERT OR REPLACE INTO meta VALUES(?,?)", ("host_last_start:" + host, dump(now)))
            return 0.0

    def release_host(self, host: str, token: str):
        with self.tx() as db:
            row = db.execute("SELECT value FROM meta WHERE key=?", ("host_lease:" + host,)).fetchone()
            if row and json.loads(row[0]).get("token") == token:
                db.execute("DELETE FROM meta WHERE key=?", ("host_lease:" + host,))

    def meta_get(self, key: str, default=None):
        with self.lock:
            row = self.db.execute("SELECT value FROM meta WHERE key=?", (key,)).fetchone()
        return json.loads(row[0]) if row else default

    def meta_set(self, key: str, value):
        with self.tx() as db:
            db.execute("INSERT OR REPLACE INTO meta VALUES(?,?)", (key, dump(value)))

    def cleanup(self) -> dict:
        import shutil
        # Without a retention nothing is old enough to delete.
        cutoff = time.time() - self.cfg.retention_days * 86400 if self.cfg.retention_days else 0
        with self.tx() as db:
            ids = [r[0] for r in db.execute("SELECT id FROM searches WHERE touched<?", (cutoff,))]
            for sid in ids:
                db.execute("DELETE FROM searches WHERE id=?", (sid,))
            db.execute("DELETE FROM daily WHERE day<?", (int(time.time()//86400)-2,))
        for sid in ids:
            shutil.rmtree(self.cfg.data_dir / "private" / sid, ignore_errors=True)
        return {"deleted_searches": len(ids)}
