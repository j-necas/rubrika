"""Server start, development and diagnostic commands; not a second agent application."""
import argparse
import asyncio
import importlib.metadata
import json
from pathlib import Path
import sys

from .config import Settings
from .network import SafeSource
from .service import ResearchService
from .storage import Store


def make_service(cfg,**source_kwargs):
    store=Store(cfg)
    return ResearchService(cfg,store,SafeSource(cfg,store,**source_kwargs))


def start_service(cfg):
    """Service for the MCP server. Every start trims the source cache; research is removed only by `rubrika cleanup`.
    Housekeeping never blocks the start: several servers (one per Claude session) may share the data dir."""
    service=make_service(cfg)
    try: service.source.cleanup_cache()
    except OSError as e: print(f"rubrika: cache cleanup skipped: {e}",file=sys.stderr)
    return service


async def diagnostic(args):
    cfg=Settings()
    if args.command=="demo":
        from .demo import DemoArchive,run_demo
        from .reporting import export_report
        cfg=Settings(_env_file=None,data_dir=Path(args.data_dir),live_enabled=True,request_interval=0)
        archive=DemoArchive()
        service=make_service(cfg,transport=archive.transport,respect_robots=True,source_mode="synthetic_demo")
        try:
            sid,trace,report=await run_demo(service)
            output=Path(args.output)
            export_report(service,sid,"local",output,"synthetic_demo")
            print(json.dumps({"source_mode":"synthetic_demo","research_id":sid,"trace":trace,
                    "report":str(output),"network":"All requests intercepted by a synthetic fixture; no MZA accessed.",
                    "handwriting_evaluated":False},ensure_ascii=False,indent=2))
        finally:
            await service.close();service.store.close()
        return 0
    if args.command=="probe":
        if not args.allow_live:
            print("Živé testování vyžaduje --allow-live po ověření podmínek přístupu.",file=sys.stderr)
            return 2
        cfg.live_enabled=True
        cfg.data_dir=Path(args.data_dir)
        service=make_service(cfg)
        trace=[]
        async def call(name,**kwargs):
            result=await service.call(name,"local",kwargs)
            trace.append({"tool":name,"result":result.data,"has_image":bool(result.image_b64)})
            if result.is_error: raise RuntimeError(result.data["error"]["code"])
            return result.data
        code=0
        try:
            s=await call("start_research",query={"raw_query":"Technický test živého napojení, ne hledání konkrétní osoby.",
                        "place_hint":args.place,"event_type":args.event_type,
                        "year_from":args.year_from,"year_to":args.year_to})
            sid=s["research_id"]
            p=await call("find_place",research_id=sid,term=args.place,kind="parish_registers",archives=["mza"])
            matches=p["places"]
            if len(matches)!=1:
                raise RuntimeError("PLACE_NOT_UNIQUE: refine --place; the probe does not guess")
            books=await call("list_sources",research_id=sid,place_ids=[matches[0]["source_id"]],kind="parish_registers")
            if args.image:
                relevant=[b for b in books["sources"] if b["relevance"]["status"]!="excluded"]
                if not relevant: raise RuntimeError("NO_CANDIDATE_IN_FIRST_BATCH: inspect catalog manually")
                opened=await call("open_source",research_id=sid,source_id=relevant[0]["source_id"])
                img=await call("view_scan",research_id=sid,page_id=opened["pages"][0]["page_id"])
                ref=img["image"]
                await call("zoom_scan",research_id=sid,image_id=ref["image_id"],bbox_px={"x":0,"y":0,"width":ref["width"]/2,"height":ref["height"]/2})
        except Exception as e:
            trace.append({"failure":type(e).__name__,"message":str(e)[:500]})
            code=1
        finally:
            Path(args.output).parent.mkdir(parents=True,exist_ok=True)
            Path(args.output).write_text(json.dumps({"source_mode":"live","passed":code==0,"trace":trace,
                "claude_tested":False,"viewer_links_manually_verified":False},ensure_ascii=False,indent=2),encoding="utf-8")
            await service.close();service.store.close()
        print(f"{'PASS' if code==0 else 'FAIL'}: {args.output}")
        return code
    return 2


def main():
    parser=argparse.ArgumentParser(description="Rubrika")
    sub=parser.add_subparsers(dest="command",required=True)
    sub.add_parser("serve",help="Run the MCP server over stdio (what the Claude plugin starts)")
    demo=sub.add_parser("demo",help="Offline synthetic engineering demo, no genealogy evaluation")
    demo.add_argument("--data-dir",default=".demo-data")
    demo.add_argument("--output",default="demo-report.html")
    probe=sub.add_parser("probe",help="Explicit read-only live source diagnostic")
    probe.add_argument("--allow-live",action="store_true")
    probe.add_argument("--place",required=True)
    probe.add_argument("--event-type",choices=("birth","marriage","death"),default="birth")
    probe.add_argument("--year-from",type=int,default=1840)
    probe.add_argument("--year-to",type=int,default=1845)
    probe.add_argument("--image",action="store_true")
    probe.add_argument("--data-dir",default="rubrika-data")
    probe.add_argument("--output",default="source-probe.json")
    sub.add_parser("doctor",help="Inspect dependency availability without contacting any service")
    sub.add_parser("cleanup",help="Remove expired private state and excess source cache")
    export=sub.add_parser("export",help="Local operator export (operator has full database access)")
    export.add_argument("research_id");export.add_argument("--output",default="report.html")
    args=parser.parse_args()
    if args.command in ("demo","probe"):
        raise SystemExit(asyncio.run(diagnostic(args)))
    if args.command=="doctor":
        result={}
        for package in ("rubrika","mcp","httpx","httpcore","pydantic","Pillow"):
            try: result[package]=importlib.metadata.version(package)
            except importlib.metadata.PackageNotFoundError: result[package]="NOT_INSTALLED"
        print(json.dumps(result,indent=2));return
    cfg=Settings()
    if args.command=="cleanup":
        store=Store(cfg);source=SafeSource(cfg,store)
        print(json.dumps({**store.cleanup(),"deleted_cache_entries":source.cleanup_cache()}))
        asyncio.run(source.close());store.close();return
    if args.command=="export":
        from .reporting import export_report
        service=make_service(cfg)
        row=service.store.db.execute("SELECT owner FROM searches WHERE id=?",(args.research_id,)).fetchone()
        if not row: parser.error("Unknown search")
        export_report(service,args.research_id,row[0],Path(args.output),"operator_export")
        asyncio.run(service.close());service.store.close();return
    try:
        import mcp
        from .server import make_server
    except ImportError:
        parser.error("Official MCP SDK 2.2.0 is not installed. Run: pip install -e ./plugin/server")
    service=start_service(cfg)
    try: make_server(service).run(transport="stdio")
    finally: service.store.close()

if __name__=="__main__": main()
