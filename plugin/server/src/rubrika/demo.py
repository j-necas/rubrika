"""Explicit, entirely synthetic offline archive. NEVER a fallback for failed MZA access."""
import io
import json
import math
import re
from urllib.parse import parse_qs

import httpx
from PIL import Image, ImageDraw

from .catalog import normalize
from .demo_aron import handle_aron
from .demo_census import handle_census
from .demo_pf import CRAWL_DELAY, handle_pf
from .demo_prihlasky import DemoPrihlasky
from .demo_sketches import DemoSketches
from .demo_soapraha import DemoSoapraha
from .demo_trebon import DemoTrebon
from .demo_vca import handle_vca
from .sources import PF_HOST, PRIHLASKY_HOST, SOAPRAHA_HOST, TREBON_HOST, VCA_HOST, VCA_IMAGES


class DemoArchive:
    width,height,tile_size,overlap=1281,897,128,1

    def __init__(self):
        self.requests=[]
        self.posts=[]
        self.census_sessions={}
        self.soapraha=DemoSoapraha()
        self.sketches=DemoSketches()
        self.trebon=DemoTrebon()
        self.prihlasky=DemoPrihlasky()
        self.pf_crawl_delay=CRAWL_DELAY   # what the synthetic Porta fontium robots.txt asks for (tests may set 0)
        self.image=Image.new("RGB",(self.width,self.height),(248,240,220))
        draw=ImageDraw.Draw(self.image)
        for x in range(0,self.width,128):
            draw.line((x,0,x,self.height),fill=(120,110,90),width=2)
        for y in range(0,self.height,128):
            draw.line((0,y,self.width,y),fill=(120,110,90),width=2)
        draw.text((40,30),"SYNTHETIC TEST IMAGE - NOT AN ARCHIVAL RECORD",fill=(20,20,20))
        draw.text((40,170),"This exercises image delivery, tiles and crops. No handwriting was read.",fill=(20,20,20))
        draw.rectangle((510,385,690,490),fill=(110,145,180))
        self._levels={}

    @property
    def transport(self): return httpx.MockTransport(self.handle)

    def level_size(self,level):
        factor=2**(math.ceil(math.log2(max(self.width,self.height)))-level)
        return math.ceil(self.width/factor),math.ceil(self.height/factor)

    def handle(self,request):
        self.requests.append(str(request.url))
        if request.url.host in (VCA_HOST,VCA_IMAGES): return handle_vca(request,self.posts,self.image)
        if request.url.host==PF_HOST: return handle_pf(request,self.image,self.pf_crawl_delay)
        if request.url.host==SOAPRAHA_HOST: return self.soapraha.handle(request)
        if request.url.host==TREBON_HOST: return self.trebon.handle(request)
        if request.url.host==PRIHLASKY_HOST: return self.prihlasky.handle(request)
        path=request.url.path
        query=parse_qs(request.url.query.decode())
        if path.startswith("/aron/"): return handle_aron(request,self.posts)
        if path.startswith("/scitacioperaty/"): return handle_census(request,self.census_sessions)
        if path.startswith("/indikacniskici/"): return self.sketches.handle(request)
        if path=="/robots.txt": return httpx.Response(200,text="User-agent: *\nAllow: /\n",headers={"content-type":"text/plain"})
        if path.endswith("autocomplete_obec"):
            labels=[("900001","Demo Lhota (Demo Dorf), obec: Testov, okres: TEST"),
                    ("900002","Demovice, obec: Testov, okres: TEST"),
                    ("900003","Demo Ves, obec: Testov, okres: TEST")]
            term=normalize(query.get("term",[""])[0])
            data=[{"id":i,"value":s,"label":s} for i,s in labels if term in normalize(s)]
            return httpx.Response(200,json=data)
        if path.endswith("/matrika/hledani"):
            return httpx.Response(200,text='''<!doctype html><html lang="cs"><h1>SYNTHETIC FIXTURE</h1>
            <table><thead><tr><th>Číslo knihy</th><th>Původce</th><th>Narození od-do<br>index narození</th>
            <th>Oddaní od-do<br>index oddaní</th><th>Zemřelí od-do<br>index zemřelí</th><th>Obce</th><th>Počet snímků</th><th></th></tr></thead>
            <tbody><tr><td>DEMO-1</td><td>Testov<br><i>syntetický původce</i></td><td>1840 - 1850<br><i>1840 - 1850</i></td>
            <td>1850 - 1860<br><i>-</i></td><td>1842 - 1848<br><i>-</i></td><td>3</td><td>2</td>
            <td><a href="/actapublica/matrika/detail/900001">Zobrazit</a></td></tr>
            <tr><td>DEMO-2</td><td>Testov</td><td>1900 - 1910<br><i>-</i></td><td>-<br><i>-</i></td><td>-<br><i>-</i></td><td>1</td><td>2</td>
            <td><a href="/actapublica/matrika/detail/900002">Zobrazit</a></td></tr></tbody></table></html>''',headers={"content-type":"text/html; charset=utf-8"})
        if "/matrika/detail/" in path:
            sources=[f"https://iipserver.mza.cz/iipsrv/iipsrv.fcgi?Deepzoom=/srv/DEMO/page-{i}.jp2.dzi" for i in (1,2)]
            names=[f"page-{i}.jp2" for i in (1,2)]
            return httpx.Response(200,text="<script>CreateSeadragon("+json.dumps(sources)+","+json.dumps(names)+");</script>",headers={"content-type":"text/html"})
        iiif=query.get("IIIF",[""])[0]
        if iiif.endswith("/info.json"):
            return httpx.Response(200,json={"@context":"http://iiif.io/api/image/2/context.json",
                "width":self.width,"height":self.height,"profile":["http://iiif.io/api/image/2/level1",{"maxWidth":2000,"maxHeight":2000}]},
                headers={"content-type":"application/json+ld"})  # as MZA iipsrv sends it
        m=re.search(r"/(\d+),(\d+),(\d+),(\d+)/(\d+),(\d+)/0/default\.jpg$",iiif)
        if m:
            x,y,w,h,tw,th=map(int,m.groups())
            if w<=0 or h<=0 or x+w>self.width or y+h>self.height: return httpx.Response(400)
            out=io.BytesIO(); self.image.crop((x,y,x+w,y+h)).resize((tw,th),Image.Resampling.LANCZOS).save(out,format="JPEG",quality=95)
            return httpx.Response(200,content=out.getvalue(),headers={"content-type":"image/jpeg"})
        value=query.get("Deepzoom",[""])[0]
        if value.endswith(".dzi"):
            xml=f'<Image xmlns="http://schemas.microsoft.com/deepzoom/2008" TileSize="{self.tile_size}" Overlap="{self.overlap}" Format="png"><Size Width="{self.width}" Height="{self.height}"/></Image>'
            return httpx.Response(200,text=xml,headers={"content-type":"application/xml"})
        m=re.search(r"_files/(\d+)/(\d+)_(\d+)\.png$",value)
        if m:
            level,col,row=map(int,m.groups())
            size=self.level_size(level)
            image=self._levels.setdefault(level,self.image.resize(size,Image.Resampling.LANCZOS))
            ts,ov=self.tile_size,self.overlap
            box=(max(0,col*ts-ov),max(0,row*ts-ov),min(size[0],(col+1)*ts+ov),min(size[1],(row+1)*ts+ov))
            if box[0]>=box[2] or box[1]>=box[3]: return httpx.Response(404)
            out=io.BytesIO(); image.crop(box).save(out,format="PNG")
            return httpx.Response(200,content=out.getvalue(),headers={"content-type":"image/png"})
        return httpx.Response(404,text="Synthetic fixture has no such endpoint")


