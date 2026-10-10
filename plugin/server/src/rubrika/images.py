"""DZI/IIIF region rendering with provenance and exact coordinate transforms."""
from __future__ import annotations

import base64
import dataclasses
import hashlib
import io
import json
import math
import re
import xml.etree.ElementTree as ET
from dataclasses import dataclass
from urllib.parse import parse_qsl, urlencode, urlsplit, urlunsplit

from PIL import Image, UnidentifiedImageError

from .archives import ARCHIVES
from .errors import ResearchError
from .models import BBox, ImageRef, Page, ToolOutput
from .sketches import LEVEL_TOLERANCE, MAX_TILES_PER_CALL as SKETCH_MAX_TILES, SketchGrid, fetch_grid, level_for, region_tiles
from .storage import uid


@dataclass(frozen=True)
class DZI:
    url: str
    width: int
    height: int
    tile_size: int
    overlap: int
    format: str

    @property
    def max_level(self): return math.ceil(math.log2(max(self.width,self.height)))

    def level_sizes(self,level):
        """Candidate level sizes: Deep Zoom specifies ceil, iipsrv (MZA) halves with floor per step."""
        factor=2**(self.max_level-level)
        w,h=self.width,self.height
        for _ in range(self.max_level-level): w,h=max(1,w//2),max(1,h//2)
        return list(dict.fromkeys([(math.ceil(self.width/factor),math.ceil(self.height/factor)),(w,h)]))

    def tile_url(self,level,x,y):
        p=urlsplit(self.url)
        query=parse_qsl(p.query,keep_blank_values=True)
        for i,(key,value) in enumerate(query):
            if key.lower()=="deepzoom":
                if not value.endswith(".dzi"):
                    raise ResearchError("PARSE_ERROR","DZI identifikátor nekončí .dzi.")
                query[i]=(key,value[:-4]+f"_files/{level}/{x}_{y}.{self.format}")
                return urlunsplit((p.scheme,p.netloc,p.path,urlencode(query,safe="/"),""))
        if not p.path.endswith(".dzi"):
            raise ResearchError("PARSE_ERROR","Neznámá struktura URL DZI.")
        return urlunsplit((p.scheme,p.netloc,p.path[:-4]+f"_files/{level}/{x}_{y}.{self.format}",p.query,""))


def parse_dzi(data:bytes,url:str,max_pixels:int) -> DZI:
    if b"<!DOCTYPE" in data.upper() or b"<!ENTITY" in data.upper():
        raise ResearchError("PARSE_ERROR","XML s externími entitami není povoleno.")
    try:
        root=ET.fromstring(data)
        if root.tag.split("}")[-1]!="Image": raise ValueError()
        size=next(n for n in root if n.tag.split("}")[-1]=="Size")
        d=DZI(url=url,width=int(size.attrib["Width"]),height=int(size.attrib["Height"]),
              tile_size=int(root.attrib["TileSize"]),overlap=int(root.attrib.get("Overlap","0")),
              format=root.attrib["Format"].lower())
        if d.width<=0 or d.height<=0 or d.width*d.height>max_pixels or not 16<=d.tile_size<=2048 or not 0<=d.overlap<=16 or d.format not in ("jpg","jpeg","png"):
            raise ValueError()
        return d
    except (ValueError,KeyError,StopIteration,ET.ParseError) as e:
        raise ResearchError("PARSE_ERROR","Neplatný nebo příliš velký DZI descriptor.") from e


@dataclass(frozen=True)
class Zoomify:
    url: str          # ImageProperties.xml
    width: int
    height: int
    tile_size: int
    num_tiles: int
    halving: str = "ceil"    # how the server rounds an odd size when halving: "ceil" (real AHMP and NA) or "floor"

    @property
    def tiers(self) -> list[tuple[int, int]]:
        """Tier sizes, smallest first: full size halved until both sides fit one tile."""
        half = (lambda n: (n + 1) // 2) if self.halving == "ceil" else (lambda n: n // 2)
        sizes = [(self.width, self.height)]
        while sizes[-1][0] > self.tile_size or sizes[-1][1] > self.tile_size:
            sizes.append((half(sizes[-1][0]), half(sizes[-1][1])))
        return sizes[::-1]

    def variants(self) -> list["Zoomify"]:
        """The rounding conventions consistent with NUMTILES, preferred one first. They usually agree except for the
        size of the last row and column of a lower tier, which the first fetched edge tile then settles."""
        out, seen = [], set()
        for halving in dict.fromkeys((self.halving, "ceil", "floor")):
            v = dataclasses.replace(self, halving=halving)
            if v.total_tiles() == self.num_tiles and tuple(v.tiers) not in seen:
                seen.add(tuple(v.tiers))
                out.append(v)
        return out

    def grid(self, tier: int) -> tuple[int, int]:
        w, h = self.tiers[tier]
        return math.ceil(w / self.tile_size), math.ceil(h / self.tile_size)

    def total_tiles(self) -> int:
        return sum(math.ceil(w / self.tile_size) * math.ceil(h / self.tile_size) for w, h in self.tiers)

    def tile_url(self, tier: int, x: int, y: int) -> str:
        """`<root>/TileGroup<g>/<tier>-<x>-<y>.jpg`; tiles are numbered tier by tier, 256 to a group."""
        before = sum(math.ceil(w / self.tile_size) * math.ceil(h / self.tile_size) for w, h in self.tiers[:tier])
        group = (before + y * self.grid(tier)[0] + x) // 256
        return f"{self.url.removesuffix('ImageProperties.xml')}TileGroup{group}/{tier}-{x}-{y}.jpg"

    def pick_tier(self, region, side: int, tolerance: float = 1.0) -> int:
        """Smallest tier whose scale still covers the requested downscale ratio; with tolerance < 1 a tier that gives at
        least that share of `side` is enough (a slightly smaller overview for a quarter of the tiles)."""
        ratio = min(1.0, side / max(region[2], region[3]))
        return next(i for i, (w, _) in enumerate(self.tiers) if w / self.width >= ratio * tolerance - 1e-9)


OVERVIEW_TIER_TOLERANCE = 0.85


def parse_zoomify(data: bytes, url: str, max_pixels: int) -> Zoomify:
    if b"<!DOCTYPE" in data.upper() or b"<!ENTITY" in data.upper():
        raise ResearchError("PARSE_ERROR", "XML s externími entitami není povoleno.")
    try:
        root = ET.fromstring(data)
        if root.tag != "IMAGE_PROPERTIES" or not url.endswith("/ImageProperties.xml"): raise ValueError()
        z = Zoomify(url=url, width=int(root.attrib["WIDTH"]), height=int(root.attrib["HEIGHT"]),
                    tile_size=int(root.attrib["TILESIZE"]), num_tiles=int(root.attrib["NUMTILES"]))
        if (z.width <= 0 or z.height <= 0 or z.width * z.height > max_pixels or not 16 <= z.tile_size <= 2048
                or root.attrib.get("NUMIMAGES", "1") != "1"):
            raise ValueError()
        candidates = z.variants()
        if not candidates:          # NUMTILES fits neither rounding convention
            raise ValueError()
        return candidates[0]
    except (ValueError, KeyError, ET.ParseError) as e:
        raise ResearchError("PARSE_ERROR", "Neplatný nebo příliš velký Zoomify descriptor.") from e


def decode_image(data:bytes,max_pixels:int) -> Image.Image:
    try:
        image=Image.open(io.BytesIO(data))
        if image.format not in ("JPEG","PNG","WEBP") or image.width*image.height>max_pixels:
            raise ValueError()
        image.load()  # Truncated images are NOT accepted.
        return image.convert("RGB")
    except (ValueError,OSError,UnidentifiedImageError,Image.DecompressionBombError) as e:
        raise ResearchError("INCOMPLETE_IMAGE","Obraz nelze bezpečně dekódovat jako úplný snímek.") from e


def source_bbox(ref:ImageRef,bbox:BBox) -> tuple[int,int,int,int]:
    if bbox.x+bbox.width>ref.width+1e-6 or bbox.y+bbox.height>ref.height+1e-6:
        raise ResearchError("INVALID_REGION","Výřez přesahuje konkrétní dodaný obrázek.",width=ref.width,height=ref.height)
    x,y,w,h=ref.source_bbox
    left=max(x,math.floor(x+bbox.x*w/ref.width))
    top=max(y,math.floor(y+bbox.y*h/ref.height))
    right=min(x+w,math.ceil(x+(bbox.x+bbox.width)*w/ref.width))
    bottom=min(y+h,math.ceil(y+(bbox.y+bbox.height)*h/ref.height))
    return left,top,right-left,bottom-top


class _TileSizeMismatch(Exception):
    """A fetched tile does not have the size one rounding convention predicts; the next convention may fit."""
    def __init__(self,url:str,error:ResearchError):
        super().__init__(url)
        self.url,self.error=url,error


class ImageService:
    def __init__(self,source,store,cfg):
        self.source,self.store,self.cfg=source,store,cfg
        # archive code → async (sid, owner, page) -> (JPEG bytes, file name): fetches a whole-scan JPEG into the user's cache
        self.scan_fetchers={}

    async def _jpeg(self,sid,owner,page) -> Image.Image:
        """A whole-scan JPEG (source_type "jpeg"): decoded from the user's cache under the page's source address, else fetched
        once by the archive's own fetcher (which needs the archive's session). A damaged cache entry is fetched again once."""
        fetch=self.scan_fetchers.get(page.archive)
        if fetch is None:
            raise ResearchError("UNSUPPORTED_SOURCE","Pro tento archiv Rubrika celé snímky nestahuje.")
        for attempt in (0,1):
            raw=self.source.cache_get(page.source_url,self.cfg.metadata_ttl)
            if raw is None:
                self.store.check_book_image(page.book_id)     # the per-book cap is checked before a download, not after
                raw,_=await fetch(sid,owner,page)
            try:
                return decode_image(raw,self.cfg.max_source_pixels)
            except ResearchError:
                self.source.invalidate(page.source_url)
                if attempt: raise

    def _user_scan(self,sid,page) -> Image.Image:
        """A scan the user imported (source_type "user_supplied"): the normalized copy in the research's private folder."""
        path=self.cfg.data_dir/"private"/sid/page.image_key
        try:
            return decode_image(path.read_bytes(),self.cfg.max_source_pixels)
        except (OSError,ResearchError) as e:
            raise ResearchError("INCOMPLETE_IMAGE","Uložená kopie importovaného snímku chybí nebo je poškozená; importujte ho znovu.") from e

    async def descriptor(self,sid,owner,page):
        if page.source_type=="manual":
            raise ResearchError("MANUAL_SOURCE","Snímek tohoto archivu Rubrika nestahuje; načtěte ho z prohlížeče nástrojem import_scan.",viewer_url=page.viewer_url)
        if page.source_type=="user_supplied":
            image=self._user_scan(sid,page)
            return image,image.width,image.height
        if page.source_type=="jpeg":
            image=await self._jpeg(sid,owner,page)
            return image,image.width,image.height
        if page.source_type=="sketch":
            grid=await fetch_grid(self.source,sid,owner,page)
            return grid,grid.width,grid.height
        if page.source_type=="zoomify":
            raw=await self.source.fetch(sid,owner,page.source_url,kind="xml")
            z=parse_zoomify(raw,page.source_url,self.cfg.max_source_pixels)
            return z,z.width,z.height
        if page.source_type=="dzi":
            raw=await self.source.fetch(sid,owner,page.source_url,kind="xml")
            d=parse_dzi(raw,page.source_url,self.cfg.max_source_pixels)
            return d,d.width,d.height
        raw=await self.source.fetch(sid,owner,page.source_url,kind="json")
        try:
            d=json.loads(raw)
            w,h=int(d["width"]),int(d["height"])
            if w<=0 or h<=0 or w*h>self.cfg.max_source_pixels: raise ValueError()
            # Use the descriptor URL actually supplied by the source; do not change protocol.
            if not page.source_url.endswith("/info.json"): raise ValueError()
            return d,w,h
        except (ValueError,TypeError,KeyError) as e:
            raise ResearchError("PARSE_ERROR","Neplatné metadata IIIF.") from e

    async def _tile(self,sid,owner,url,**where):
        try:
            raw=await self.source.fetch(sid,owner,url,kind="image")
            return decode_image(raw,self.cfg.max_source_pixels)
        except ResearchError as e:
            if e.code in ("SOURCE_UNAVAILABLE","INCOMPLETE_IMAGE"):
                self.source.invalidate(url)
                raise ResearchError("INCOMPLETE_IMAGE","Chybí potřebná dlaždice; stránka není přečtená.",**where) from e
            raise

    async def render_dzi(self,sid,owner,d:DZI,bbox,side):
        x,y,w,h=bbox
        ratio=min(1.0,side/max(w,h))
        level=max(0,min(d.max_level,d.max_level+math.ceil(math.log2(ratio))))
        factor=2**(d.max_level-level)
        sizes=d.level_sizes(level)
        left,top=math.floor(x/factor),math.floor(y/factor)
        right,bottom=math.ceil((x+w)/factor),math.ceil((y+h)/factor)
        ts=d.tile_size
        xs=range(left//ts,(right-1)//ts+1)
        ys=range(top//ts,(bottom-1)//ts+1)
        if len(xs)*len(ys)>self.cfg.max_tiles_per_call:
            raise ResearchError("REGION_TOO_LARGE","Výřez vyžaduje příliš mnoho dlaždic; rozdělte jej.",tiles=len(xs)*len(ys))
        canvas=Image.new("RGB",(right-left,bottom-top))
        urls=[]
        for row in ys:
            for col in xs:
                url=d.tile_url(level,col,row)
                urls.append(url)
                tile=await self._tile(sid,owner,url,level=level,column=col,row=row)
                tl=max(0,col*ts-d.overlap); tt=max(0,row*ts-d.overlap)
                def extent(size): return min(size[0],(col+1)*ts+d.overlap)-tl,min(size[1],(row+1)*ts+d.overlap)-tt
                # Edge tiles reveal which level-size convention the server uses; keep only matching ones.
                fitting=[size for size in sizes if tile.size==extent(size)]
                if not fitting:
                    self.source.invalidate(url)
                    raise ResearchError("INCOMPLETE_IMAGE","Dlaždice má jiné než očekávané rozměry.",expected=list(extent(sizes[0])),actual=list(tile.size))
                sizes=fitting
                tr,tb=tl+tile.width,tt+tile.height
                a,b,c,e=max(left,tl),max(top,tt),min(right,tr),min(bottom,tb)
                canvas.paste(tile.crop((a-tl,b-tt,c-tl,e-tt)),(a-left,b-top))
        target=(max(1,round(w*ratio)),max(1,round(h*ratio)))
        lw,lh=sizes[0]
        # Float box compensates for non-integer boundaries at downsampled DZI levels;
        # clamping keeps a floor-halved level's missing last pixel out of the result.
        return canvas.resize(target,Image.Resampling.LANCZOS,
                             box=(x/factor-left,y/factor-top,min((x+w)/factor,lw)-left,min((y+h)/factor,lh)-top)),urls

    async def render_zoomify(self,sid,owner,z:Zoomify,bbox,side,tier=None):
        """Tiles of one tier, cropped and scaled. The server's rounding of odd tier sizes is not announced: each
        convention consistent with NUMTILES is tried in turn and the first whose expected edge tile sizes match wins;
        a tile of any other size is refused. `tier` counts from the smallest tier of `z`."""
        from_top=None if tier is None else len(z.tiers)-1-tier
        refused=None
        for variant in z.variants():
            try:
                return await self._render_zoomify(sid,owner,variant,bbox,side,
                    None if from_top is None else len(variant.tiers)-1-from_top)
            except _TileSizeMismatch as mismatch:
                refused=refused or mismatch
        self.source.invalidate(refused.url)
        raise refused.error

    async def _render_zoomify(self,sid,owner,z:Zoomify,bbox,side,tier):
        x,y,w,h=bbox
        ratio=min(1.0,side/max(w,h))
        tier=z.pick_tier(bbox,side) if tier is None else tier
        if not 0<=tier<len(z.tiers): raise _TileSizeMismatch("",ResearchError("INCOMPLETE_IMAGE","Zoomify nemá požadovanou úroveň."))
        tw,th=z.tiers[tier]
        fx,fy=tw/z.width,th/z.height
        left,top=min(tw-1,math.floor(x*fx)),min(th-1,math.floor(y*fy))
        right,bottom=min(tw,max(left+1,math.ceil((x+w)*fx))),min(th,max(top+1,math.ceil((y+h)*fy)))
        ts=z.tile_size
        xs=range(left//ts,(right-1)//ts+1)
        ys=range(top//ts,(bottom-1)//ts+1)
        if len(xs)*len(ys)>self.cfg.max_tiles_per_call:
            raise ResearchError("REGION_TOO_LARGE","Výřez vyžaduje příliš mnoho dlaždic; rozdělte jej.",tiles=len(xs)*len(ys))
        canvas=Image.new("RGB",(right-left,bottom-top))
        urls=[]
        for row in ys:
            for col in xs:
                url=z.tile_url(tier,col,row)
                urls.append(url)
                try:
                    tile=await self._tile(sid,owner,url,level=tier,column=col,row=row)
                except ResearchError as e:
                    # A missing tile may only mean the server rounds odd tier sizes the other way (other grid).
                    if e.code=="INCOMPLETE_IMAGE": raise _TileSizeMismatch(url,e) from e
                    raise
                expected=(min(tw,(col+1)*ts)-col*ts,min(th,(row+1)*ts)-row*ts)
                if tile.size!=expected:
                    raise _TileSizeMismatch(url,ResearchError("INCOMPLETE_IMAGE","Dlaždice má jiné než očekávané rozměry.",
                                                              expected=list(expected),actual=list(tile.size)))
                a,b,c,e=max(left,col*ts),max(top,row*ts),min(right,col*ts+tile.width),min(bottom,row*ts+tile.height)
                canvas.paste(tile.crop((a-col*ts,b-row*ts,c-col*ts,e-row*ts)),(a-left,b-top))
        target=(max(1,round(w*ratio)),max(1,round(h*ratio)))
        return canvas.resize(target,Image.Resampling.LANCZOS,
                             box=(x*fx-left,y*fy-top,min((x+w)*fx,tw)-left,min((y+h)*fy,th)-top)),urls

    async def render_sketch(self,sid,owner,g:SketchGrid,bbox,side):
        """Tiles of one level of a sketch's fixed pyramid (every tile the same size), cropped and scaled. Areas without a tile
        (the sketch is not a rectangle) stay white. Every tile is a request, so the level steps down to a coarser one until the
        region fits the tile limit; a whole-sheet region that still does not fit (or that the single-file thumbnail `map.jpg`
        of the front side already serves at the requested size) is taken from that thumbnail. A smaller region is asked for otherwise.
        An image is never upscaled: when the level or the thumbnail is coarser than requested it comes back at its native size,
        and when that is below the requested resolution (by more than the level tolerance) `low_resolution` is set.
        Returns (image, urls, {"effective_scale", "low_resolution"})."""
        x,y,w,h=bbox
        ratio=min(1.0,side/max(w,h))
        limit=min(self.cfg.max_tiles_per_call,SKETCH_MAX_TILES)
        whole=(x,y,w,h)==(0,0,g.width,g.height)
        can_map=whole and g.side=="A"          # no separate back-side thumbnail is known
        mw,mh=g.map_size
        if can_map and max(mw,mh)>=ratio*max(w,h)*LEVEL_TOLERANCE-1e-9:
            got=await self._sketch_map(sid,owner,g,ratio)
            if got: return got
        level=level_for(ratio)
        while True:
            (left,top,right,bottom),xs,ys=region_tiles(g,level,bbox)
            if len(xs)*len(ys)<=limit and (right-left)*(bottom-top)<=self.cfg.max_source_pixels: break
            if level==1:
                got=await self._sketch_map(sid,owner,g,ratio) if can_map else None
                if got: return got
                raise ResearchError("REGION_TOO_LARGE","Výřez vyžaduje příliš mnoho dlaždic i na nejhrubší úrovni; požádejte o menší oblast.",
                                    tiles=len(xs)*len(ys),limit=limit)
            level-=1
        s=g.scale(level)
        canvas=Image.new("RGB",(right-left,bottom-top),(255,255,255))
        urls=[]
        for row in ys:
            for col in xs:
                url=g.tile_url(level,col,row)
                if url is None: continue
                urls.append(url)
                tile=await self._tile(sid,owner,url,level=level,column=col,row=row)
                if tile.size!=(g.tile_w,g.tile_h):
                    self.source.invalidate(url)
                    raise ResearchError("INCOMPLETE_IMAGE","Dlaždice má jiné než očekávané rozměry.",expected=[g.tile_w,g.tile_h],actual=list(tile.size))
                a,b,c,e=max(left,col*g.tile_w),max(top,row*g.tile_h),min(right,(col+1)*g.tile_w),min(bottom,(row+1)*g.tile_h)
                canvas.paste(tile.crop((a-col*g.tile_w,b-row*g.tile_h,c-col*g.tile_w,e-row*g.tile_h)),(a-left,b-top))
        out=min(ratio,s)                      # never above the level's own resolution
        target=(max(1,round(w*out)),max(1,round(h*out)))
        image=canvas.resize(target,Image.Resampling.LANCZOS,
                            box=(x*s-left,y*s-top,min((x+w)*s,right)-left,min((y+h)*s,bottom)-top))
        return image,urls,{"effective_scale":out,"low_resolution":out<ratio*LEVEL_TOLERANCE-1e-9}

    async def _sketch_map(self,sid,owner,g:SketchGrid,ratio):
        """The whole front sheet from the archive's single-file thumbnail (one request), at its native decoded size (not upscaled).
        None when the thumbnail cannot be used, so the caller goes on with tiles."""
        try:
            thumb=await self._tile(sid,owner,g.map_url,level=0,column=0,row=0)
        except ResearchError as e:
            if e.code!="INCOMPLETE_IMAGE": raise
            return None
        if thumb.width*thumb.height>self.cfg.max_source_pixels or thumb.width<1:
            self.source.invalidate(g.map_url)
            return None
        scale=thumb.width/g.width
        target=(round(g.width*ratio),round(g.height*ratio))
        if scale>ratio:                        # a thumbnail finer than requested (large grids): scale down
            thumb=thumb.resize((max(1,target[0]),max(1,target[1])),Image.Resampling.LANCZOS)
            scale=ratio
        return thumb,[g.map_url],{"effective_scale":scale,"low_resolution":scale<ratio*LEVEL_TOLERANCE-1e-9}

    @staticmethod
    def render_jpeg(image:Image.Image,bbox,side):
        """Crop and scale a whole decoded scan; the region is in the pixels of the scan itself, so no rounding is involved."""
        x,y,w,h=bbox
        ratio=min(1.0,side/max(w,h))
        crop=image.crop((x,y,x+w,y+h))
        target=(max(1,round(w*ratio)),max(1,round(h*ratio)))
        return (crop if target==crop.size else crop.resize(target,Image.Resampling.LANCZOS)),[]

    async def render_iiif(self,sid,owner,page,descriptor,bbox,side):
        x,y,w,h=bbox
        ratio=min(1,side/max(w,h))
        tw,th=max(1,round(w*ratio)),max(1,round(h*ratio))
        if (descriptor.get("maxWidth") and tw>descriptor["maxWidth"]) or (descriptor.get("maxHeight") and th>descriptor["maxHeight"]) or (descriptor.get("maxArea") and tw*th>descriptor["maxArea"]):
            raise ResearchError("REGION_TOO_LARGE","Výřez přesahuje limity IIIF služby.")
        base=page.source_url[:-len("/info.json")]
        url=f"{base}/{x},{y},{w},{h}/{tw},{th}/0/default.jpg"
        try:
            raw=await self.source.fetch(sid,owner,url,kind="image")
            image=decode_image(raw,self.cfg.max_source_pixels)
        except ResearchError as e:
            if e.code in ("SOURCE_UNAVAILABLE","INCOMPLETE_IMAGE"):
                self.source.invalidate(url)
                raise ResearchError("INCOMPLETE_IMAGE","Výřez se nepodařilo stáhnout celý; stránka není přečtená.") from e
            raise
        if image.size!=(tw,th):
            self.source.invalidate(url)
            raise ResearchError("INCOMPLETE_IMAGE","IIIF vrátil jiné než požadované rozměry.")
        return image,[url]

    async def get(self,sid,owner,page:Page,region=None,role="overview") -> ToolOutput:
        reserved=[]      # full-resolution pixels held for this call; returned when the render does not complete
        try:
            return await self._get(sid,owner,page,region,role,reserved)
        except BaseException:
            for pixels in reserved: self.store.release_full_res(page.page_id,pixels)
            raise

    async def _get(self,sid,owner,page:Page,region,role,reserved) -> ToolOutput:
        self.store.search(sid,owner)
        descriptor,width,height=await self.descriptor(sid,owner,page)
        region=region or (0,0,width,height)
        x,y,w,h=region
        if x<0 or y<0 or w<=0 or h<=0 or x+w>width or y+h>height:
            raise ResearchError("INVALID_REGION","Neplatná oblast zdrojového snímku.")
        local=page.source_type=="user_supplied"     # imported by the user: no archive, so no per-book network-side cap either
        if not local: self.store.check_book_image(page.book_id)   # charged only after a successful render
        side=self.cfg.preview_side if role=="overview" else self.cfg.crop_side
        full_res=0
        sketch_meta=None
        if isinstance(descriptor,Zoomify):
            # Zoomify archives are paced per tile: an overview may come from the tier just below the preview size.
            tier=descriptor.pick_tier(region,side,OVERVIEW_TIER_TOLERANCE if role=="overview" else 1.0)
            archive=ARCHIVES.get(page.archive)
            if role=="detail" and archive and archive.full_res_captcha and tier==len(descriptor.tiers)-1:
                # Area rendered from the top tier: the app hands out the whole scan at full resolution only behind a captcha.
                full_res=w*h
                self.store.reserve_full_res(page.page_id,full_res,int(self.cfg.full_res_page_share*width*height))
                reserved.append(full_res)
            image,urls=await self.render_zoomify(sid,owner,descriptor,region,side,tier)
        elif isinstance(descriptor,DZI):
            image,urls=await self.render_dzi(sid,owner,descriptor,region,side)
        elif isinstance(descriptor,SketchGrid):
            image,urls,sketch_meta=await self.render_sketch(sid,owner,descriptor,region,side)
        elif isinstance(descriptor,Image.Image):
            image,urls=self.render_jpeg(descriptor,region,side)
        else:
            image,urls=await self.render_iiif(sid,owner,page,descriptor,region,side)
        robots_disallowed=not local and any(self.source.robots_disallowed(u) for u in [page.source_url,*urls])
        if not local: self.store.link(sid,owner,"downloaded_page",page.page_id)
        iid=uid("image")
        for _ in range(5):
            buf=io.BytesIO()
            image.save(buf,format="JPEG",quality=85,subsampling=0,optimize=True)
            raw=buf.getvalue()
            ref=ImageRef(image_id=iid,page_id=page.page_id,book_id=page.book_id,
                source_width=width,source_height=height,source_bbox=region,width=image.width,height=image.height,
                role=role,sha256=hashlib.sha256(raw).hexdigest(),viewer_url=page.viewer_url,
                manifest_sha256=page.manifest_sha256,scan_number=page.scan_number,
                viewer_link_verified=page.viewer_link_verified,
                source=page.source,citation=page.citation,image_key=page.image_key,robots_disallowed=robots_disallowed,archive=page.archive,
                provenance=page.provenance,scan_label=page.scan_label,
                **({"effective_scale":round(sketch_meta["effective_scale"],4),"low_resolution":sketch_meta["low_resolution"]} if sketch_meta else {}))
            result=ToolOutput(data={"ok":True,"source_mode":self.source.mode,"image":ref.model_dump(),
                "instruction":"Souřadnice výřezu zadávejte v pixelech tohoto obrázku. Náhled není důkazem prohledání.",
                "provenance":"Source pixels only; JPEG compression and resizing, no generated text."},
                image_b64=base64.b64encode(raw).decode(),mime_type="image/jpeg")
            if sketch_meta and sketch_meta["low_resolution"]:
                result.data["low_resolution"]=True
                result.data["warnings"]=["Náhled v nízkém rozlišení (nezvětšeno); pro čtení zvětšete menší oblast."]
            if len(json.dumps(result.wire(),ensure_ascii=True)) <= self.cfg.payload_max_chars-2000:
                if not local: self.store.charge_book_image(owner,page.book_id)
                self.store.save_image(sid,owner,ref.model_dump(),raw,archive_image=not local)
                reserved.clear()
                return result
            if role=="detail":
                break
            if max(image.size)<=300: break
            image.thumbnail((max(256,int(image.width*.75)),max(256,int(image.height*.75))),Image.Resampling.LANCZOS)
        raise ResearchError("PAYLOAD_TOO_LARGE","Obraz se nevejde do MCP výsledku. Požádejte o menší výřez; detail nebyl tiše zmenšen.",
                            max_chars=self.cfg.payload_max_chars,source_bbox=region)
