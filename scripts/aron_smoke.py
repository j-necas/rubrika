"""Manual live ARON smoke test. Run only with RUBRIKA_LIVE_ENABLED=true and RUBRIKA_ARON_ENABLED=true.
Prints a JSON summary without image data. Not part of pytest; never run in CI."""
import asyncio
import json
import sys

from rubrika.cli import make_service
from rubrika.config import Settings


async def main(term: str, year_from: int, year_to: int):
    cfg = Settings()
    if not (cfg.live_enabled and cfg.aron_enabled):
        sys.exit("Nastavte RUBRIKA_LIVE_ENABLED=true a RUBRIKA_ARON_ENABLED=true (po posouzení podmínek užití ARON).")
    service = make_service(cfg)
    summary = {}

    async def call(name, **args):
        out = await service.call(name, "local", args)
        if out.is_error:
            print(json.dumps({"failed_tool": name, "error": out.data, "summary": summary}, ensure_ascii=False, indent=2))
            raise SystemExit(1)
        return out.data

    def require(items, message):
        # A blank result is expected data (e.g. no digitized units for this place/period), not a bug;
        # fail loudly with a clear message instead of an IndexError deep in the summary.
        if not items:
            print(json.dumps({"failed": message, "summary": summary}, ensure_ascii=False, indent=2))
            raise SystemExit(1)
        return items

    try:
        created = await call("start_research", query={"raw_query": f"ARON smoke test: {term}", "place_hint": term,
                                                    "year_from": year_from, "year_to": year_to})
        sid = created["research_id"]
        places = await call("find_place", research_id=sid, term=term, kind="land_records", archives=["mza"])
        require(places["places"], f"ARON nenašel žádnou obec pro '{term}'.")
        summary["places"] = [p["label"] for p in places["places"]]
        units = await call("list_sources", research_id=sid, place_ids=[places["places"][0]["source_id"]], kind="land_records")
        require(units["sources"], "ARON nevrátil žádné archiválie pro tuto obec a období.")
        summary["units"] = [{"title": u["title"], "fund": u["fund"]["mark"], "dating": u["dating"]["raw"],
                             "relevance": u["relevance"]["status"]} for u in units["sources"]]
        summary["listing"] = {"total": units["total"], "complete": units["listing_complete"], "warnings": units["warnings"]}
        chosen = next((u for u in units["sources"] if u["relevance"]["status"] == "candidate"), units["sources"][0])
        opened = await call("open_source", research_id=sid, source_id=chosen["source_id"], limit=1)
        require(opened["pages"], "Otevřená archiválie nemá žádný podporovaný snímek.")
        summary["opened"] = {"title": opened["unit"]["title"], "citation": opened["citation"], "total": opened["total"],
                             "warnings": opened["warnings"], "viewer_url": opened["pages"][0]["viewer_url"]}
        page = await call("view_scan", research_id=sid, page_id=opened["pages"][0]["page_id"])
        ref = page["image"]
        crop = await call("zoom_scan", research_id=sid, image_id=ref["image_id"],
                          bbox_px={"x": 0, "y": 0, "width": ref["width"] / 2, "height": ref["height"] / 2})
        await call("record_finding", research_id=sid, idempotency_key="aron-smoke",
                   observation={"kind": "reviewed_region", "image_id": crop["image"]["image_id"],
                                "coverage_status": "overview_only",
                                "text": "Smoke test: výřez přenesen, rukopis nebyl čten."})
        report = await call("finish_research", research_id=sid, stop_reason="user_stopped")
        summary["image"] = {k: crop["image"][k] for k in ("source", "citation", "image_key", "viewer_url",
                                                          "source_bbox", "sha256", "viewer_link_verified")}
        summary["usage"] = report["usage"]
        summary["research_id"] = sid
        print(json.dumps(summary, ensure_ascii=False, indent=2))
    finally:
        await service.close()
        service.store.close()