async def run_demo(service,owner="local"):
    """Deterministic plumbing exercise. It does not simulate a model finding a person."""
    trace=[]
    async def call(name,**args):
        out=await service.call(name,owner,args)
        trace.append({"tool":name,"ok":not out.is_error})
        if out.is_error: raise RuntimeError(out.data)
        return out
    created=await call("start_research",query={"raw_query":"SYNTETICKÝ TECHNICKÝ TEST, nikoli genealogický nález.",
                "place_hint":"Demo Lhota","event_type":"birth","year_from":1840,"year_to":1845})
    sid=created.data["research_id"]
    places=await call("find_place",research_id=sid,term="Demo Lhota",kind="parish_registers",archives=["mza"])
    books=await call("list_sources",research_id=sid,place_ids=[places.data["places"][0]["source_id"]],kind="parish_registers")
    opened=await call("open_source",research_id=sid,source_id=books.data["sources"][0]["source_id"])
    image=await call("view_scan",research_id=sid,page_id=opened.data["pages"][0]["page_id"])
    ref=image.data["image"]
    crop=await call("zoom_scan",research_id=sid,image_id=ref["image_id"],
            bbox_px={"x":ref["width"]*.25,"y":ref["height"]*.25,"width":ref["width"]*.5,"height":ref["height"]*.5})
    await call("record_finding",research_id=sid,idempotency_key="demo-engineering-check",
        observation={"kind":"reviewed_region","image_id":crop.data["image"]["image_id"],
                     "coverage_status":"overview_only","text":"Syntetický technický test: obraz přenesen a vyříznut. Žádný model nečetl rukopis."})
    report=await call("finish_research",research_id=sid,stop_reason="user_stopped")
    return sid,trace,report.data
