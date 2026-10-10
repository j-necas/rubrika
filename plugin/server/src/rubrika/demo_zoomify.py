"""Synthetic Zoomify image server for tests and demos (AHMP and Národní archiv URL shapes). Not shipped in the desktop bundle."""
from __future__ import annotations

import io
import math
import re

import httpx
from PIL import Image, ImageDraw

TILE = 256
AHMP_HOST = "images.ahmp.cz"
NA_HOST = "vademecum.nacr.cz"
AHMP_ROOT = ("https://images.ahmp.cz/mrimage/ahmp/zoomify/cz/archives/CZ-321100010/NAD-156/dao/images/0014/"
             "4cd4d3321eecc71459e876d6582dda29.jpg")
NA_ROOT = ("https://vademecum.nacr.cz/mrimage/vademecum/zoomify//cz/archives/CZ-100000010/inventare/dao/images/0245/"
           "ca182acf-5e28-4abf-ab4e-ad7b06ec4e44.jpg")
# Tiles the server pretends not to have: {(tier, x, y)}. Tests add to and clear it.
MISSING_TILES: set[tuple[int, int, int]] = set()


def tier_sizes(width: int, height: int, tile: int = TILE, halving: str = "floor") -> list[tuple[int, int]]:
    """Independent implementation for the tests: full size, halved (rounding down or up, like the real servers do)
    until both sides fit a tile; smallest first."""
    half = (lambda n: (n + 1) // 2) if halving == "ceil" else (lambda n: n // 2)
    sizes = [(width, height)]
    while sizes[-1][0] > tile or sizes[-1][1] > tile:
        sizes.append((half(sizes[-1][0]), half(sizes[-1][1])))
    return sizes[::-1]


def synthetic_scan(width: int, height: int) -> Image.Image:
    """Smooth, position-dependent colours plus sharp marks, so a wrong tile offset shows up in the pixels."""
    gradient = Image.linear_gradient("L")
    red = gradient.rotate(90, expand=True).resize((width, height), Image.Resampling.BILINEAR)
    green = gradient.resize((width, height), Image.Resampling.BILINEAR)
    blue = Image.new("L", (width, height), 90)
    image = Image.merge("RGB", (red, green, blue))
    draw = ImageDraw.Draw(image)
    for i in range(0, width, 173):
        draw.line([(i, 0), (i, height)], fill=(255, 255, 255), width=2)
    for j in range(0, height, 131):
        draw.line([(0, j), (width, j)], fill=(0, 0, 0), width=2)
    draw.rectangle([width - 40, height - 40, width - 1, height - 1], fill=(255, 0, 255))
    return image


class ZoomifyArchive:
    def __init__(self, width=4379, height=3284, *, root=AHMP_ROOT, num_tiles=None, robots="User-agent: *\nDisallow: /\n",
                 halving="floor"):
        self.width, self.height, self.root, self.robots = width, height, root, robots
        self.image = synthetic_scan(width, height)
        self.halving = halving        # "ceil": the real AHMP and Národní archiv servers; "floor": the phase A default
        self.sizes = tier_sizes(width, height, halving=halving)
        self.num_tiles = num_tiles if num_tiles is not None else sum(
            math.ceil(w / TILE) * math.ceil(h / TILE) for w, h in self.sizes)
        self.requests: list[str] = []
        self._tiers: dict[int, Image.Image] = {}
        self.transport = httpx.MockTransport(self.handle)

    @property
    def descriptor_url(self) -> str:
        return self.root + "/ImageProperties.xml"

    def tier(self, z: int) -> Image.Image:
        if z not in self._tiers:
            w, h = self.sizes[z]
            self._tiers[z] = self.image if z == len(self.sizes) - 1 else self.image.resize((w, h), Image.Resampling.BOX)
        return self._tiers[z]

    def group_of(self, z: int, x: int, y: int) -> int:
        before = sum(math.ceil(w / TILE) * math.ceil(h / TILE) for w, h in self.sizes[:z])
        return (before + y * math.ceil(self.sizes[z][0] / TILE) + x) // TILE

    def handle(self, request: httpx.Request) -> httpx.Response:
        self.requests.append(str(request.url))
        path = request.url.path
        if path == "/robots.txt":
            return httpx.Response(200, text=self.robots, headers={"content-type": "text/plain"})
        prefix = "/" + self.root.split("/", 3)[3]
        if request.method != "GET" or not path.startswith(prefix + "/"):
            return httpx.Response(404)
        rest = path[len(prefix) + 1:]
        if rest == "ImageProperties.xml":
            xml = (f"<IMAGE_PROPERTIES WIDTH='{self.width}' HEIGHT='{self.height}' NUMTILES='{self.num_tiles}' "
                   f"NUMIMAGES='1' VERSION='1.8' TILESIZE='{TILE}' DPIX='300' DPIY='300' />")
            return httpx.Response(200, text=xml, headers={"content-type": "text/xml"})
        m = re.fullmatch(r"TileGroup(\d+)/(\d+)-(\d+)-(\d+)\.jpg", rest)
        if not m:
            return httpx.Response(404)
        g, z, x, y = map(int, m.groups())
        if z >= len(self.sizes) or (z, x, y) in MISSING_TILES:
            return httpx.Response(404)
        w, h = self.sizes[z]
        if x * TILE >= w or y * TILE >= h or g != self.group_of(z, x, y):
            return httpx.Response(404)
        out = io.BytesIO()
        self.tier(z).crop((x * TILE, y * TILE, min(w, (x + 1) * TILE), min(h, (y + 1) * TILE))).save(out, "JPEG", quality=95)
        return httpx.Response(200, content=out.getvalue(), headers={"content-type": "image/jpeg"})
