"""AHMP routes for the Soupis and census lists: the observed GET jump (rowTxt, _sourcePage, __fp) and the census image folder."""
import pytest

from rubrika import archives as A
from rubrika.errors import ResearchError
from rubrika.network import SourcePolicy

BASE = "https://katalog.ahmp.cz/pragapublica/"
TOKEN = "SyntheticSourcePage_" * 5  # synthetic; never reuse an archive session token
FP = "SyntheticFormToken_" * 4  # synthetic; never reuse an archive session token
JUMP = BASE + f"PaginatorNavig.action?rowTxt=60000&_sourcePage={TOKEN}&__fp={FP}"
ROOT = "https://images.ahmp.cz/mrimage/ahmp/zoomify/cz/archives/CZ-321100010/"
HASH = "aa99f9da870bcdde3847bf42d8a40750"


def route(url, method="GET"):
    return SourcePolicy(A.integrated_hosts()).validate(url, method)


def test_jump_by_row_number_is_allowed():
    r = route(JUMP)
    assert (r.archive, r.group) == ("ahmp", "ahmp_catalog")
    assert route(BASE + f"PaginatorNavig.action?rowTxt=1&_sourcePage={TOKEN}&__fp={FP}%3D")


@pytest.mark.parametrize("url", [
    BASE + f"PaginatorNavig.action?rowTxt=abc&_sourcePage={TOKEN}&__fp={FP}",
    BASE + f"PaginatorNavig.action?rowTxt=-1&_sourcePage={TOKEN}&__fp={FP}",
    BASE + f"PaginatorNavig.action?rowTxt=1234567&_sourcePage={TOKEN}&__fp={FP}",
    BASE + f"PaginatorNavig.action?rowTxt=5&_sourcePage={TOKEN}",
    BASE + f"PaginatorNavig.action?rowTxt=5&__fp={FP}",
    BASE + f"PaginatorNavig.action?rowTxt=5&_sourcePage={TOKEN}&__fp={FP}&x=1",
    BASE + f"PaginatorNavig.action?rowTxt=5&rowTxt=6&_sourcePage={TOKEN}&__fp={FP}",
    BASE + f"PaginatorNavig.action?rowTxt=5&_sourcePage=a%20b&__fp={FP}",
    BASE + f"PaginatorNavig.action?rowTxt=5&_sourcePage={TOKEN}&__fp=x",
    BASE + f"PaginatorNavig.action?rowTxt=5&row=5&_sourcePage={TOKEN}&__fp={FP}",
    BASE + f"PaginatorMedia.action?rowTxt=5&_sourcePage={TOKEN}&__fp={FP}",
    BASE + f"SearchBean.action?rowTxt=5&_sourcePage={TOKEN}&__fp={FP}",
])
def test_malformed_or_foreign_jumps_are_refused(url):
    with pytest.raises(ResearchError) as e:
        route(url)
    assert e.value.code == "INVALID_SOURCE"


@pytest.mark.parametrize("method", ["POST", "PUT", "DELETE"])
def test_jump_is_read_only(method):
    with pytest.raises(ResearchError):
        route(JUMP, method)


def test_census_images_live_in_their_own_folder():
    assert route(f"{ROOT}spisy/dao/images/0055/{HASH}.jpg/ImageProperties.xml").group == "ahmp_images"
    assert route(f"{ROOT}spisy/dao/images/0055/{HASH}.jpg/TileGroup0/3-1-2.jpg").group == "ahmp_images"
    assert route(f"{ROOT}NAD-4/dao/images/0226/{HASH}.jpg/ImageProperties.xml")          # unchanged


@pytest.mark.parametrize("folder", ["admin", "spisy/../x", "SPISY", "spisy2", "..", ""])
def test_other_image_folders_are_refused(folder):
    with pytest.raises(ResearchError):
        route(f"{ROOT}{folder}/dao/images/0055/{HASH}.jpg/ImageProperties.xml")