async def census_main(place: str, year: int):
    cfg = Settings()
    if not (cfg.live_enabled and cfg.census_enabled):
        sys.exit("Nastavte RUBRIKA_LIVE_ENABLED=true a RUBRIKA_CENSUS_ENABLED=true (po posouzení podmínek).")
    service = make_service(cfg)
    summary = {}

    async def call(name, **args):
        out = await service.call(name, "local", args)
        if out.is_error:
            print(json.dumps({"failed_tool": name, "error": out.data, "summary": summary}, ensure_ascii=False, indent=2))
            raise SystemExit(1)
        return out.data

    def require(items, message):
        # A blank result is expected data (e.g. no sets for this place/year, or 1921 reading-room-only), not a bug;
        # fail loudly with a clear message instead of an IndexError deep in the summary.
        if not items:
            print(json.dumps({"failed": message, "summary": summary}, ensure_ascii=False, indent=2))
            raise SystemExit(1)
        return items

    try:
        sid = (await call("start_research", query={"raw_query": f"Census smoke test: {place} {year}",
                                                   "place_hint": place, "year_from": year, "year_to": year}))["research_id"]
        places = await call("find_place", research_id=sid, term=place, kind="census", archives=["mza"])
        require(places["places"], "Našeptávač nevrátil žádné místo.")
        summary["places"] = [p["label"] for p in places["places"]]
        sets = await call("list_sources", research_id=sid, place_ids=[places["places"][0]["source_id"]], kind="census", year_from=year, year_to=year)
        require(sets["sources"], "Pro místo a rok nebyla nalezena žádná sada.")
        summary["sets"] = [{"code": s["code"], "year": s["year"], "archive": s["district_archive"], "part": s["part"]} for s in sets["sources"]]
        chosen = next((s for s in sets["sources"] if s["images_available"]), None)
        require(chosen and [chosen], "Žádná sada se snímky (rok 1921 je jen v badatelně).")
        opened = await call("open_source", research_id=sid, source_id=chosen["source_id"], limit=1)
        require(opened["pages"], "Sada nemá snímky.")
        summary["opened"] = {"citation": opened["citation"], "total": opened["total"], "viewer_url": opened["pages"][0]["viewer_url"]}
        ref = (await call("view_scan", research_id=sid, page_id=opened["pages"][0]["page_id"]))["image"]
        crop = await call("zoom_scan", research_id=sid, image_id=ref["image_id"],
                          bbox_px={"x": 0, "y": 0, "width": ref["width"] / 2, "height": ref["height"] / 2})
        await call("record_finding", research_id=sid, idempotency_key="census-smoke",
                   observation={"kind": "reviewed_region", "image_id": crop["image"]["image_id"],
                                "coverage_status": "overview_only", "text": "Smoke test: výřez přenesen, rukopis nebyl čten."})
        report = await call("finish_research", research_id=sid, stop_reason="user_stopped")
        summary["image"] = {k: crop["image"][k] for k in ("source", "citation", "image_key", "viewer_url", "source_bbox", "sha256")}
        summary["usage"] = report["usage"]
        print(json.dumps(summary, ensure_ascii=False, indent=2))

        # A restricted (1921) set is surfaced as expected data rather than as a bug in the main flow above.
        restricted = next((s for s in sets["sources"] if s["year"] == 1921), None)
        if restricted:
            attempt = await service.call("open_source", "local",
                                         {"research_id": sid, "source_id": restricted["source_id"]})
            print(json.dumps({"restricted_open_attempt": attempt.data}, ensure_ascii=False, indent=2))
    finally:
        await service.close()
        service.store.close()


USAGE = ("Použití: aron_smoke.py \"<obec> <okres nebo sousední město>\" <rok od> <rok do>\n"
         "        aron_smoke.py --census \"<obec>\" <rok sčítání>")

if __name__ == "__main__":
    args = sys.argv[1:]
    if args[:1] == ["--census"] and len(args) == 3:
        asyncio.run(census_main(args[1], int(args[2])))
    elif len(args) == 3 and args[0] != "--census":
        asyncio.run(main(args[0], int(args[1]), int(args[2])))
    else:
        sys.exit(USAGE)
