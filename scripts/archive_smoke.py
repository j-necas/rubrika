"""Manual live smoke test of one archive through the generic tools.
Run only with RUBRIKA_LIVE_ENABLED=true and the archive's own switch (e.g. RUBRIKA_VCA_ENABLED=true), after checking
the archive's terms. Prints a JSON summary without image data. Not part of pytest; never run in CI.

    python scripts/archive_smoke.py --archive vca --kind parish_registers "Hradec Králové" --year 1790
"""
import argparse
import asyncio
import json
import sys

from rubrika.archives import ARCHIVES
from rubrika.cli import make_service
from rubrika.config import Settings


async def main(archive: str, kind: str, term: str, year: int | None):
    cfg = Settings()
    a = ARCHIVES[archive]
    if not cfg.live_enabled or (a.enabled_setting and not getattr(cfg, a.enabled_setting)):
        sys.exit(f"Zapněte RUBRIKA_LIVE_ENABLED a přepínač archivu {archive} (po posouzení podmínek: {a.terms_url}).")
    service = make_service(cfg)
    summary = {"archive": archive, "kind": kind, "term": term}

    async def call(name, **args):
        out = await service.call(name, "local", args)
        if out.is_error:
            print(json.dumps({"failed_tool": name, "error": out.data, "summary": summary}, ensure_ascii=False, indent=2))
            raise SystemExit(1)
        return out.data

    def require(items, message):
        if not items:
            print(json.dumps({"failed": message, "summary": summary}, ensure_ascii=False, indent=2))
            raise SystemExit(1)
        return items

    try:
        query = {"raw_query": f"Smoke test {archive}: {term}", "place_hint": term}
        if year:
            query.update(year_from=year, year_to=year)
        sid = (await call("start_research", query=query))["research_id"]
        places = await call("find_place", research_id=sid, term=term, kind=kind, archives=[archive])
        summary["find_place"] = {"searched": places["searched"], "not_searched": places["not_searched"],
                                 "places": [p["label"] for p in places["places"]][:10], "warnings": places["warnings"]}
        require(places["places"], f"Archiv {archive} nenašel místo '{term}'.")
        # Prefer the place whose name is exactly the term: ARON also returns corporations and parts of a town.
        place = next((p for p in places["places"] if p["names"] and p["names"][0] == term), places["places"][0])
        summary["place"] = place["label"]
        listed = await call("list_sources", research_id=sid, place_ids=[place["source_id"]], kind=kind)
        require(listed["sources"], "Výpis nevrátil žádné prameny.")
        summary["sources"] = {"total": listed["total"], "complete": listed["listing_complete"], "warnings": listed["warnings"],
                              "first": [{"id": s["source_id"], "title": s.get("title"), "storage": s.get("storage"),
                                         "dating": (s.get("dating") or {}).get("raw"), "relevance": s["relevance"]["status"]}
                                        for s in listed["sources"][:5]]}
        chosen = next((s for s in listed["sources"] if s["relevance"]["status"] == "candidate"), listed["sources"][0])
        opened = await call("open_source", research_id=sid, source_id=chosen["source_id"], limit=2)
        require(opened["pages"], "Otevřený pramen nemá podporovaný snímek.")
        summary["opened"] = {"id": chosen["source_id"], "kind": opened["kind"], "citation": opened.get("citation"),
                             "total": opened["total"], "warnings": opened["warnings"],
                             "viewer_url": opened["pages"][0]["viewer_url"], "source_url": opened["pages"][0]["source_url"]}
        page = opened["pages"][min(1, len(opened["pages"]) - 1)]
        view = (await call("view_scan", research_id=sid, page_id=page["page_id"]))["image"]
        # A crop around the centre crosses tile borders at full resolution.
        crop = (await call("zoom_scan", research_id=sid, image_id=view["image_id"],
                           bbox_px={"x": view["width"] * 0.4, "y": view["height"] * 0.4,
                                    "width": view["width"] * 0.2, "height": view["height"] * 0.2}))["image"]
        summary["image"] = {k: crop[k] for k in ("archive", "source", "source_width", "source_height", "source_bbox",
                                                  "width", "height", "robots_disallowed", "viewer_url", "citation")}
        await call("finish_research", research_id=sid, stop_reason="user_stopped")
        status = await call("get_research_status", research_id=sid)
        summary["usage"] = status["usage"]
        summary["robots_tolerated"] = status["coverage"].get("robots_tolerated")
        print(json.dumps(summary, ensure_ascii=False, indent=2))
    finally:
        await service.close()


if __name__ == "__main__":
    p = argparse.ArgumentParser()
    p.add_argument("--archive", required=True, choices=sorted(a for a in ARCHIVES if not ARCHIVES[a].link_only))
    p.add_argument("--kind", default="parish_registers", choices=["parish_registers", "land_records", "census", "cadastral_maps"])
    p.add_argument("--year", type=int)
    p.add_argument("term")
    a = p.parse_args()
    asyncio.run(main(a.archive, a.kind, a.term, a.year))
