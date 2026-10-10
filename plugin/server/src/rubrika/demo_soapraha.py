"""Entirely synthetic responses shaped like eBadatelna of SOA Praha (Apache Wicket: session cookie, redirects to page
versions, an AJAX search whose answer is XML with an HTML table in CDATA, whole-scan JPEGs bound to the session).
Registers, places and images are invented; NEVER real archive data."""
import html
import io
import json
import re
import unicodedata
import uuid
from urllib.parse import parse_qs, urljoin

import httpx
from PIL import Image, ImageDraw

from .catalog import normalize

ROWS_PER_PAGE = 3                       # the live application shows 50; tests patch this to force paging
HTML = {"content-type": "text/html;charset=UTF-8"}
XML = {"content-type": "text/xml;charset=UTF-8"}
NO_STORE = {"cache-control": "no-cache, no-store"}
SCAN_SIZE = (1600, 1200)
MARKER = (610, 420, 790, 540)           # the blue rectangle every synthetic scan carries (x0, y0, x1, y1)

RC, EV = "Roman Catholic Church", "Evangelical Church"
DASH = "---"
# id, title, originator type, district, localities [(name, variants)], years [B, M, D, index B, index M, index D], scans
BOOKS = [
    (7001, "Demolov 01", RC, "Demolov", [("Demolov", "Demolau"), ("Horní Demolov", "Ober-Demolau")],
     ["1785 - 1830", DASH, DASH, DASH, DASH, DASH], 3),
    (7002, "Demolov 02", RC, "Demolov", [("Demolov", "Demolau"), ("Horní Demolov", "Ober-Demolau")],
     [DASH, "1785 - 1830", DASH, DASH, "1785 - 1830", DASH], 2),
    (7003, "Demolov 03", RC, "Demolov", [("Demolov", "Demolau")], [DASH, DASH, "1785 - 1830", DASH, DASH, DASH], 2),
    (7004, "Demolov 04", RC, "Demolov", [("Demolov", "Demolau"), ("Testov", "Teststadt")],
     ["1850 - 1899", DASH, DASH, DASH, DASH, DASH], 2),
    (7005, "Demolov 05", RC, "Demolov", [("Demolov", "Demolau")], [DASH] * 6, 2),
    (7006, "Demolov 06", RC, "Demolov", [("Demolov", "Demolau")], ["1900 - ?", DASH, DASH, DASH, DASH, DASH], 2),
    (7007, "Index Demolov 01", RC, "Demolov", [("Demolov", "Demolau")], [DASH, DASH, DASH, "1648 - 1733", DASH, DASH], 2),
    (7008, "Testov 01", RC, "Testov", [("Testov", "Teststadt"), ("Malá Ves", "")],
     ["1790 - 1850", "1790 - 1850", "1790 - 1850", DASH, DASH, DASH], 2),
    (7009, "Starynov 01", RC, "Testov", [("Starý Demolovec", "Alt-Demolau")], ["1801 - 1820", DASH, DASH, DASH, DASH, DASH], 2),
    (7010, "Demolov-ev 01", EV, "Demolov", [("Demolov", "Demolau"), ("Testov", "Teststadt"), ("Malá Ves", "")],
     ["1850 - 1870", "1850 - 1870", DASH, DASH, DASH, DASH], 2),
    (7011, "Demolov-ev 02", EV, "Demolov", [("Demolov", "Demolau")], ["1871 - 1900, 1902", DASH, DASH, DASH, DASH, DASH], 2),
]
BY_ID = {b[0]: b for b in BOOKS}
# the locality list of the search page: (community, district, [Czech name, variants...]); the last two share a name with places elsewhere
LOCALITIES = [
    ("Demolov", "Demolov", ["Demolov", "Demolau"]),
    ("Demolov", "Demolov", ["Horní Demolov", "Ober-Demolau"]),
    ("Starynov", "Testov", ["Starý Demolovec", "Alt-Demolau"]),
    ("Testov", "Testov", ["Malá Ves"]),
    ("Testov", "Testov", ["Testov", "Teststadt"]),
    ("Lhotka", "Kolonov", ["Testov"]),
    ("Kolonov", "Kolonov", ["Malá Ves", "Klein Dorf"]),
]
DETAIL_LABELS = ("Original name", "District", "Provenance", "Births / Index", "Marriages / Index", "Deaths / Index",
                 "Physical state", "Completeness", "State of damage", "Register type", "Language", "Binding", "Dimension",
                 "Number of pages", "Number of sheets", "Number of scans", "Place of storage")


def file_stem(title: str) -> str:
    return re.sub(r"[^A-Za-z0-9-]+", "_", normalize(title)).strip("_").capitalize()


def image_name(book_id: int, n: int) -> str:
    return f"{file_stem(BY_ID[book_id][1])}__{n:03d}.jpg"


def scan_image(book_id: int, n: int) -> Image.Image:
    image = Image.new("RGB", SCAN_SIZE, (238 - 9 * n % 40, 230, 205))
    draw = ImageDraw.Draw(image)
    for x in range(0, SCAN_SIZE[0], 200):
        draw.line((x, 0, x, SCAN_SIZE[1]), fill=(150, 140, 120), width=2)
    draw.text((30, 20), f"SYNTHETIC SCAN {book_id}/{n} - NOT AN ARCHIVAL RECORD", fill=(20, 20, 20))
    draw.rectangle(MARKER, fill=(60, 100, 190))
    return image


def _tokens(text: str) -> list[str]:
    return re.findall(r"[a-z0-9]+", normalize(text))


def _matches(book, term_tokens: list[str]) -> bool:
    """A word of a locality starts with every word of the term (accents and case ignored); variants do not count."""
    words = {w for name, _ in book[4] for w in _tokens(name)}
    return bool(term_tokens) and all(any(w.startswith(t) for w in words) for t in term_tokens)


def _cell(text: str) -> str:
    return f"<td>\n<span>{html.escape(text).replace(' ', '&nbsp;')}</span>\n</td>\n"


def _row(index: int, b) -> str:
    bid, title, kind, district, places = b[0], b[1], b[2], b[3], b[4]
    link = f"/pages/MatrikaPage/matrikaId/{bid}"
    return (f'<tr name="dataLink" onclick="renderRowClasses(this.getAttribute(&#039;rowindex&#039;)); openInNewTab(this.getAttribute(&#039;href&#039;));" '
            f'id="id{0x4f + index:x}" href="/pages/DataMatrikaPage/idx/1/matrikaId/{bid}" rowindex="{index}" class="{"even" if index % 2 == 0 else "odd"}">\n\n'
            f'<td>\n<a target="_blank" class="matrikaLink" onclick="renderRowClasses(this.parentNode.parentNode.getAttribute(&#039;rowindex&#039;));" href="{link}">'
            f'{html.escape(title).replace(" ", "&nbsp;")}</a>\n</td>\n'
            + _cell(kind) + _cell(district)
            + f'<td class="hidden">\n<span>{len(places)}</span>\n</td>\n'
            + _cell(", ".join(name for name, _ in places)) + "".join(_cell(y) for y in b[5]) + _cell(str(b[6])) + "\n</tr>\n")


def _pager(name: str, page: int, pages: int) -> str:
    options = "".join('<option ' + ('selected="selected" ' if i == page else '') + f'value="{i}">{i + 1}</option>\n' for i in range(pages))
    return (f'<div id="pagination"><div><table><tr><td><a class="pageNumberCurrent" href="#"><span>1</span></a></td><td>'
            f'<div style="padding-top: 0px; float: left;"><select class="pageDropdown" name="tableContainer:{name}:results:pageDropdown">\n{options}</select></div>'
            f'</td><td><a class="pageNumber" href="#">&nbsp;&gt;&nbsp;</a><a class="pageNumber" href="#"><span>{pages}</span></a></td></tr></table></div></div>')


HEADERS = ("Register title", "Provenance type", "District", "Localities", "Localities", "Births", "Marriages", "Deaths",
           "Index B", "Index M", "Index D", "Scans")


def results_component(hits, page: int) -> str:
    pages = max(1, -(-len(hits) // ROWS_PER_PAGE))
    head = "".join(f'<th class="header{i}{" hidden" if i == 4 else ""}">{h}</th>\n' for i, h in enumerate(HEADERS, 1))
    rows = "".join(_row(i, b) for i, b in enumerate(hits[page * ROWS_PER_PAGE:(page + 1) * ROWS_PER_PAGE]))
    return (f'<div id="id26">\n<div class="right" id="id38">\n{_pager("pagination", page, pages)}\n</div>\n'
            f'<div class="right resultsOnPage" id="id39"><select class="pageDropdown" name="tableContainer:resultsOnPagePanel:resultsOnPage">'
            f'<option value="5">5 results per page</option><option selected="selected" value="{ROWS_PER_PAGE}">{ROWS_PER_PAGE} results per page</option></select></div>\n'
            f'<div class="right resultsFound"><span id="id4e">{len(hits)} registers found</span></div>\n<div class="clear"></div>\n'
            f'<table class="searchResult matrikaResult">\n<thead>\n<tr>\n{head}</tr>\n</thead>\n<tbody>\n{rows}</tbody>\n</table>\n'
            f'<div class="clear"></div>\n<div class="right" id="id81">\n{_pager("pagination2", page, pages)}\n</div>\n</div>')


def ajax_response(components: dict[str, str], version: int | None = None) -> str:
    script = ""
    if version is not None:
        script = ("<evaluate><![CDATA[<head xmlns:wicket=\"http://wicket.apache.org\"><script type=\"text/javascript\">\n/*<![CDATA[*/\n"
                  + "".join(f'(function(){{Wicket.Ajax.ajax({{"u":"./SearchMatrikaPage?{version}-2.0-tableContainer-pagination-results-{k}","c":"id3{i}","e":"click","pd":true}});}})();'
                            for i, k in enumerate(("firstPageUrl", "lastPageUrl", "previousPageUrl", "nextPageUrl")))
                  + f'(function(){{Wicket.Ajax.ajax({{"u":"./SearchMatrikaPage?{version}-2.0-tableContainer-resultsOnPagePanel-resultsOnPage","m":"POST","c":"id44","e":"change"}});}})();'
                  + "\n/*]]]]><![CDATA[>*/\n</script>\n</head>]]></evaluate>")
    body = "".join(f'<component id="{cid}" ><![CDATA[{content}]]></component>' for cid, content in components.items())
    return f'<?xml version="1.0" encoding="UTF-8"?><ajax-response>{body}{script}</ajax-response>'


def search_page(page_id: int, sid: str) -> str:
    base = f"./SearchMatrikaPage;jsessionid={sid}?{page_id}-1.0-searchForm-"
    return f'''<!DOCTYPE html PUBLIC "-//W3C//DTD HTML 4.01//EN" "http://www.w3.org/TR/html4/strict.dtd">
<html class="htmlPage" lang="cs"><head>
<script type="text/javascript" id="wicket-ajax-base-url">
/*<![CDATA[*/
Wicket.Ajax.baseUrl="pages/SearchMatrikaPage?{page_id}";
/*]]>*/
</script>
<title>SYNTHETIC FIXTURE</title>
<script type="text/javascript">
/*<![CDATA[*/
var okresTerm = "district";
var obecTerm = "community";
var availableTags1 = {json.dumps([f"{n} - community {c} - district {d}" for c, d, names in LOCALITIES for n in names], ensure_ascii=False)};
var availableTags2 = ["Demolov 01", "Demolov 02"];
Wicket.Event.add(window, "domready", function(event) {{
Wicket.Ajax.ajax({{"u":"./SearchMatrikaPage;jsessionid={sid}?{page_id}-1.0-mainNavigation-loginLogoutPanel-loginPanel-login","c":"ida","e":"click","pd":true}});;
Wicket.Ajax.ajax({{"u":"{base}searchType","m":"POST","c":"idc","e":"change"}});;
Wicket.Ajax.ajax({{"u":"{base}searchButton","m":"POST","c":"ide","f":"idd","sc":"searchButton","e":"click","pd":true}});;
Wicket.Ajax.ajax({{"u":"{base}searchButtonFake","m":"POST","c":"idf","f":"idd","sc":"searchButtonFake","e":"click","pd":true}});;
}});
/*]]>*/
</script></head><body><h1>SYNTHETIC FIXTURE</h1>
<form class="searchForm" id="idd" method="post" action="./SearchMatrikaPage;jsessionid={sid}?{page_id}-1.-searchForm"><div id="idd_hf_0" hidden="" class="hidden-fields"></div>
<div class="searchBox"><select name="searchType" id="idc"><option selected="selected" value="1">By keyword</option><option value="2">By provenance type</option></select></div>
<div id="id22"><div class="searchBox">
<div><input type="radio" fake-value="1" id="id23" name="keywordBox:searchTokenIn" value="radio0" checked="checked"/><label>in localities</label></div>
<div><input type="radio" fake-value="2" id="id24" name="keywordBox:searchTokenIn" value="radio1"/><label>in register titles</label></div>
</div><div class="searchBox"><input type="text" class="searchToken" value="locality" name="keywordBox:searchToken" placeholder="locality"/></div></div>
<div class="searchBox"><input type="text" class="year" maxlength="4" value="1000" name="dateYearFrom" placeholder="1000"/>
<input type="text" class="year" maxlength="4" value="2100" name="dateYearTo" placeholder="2100"/></div>
<div class="searchBox"><div class="buttonBig searchButton" id="ide">Search</div><input type="image" src="/images/empty.gif" name="searchButtonFake" id="idf"/></div>
</form><div id="id26" hidden="" data-wicket-placeholder=""></div></body></html>'''


def detail_page(book) -> str:
    bid, title, kind, district, places, years, scans = book
    values = {"Original name": "", "District": district, "Provenance": f"{title.split()[0]} ({kind})",
              "Births / Index": years[0], "Marriages / Index": years[1], "Deaths / Index": years[2],
              "Physical state": "intact", "Completeness": "complete", "State of damage": DASH,
              "Register type": "the book contains only a register", "Language": "partly czech and partly latin", "Binding": DASH,
              "Dimension": "&nbsp;", "Number of pages": "&nbsp;", "Number of sheets": "51", "Number of scans": str(scans),
              "Place of storage": "Státní oblastní archiv v Praze"}
    rows = "".join(f'<div class="tableMatrikaBasicInfoRow">\n<div class="tableMatrikaBasicInfoColumn tableMatrikaBasicInfoHeader tableMatrikaBasicInfoHeader1">\n<div>{k}</div>\n</div>\n'
                   f'<div class="tableMatrikaBasicInfoColumn tableMatrikaBasicInfoHeader2">\n<div>{html.escape(values[k]) if k != "Dimension" and k != "Number of pages" else values[k]}</div>\n</div>\n</div>\n\n'
                   for k in DETAIL_LABELS)
    locs = "".join(f'<div id="id{i + 7:x}">\n\n<div class="obecCastLabel">{html.escape(name + (" / " + var if var else ""))}</div>\n\n</div>' for i, (name, var) in enumerate(places))
    return f'''<!DOCTYPE html><html><head><title>Státní oblastní archiv v Praze</title></head><body>
<div class="wrapper"><div id="headerData"><div class="headerContent"><div class="dataPageTitle">
<a class="hxLink" target="_blank" href="./{bid};jsessionid=x?0-1.0-mainNavigation-titleLink"><h1>{html.escape(title)}</h1></a></div>
<div id="menu"><div class="menuContainer">
<a href="/pages/DataMatrikaPage/matrikaId/{bid}" class="menuItem"><span id="id2">Scan viewer</span></a>
<a href="/pages/MatrikaPage/matrikaId/{bid}" class="menuItemCurrent"><span id="id4">Register description</span></a>
<a href="/pages/PuvodcePage/matrikaId/{bid}" class="menuItem"><span id="id5">Provenance</span></a>
<a href="/pages/UserNoteMatrikaPage/matrikaId/{bid}" class="menuItem"><span id="id6">Notes</span></a>
</div></div></div></div>
<div id="contentDataOtherPage"><div class="matrikaDetailLeftContainer"><div class="tableMatrikaBasicInfo noBottomBorder">
{rows}</div></div>
<div class="matrikaDetailLeftContainer"><div class="tableMatrikaDescription">
<div class="tableMatrikaDescriptionRow tableMatrikaDescriptionHeader"><div class="tableMatrikaDescriptionColumn tableMatrikaDescriptionHeader1"><span>General description of the register </span></div></div>
<div class="tableMatrikaDescriptionRow"><div class="tableMatrikaDescriptionColumn tableMatrikaDescriptionHeader1"><span>SYNTHETIC layout note<br/>2-41 baptisms</span></div></div></div></div>
<div class="matrikaDetailLeftContainer"><div class="tableCastiObci"><div class="tableCastiObciRow tableCastiObciHeader"><div class="tableCastiObciColumn tableCastiObciHeader1">Localities</div></div>
<div class="tableCastiObciColumn tableCastiObciHeader1">{locs}</div></div></div></div></div></body></html>'''


def viewer_page(book, n: int) -> str:
    bid, title, scans = book[0], book[1], book[6]
    return f'''<!DOCTYPE html><html><head><title>Státní oblastní archiv v Praze</title>
<script>var currentZoom = 0.0;var basicUrl = '/pages/DataMatrikaPage/matrikaId/{bid}';var idx = {n - 1};var pageCount = {scans};</script>
</head><body id="dataBody"><div id="headerData"><div class="dataPageTitle"><a class="hxLink" href="./{n}?1-1.-mainNavigation-titleLink"><h1>{html.escape(title)}</h1></a></div></div>
<div id="contentDataPage"><div id="scanContainer"><div id="zoomContainer"><img id="scanImage" alt="Obrázek archiválie" src="./{n}?1--scanImage"/></div></div></div>
<div class="footerDataPage"><div class="footerContentDataPage">
<div class="scanManipulationBox dataFotterCorrection2"><div class="footerDataPageText">1.31 MB</div></div>
<div class="scanManipulationBox dataFotterCorrection2"><a class="manipulationButton" target="_blank" href="../../wicket/resource/org.apache.wicket.Application/{image_name(bid, n)}"><span>&#9660;</span></a></div>
</div></div></body></html>'''


# ---------------------------------------------------------------- archival records (census, land books, ...): invented as well
CENSUS_T, TOWN_T, ESTATE_T, CHURCH_T, PHOTO_T = "Sčítací operáty", "Města a obce", "Velkostatky", "Církev", "Fotografie"
TYPE_OPTIONS = [("8", "Cechy"), ("6", CHURCH_T), ("14", PHOTO_T), ("3", TOWN_T), ("19", CENSUS_T), ("2", ESTATE_T), ("11", "Rodinné archivy")]
# id, title, type, fund, inventory number, signature, [localities], years "from - to", year, exact date, original title, scans
ARCHIVALIA = [
    (8001, "Sčítání lidu 1869: Okres Testov - obec Demolov", CENSUS_T, "Okresní úřad Testov 1850-1945", "", "", ["Demolov"], DASH, "1869", DASH, "", 3),
    (8002, "Sčítání lidu 1880: Okres Testov - obec Demolov, čp. 1-50", CENSUS_T, "Okresní úřad Testov 1850-1945", "", "", ["Demolov"], DASH, "1880", DASH, "", 2),
    (8003, "Sčítání lidu 1880: Okres Testov - obec Demolov, čp. 51-80", CENSUS_T, "Okresní úřad Testov 1850-1945", "", "", ["Demolov"], DASH, "1880", DASH, "", 2),
    (8004, "Sčítání lidu 1900: Okres Testov - obec Demolov, čp. 1-50", CENSUS_T, "Okresní úřad Testov 1850-1945", "", "", ["Demolov"], DASH, "1900", DASH, "", 2),
    (8005, "Sčítání lidu 1900: Okres Testov - obec Demolov - osada Horní Demolov", CENSUS_T, "Okresní úřad Testov 1850-1945", "", "", ["Horní Demolov"], DASH, "1900", DASH, "", 0),
    (8006, "Sčítání lidu 1900: Okres Kolonov - obec Lhotka, čp. 1-25", CENSUS_T, "Okresní úřad Kolonov 1850-1945", "", "", ["Testov"], DASH, "1900", DASH, "", 2),
    (8007, "Sčítání lidu 1910: Okres Testov - obec Demolov, čp. 1-50 a 60-70", CENSUS_T, "Okresní úřad Testov 1850-1945", "", "", ["Demolov"], DASH, "1910", DASH, "", 2),
    (8008, "Sčítání lidu 1910: Okres Testov - obec Demolov (různé)", CENSUS_T, "Okresní úřad Testov 1850-1945", "", "", ["Demolov"], DASH, "1910", DASH, "", 2),
    (8009, "Sčítání lidu 1900: Okres Historie - obec Testov, čp. 1-10", CENSUS_T, "Okresní úřad Historie 1850-1945", "", "", ["Testov"], DASH, "1900", DASH, "", 2),
    (8101, "Demolov: Pozemková kniha města 1700-1780", TOWN_T, "Archiv města Demolov", "", "OS Testov 100", ["Demolov"], "1700 - 1780", DASH, DASH, "Grundbuch der Stadt Demolau", 3),
    (8102, "Demolov: Purkrechtní kniha 1650-1699", TOWN_T, "Archiv města Demolov", "", "OS Testov 101", ["Demolov"], "1650 - 1699", DASH, DASH, "", 2),
    (8103, "Demolov: Kronika města 1847-1959", TOWN_T, "Archiv města Demolov", "", "", ["Demolov", "Horní Demolov"], "1847 - 1959", DASH, DASH, "", 2),
    (8201, "Testov: Urbář 1669", ESTATE_T, "Velkostatek Testov", "23", "", ["Demolov", "Testov"], DASH, "1669", DASH, "Urbář panství", 2),
    (8202, "Testov: Kniha gruntovní dominikální 1757-1877", ESTATE_T, "Velkostatek Testov", "", "OS Testov 6", ["Demolov", "Testov"], "1757 - 1877", DASH, DASH, "", 2),
    (8203, "Testov: Kniha svatebních smluv", ESTATE_T, "Velkostatek Testov", "120", "", ["Demolov"], DASH, DASH, DASH, "", 2),
    (8301, "Demolov: Kronika farnosti 1704-1822", CHURCH_T, "Farní úřad Demolov", "", "", ["Demolov"], "1704 - 1822", DASH, DASH, "", 2),
    (8302, "Demolov: Fotografie náměstí", PHOTO_T, "Sbírka fotografií", "", "", ["Demolov"], DASH, "1930", DASH, "", 1),
]
ARCH_BY_ID = {a[0]: a for a in ARCHIVALIA}
ARCH_HEADERS = ("Archival name", "Archival type", "Archival fund", "Inventory number", "Signature", "Localities", "Date (year from to)",
                "Date (year)", "Date (exact date)", "Original name", "Scans")
ARCH_DETAIL_LABELS = ("Original title", "Archival type", "Archival fund", "Storage location", "Date from to", "Date year", "Exact date",
                      "Inventory number", "Signature", "Language", "Scans")


def arch_image_name(archive_id: int, n: int) -> str:
    """The archive names a scan after the record's title: spaces to underscores, diacritics removed, 'čp.' becomes 'cp.'."""
    ascii_title = "".join(c for c in unicodedata.normalize("NFKD", ARCH_BY_ID[archive_id][1]) if not unicodedata.combining(c))
    return re.sub(r"\s+", "_", ascii_title) + f"__{n:03d}.jpg"


def arch_scan_count(record) -> int:
    return record[11]


def _arch_row(index: int, a) -> str:
    aid, title = a[0], a[1]
    cells = [f'<td>\n<a target="_blank" class="matrikaLink" onclick="renderRowClasses(this.parentNode.parentNode.getAttribute(&#039;rowindex&#039;));" '
             f'href="/pages/ArchivaliePage/archivalieId/{aid}">{html.escape(title).replace(" ", "&nbsp;")}</a>\n</td>\n',
             _cell(a[2]), _cell(a[3]), _cell(a[4]), _cell(a[5]), _cell(", ".join(a[6])), _cell(a[7]), _cell(a[8]), _cell(a[9]), _cell(a[10]), _cell(str(a[11]))]
    return (f'<tr name="dataLink" onclick="renderRowClasses(this.getAttribute(&#039;rowindex&#039;)); openInNewTab(this.getAttribute(&#039;href&#039;));" '
            f'id="id{0x3e + index:x}" href="/pages/DataArchivaliePage/archivalieId/{aid}/idx/1" rowindex="{index}" class="{"even" if index % 2 == 0 else "odd"}">\n\n'
            + "".join(cells) + "\n</tr>\n")


def _arch_pager(name: str, page: int, pages: int) -> str:
    options = "".join('<option ' + ('selected="selected" ' if i == page else '') + f'value="{i}">{i + 1}</option>\n' for i in range(pages))
    return (f'<div id="pagination"><div><table><tr><td><a class="pageNumberCurrent" href="#"><span>1</span></a></td><td>'
            f'<div style="padding-top: 0px; float: left;"><select class="pageDropdown" name="tableContainer:{name}:results:pageDropdown">\n{options}</select></div>'
            f'</td><td><a class="pageNumber" href="#">&nbsp;&gt;&nbsp;</a><a class="pageNumber" href="#"><span>{pages}</span></a></td></tr></table></div></div>')


def arch_results_component(hits, page: int) -> str:
    pages = max(1, -(-len(hits) // ROWS_PER_PAGE))
    head = "".join(f'<th class="header{i}">{h}</th>\n' for i, h in enumerate(ARCH_HEADERS, 1))
    rows = "".join(_arch_row(i, a) for i, a in enumerate(hits[page * ROWS_PER_PAGE:(page + 1) * ROWS_PER_PAGE]))
    return (f'<div id="id23">\n<div class="resultsFoundContainer">\n<div class="right" id="id27">\n{_arch_pager("pagination", page, pages)}\n</div>\n'
            f'<div class="right resultsOnPage" id="id28"><select class="pageDropdown" name="tableContainer:resultsOnPagePanel:resultsOnPage">'
            f'<option value="5">5 results per page</option><option selected="selected" value="{ROWS_PER_PAGE}">{ROWS_PER_PAGE} results per page</option></select></div>\n'
            f'<div class="right resultsFound"><span id="id3d">{len(hits)} archival found</span></div>\n</div>\n<div class="clear"></div>\n'
            f'<table class="searchResult archivalieResult">\n<thead>\n<tr>\n{head}</tr>\n</thead>\n<tbody>\n{rows}</tbody>\n</table>\n'
            f'<div class="clear"></div>\n<div class="right" id="id60">\n{_arch_pager("pagination2", page, pages)}\n</div>\n</div>')


def arch_ajax_response(components: dict[str, str], version: int | None = None) -> str:
    script = ""
    if version is not None:
        script = ("<evaluate><![CDATA[<head xmlns:wicket=\"http://wicket.apache.org\"><script type=\"text/javascript\">\n/*<![CDATA[*/\n"
                  + "".join(f'(function(){{Wicket.Ajax.ajax({{"u":"./SearchArchivaliePage?{version}-1.0-tableContainer-pagination-results-{k}","c":"id2{i}","e":"click","pd":true}});}})();'
                            for i, k in enumerate(("firstPageUrl", "lastPageUrl", "previousPageUrl", "nextPageUrl")))
                  + f'(function(){{Wicket.Ajax.ajax({{"u":"./SearchArchivaliePage?{version}-1.0-tableContainer-resultsOnPagePanel-resultsOnPage","m":"POST","c":"id33","e":"change"}});}})();'
                  + "\n/*]]]]><![CDATA[>*/\n</script>\n</head>]]></evaluate>")
    body = "".join(f'<component id="{cid}" ><![CDATA[{content}]]></component>' for cid, content in components.items())
    return f'<?xml version="1.0" encoding="UTF-8"?><ajax-response>{body}{script}</ajax-response>'


def arch_search_page(page_id: int, sid: str) -> str:
    base = f"./SearchArchivaliePage;jsessionid={sid}?{page_id}-1.0-searchForm-"
    options = "".join(f'<option value="{v}">{html.escape(t)}</option>\n' for v, t in TYPE_OPTIONS)
    return f'''<!DOCTYPE html PUBLIC "-//W3C//DTD HTML 4.01//EN" "http://www.w3.org/TR/html4/strict.dtd">
<html class="htmlPage" lang="cs"><head>
<script type="text/javascript" id="wicket-ajax-base-url">
/*<![CDATA[*/
Wicket.Ajax.baseUrl="pages/SearchArchivaliePage?{page_id}";
/*]]>*/
</script>
<title>SYNTHETIC FIXTURE</title>
<script>var okresTerm = "district";

var lokalitaTerm = "locality";

var knihaTerm = "title";

var obecTerm = "community";

var availableTags1 = {json.dumps([f"{n} - community {c} - district {d}" for c, d, names in LOCALITIES for n in names], ensure_ascii=False)};
var availableTags2 = ["Demolov: Urbář"];
Wicket.Event.add(window, "domready", function(event) {{
Wicket.Ajax.ajax({{"u":"{base}mistoUlozeni","m":"POST","c":"idc","e":"change"}});;
Wicket.Ajax.ajax({{"u":"{base}searchButton","m":"POST","c":"ide","f":"idd","sc":"searchButton","e":"click","pd":true}});;
Wicket.Ajax.ajax({{"u":"{base}searchButtonFake","m":"POST","c":"idf","f":"idd","sc":"searchButtonFake","e":"click","pd":true}});;
}});
</script></head><body><h1>SYNTHETIC FIXTURE</h1>
<form class="searchForm" id="idd" method="post" action="./SearchArchivaliePage;jsessionid={sid}?{page_id}-1.-searchForm"><div id="idd_hf_0" hidden="" class="hidden-fields"></div>
<div class="searchBox"><div class="label first">Localities</div><input type="text" class="searchLocality" value="locality" name="searchLocality" placeholder="locality"/></div>
<div class="searchBox"><div class="label first">Archival name</div><input type="text" class="searchArchivalie" value="archival record" name="searchArchivalie" placeholder="archival record"/></div>
<div class="searchBox"><div class="label">Archival type</div><select class="archivalie" name="typArchivalie"><option selected="selected" value=""></option>
{options}</select></div>
<div class="searchBox"><div class="label">Storage location</div><select class="archivalie" name="mistoUlozeni" id="idc"><option selected="selected" value=""></option><option value="1">Státní oblastní archiv v Praze</option></select></div>
<div class="searchBox"><div class="label">Archival fund</div><select class="archivalie" name="archivniFondId" id="id22"><option selected="selected" value=""></option><option value="748">Archiv města Demolov</option></select></div>
<div class="searchBox"><input type="text" class="year" maxlength="4" value="1000" name="dateYearFrom" placeholder="1000"/>
<input type="text" class="year" maxlength="4" value="2100" name="dateYearTo" placeholder="2100"/></div>
<div class="searchBox"><div class="buttonBig searchButton" id="ide">Search</div><input type="image" src="/images/empty.gif" name="searchButtonFake" id="idf"/></div>
</form><div id="id23" hidden="" data-wicket-placeholder=""></div><div id="id24" hidden="" data-wicket-placeholder=""></div></body></html>'''


def arch_detail_page(a) -> str:
    aid, title, kind, fund, inv, sig, places, span, year, exact, original, scans = a
    values = {"Original title": original or DASH, "Archival type": kind, "Archival fund": fund, "Storage location": "Státní okresní archiv Testov",
              "Date from to": span, "Date year": year, "Exact date": exact, "Inventory number": inv or DASH, "Signature": sig or DASH,
              "Language": "czech, german", "Scans": str(scans)}
    rows = "".join(f'<div class="tableMatrikaBasicInfoRow">\n<div class="tableMatrikaBasicInfoColumn tableMatrikaBasicInfoHeader tableMatrikaBasicInfoHeader1">\n<div>{k}</div>\n</div>\n'
                   f'<div class="tableMatrikaBasicInfoColumn tableMatrikaBasicInfoHeader2">\n<div>{html.escape(values[k])}</div>\n</div>\n</div>\n\n'
                   for k in ARCH_DETAIL_LABELS)
    locs = "".join(f'<div id="id{i + 7:x}">\n\n<div class="obecCastLabel">{html.escape(name)}</div>\n\n</div>' for i, name in enumerate(places))
    return f'''<!DOCTYPE html><html><head><title>Státní oblastní archiv v Praze</title></head><body>
<div class="wrapper"><div id="headerData"><div class="headerContent"><div class="dataPageTitle">
<a class="hxLink" target="_blank"><h1>{html.escape(title)}</h1></a></div>
<div id="menu"><div class="menuContainer">
<a href="/pages/DataArchivaliePage/archivalieId/{aid}" class="menuItem"><span id="id9d">Scan viewer</span></a>
<a href="/pages/ArchivaliePage/archivalieId/{aid}" class="menuItemCurrent"><span id="id9f">Register description</span></a>
</div></div></div></div>
<div id="contentDataOtherPage"><div class="matrikaDetailLeftContainer"><div class="tableMatrikaBasicInfo">
{rows}</div>
<div class="marginTop30"><h3>Archival fund</h3><div class="tableMatrikaBasicInfo">
<div class="tableMatrikaBasicInfoRow"><div class="tableMatrikaBasicInfoColumn tableMatrikaBasicInfoHeader tableMatrikaBasicInfoHeader1"><div>Archival fund</div></div><div class="tableMatrikaBasicInfoColumn tableMatrikaBasicInfoHeader2"><div>{html.escape(fund)}</div></div></div>
<div class="tableMatrikaBasicInfoRow"><div class="tableMatrikaBasicInfoColumn tableMatrikaBasicInfoHeader tableMatrikaBasicInfoHeader1"><div>NAD</div></div><div class="tableMatrikaBasicInfoColumn tableMatrikaBasicInfoHeader2"><div>59</div></div></div>
</div></div></div>
<div class="matrikaDetailLeftContainer"><div class="tableMatrikaDescription">
<div class="tableMatrikaDescriptionRow tableMatrikaDescriptionHeader"><div class="tableMatrikaDescriptionColumn tableMatrikaDescriptionHeader1"><span>Description</span></div></div>
<div class="tableMatrikaDescriptionRow"><div class="tableMatrikaDescriptionColumn tableMatrikaDescriptionHeader1"><span>SYNTHETIC description of {html.escape(title)}</span></div></div></div>
<div class="tableMatrikaDescription"><div class="tableMatrikaDescriptionRow tableMatrikaDescriptionHeader"><div class="tableMatrikaDescriptionColumn tableMatrikaDescriptionHeader1"><span>Notes</span></div></div>
<div class="tableMatrikaDescriptionRow"><div class="tableMatrikaDescriptionColumn tableMatrikaDescriptionHeader1"><span>---</span></div></div></div></div>
<div class="matrikaDetailLeftContainer"><div class="tableCastiObci"><div class="tableCastiObciRow tableCastiObciHeader"><div class="tableCastiObciColumn tableCastiObciHeader1">Localities</div></div>
<div class="tableCastiObciColumn tableCastiObciHeader1">{locs}</div></div></div></div></div></body></html>'''


def arch_viewer_page(a, n: int) -> str:
    aid, title, scans = a[0], a[1], a[11]
    return f'''<!DOCTYPE html><html><head><title>Státní oblastní archiv v Praze</title>
<script>var currentZoom = 0.0;var basicUrl = '/pages/DataArchivaliePage/archivalieId/{aid}';var idx = {n - 1};var pageCount = {scans};</script>
</head><body id="dataBody"><div id="headerData"><div class="dataPageTitle"><a class="hxLink" href="./{n}?1-1.-mainNavigation-titleLink"><h1>{html.escape(title)}</h1></a></div></div>
<div id="contentDataPage"><div id="scanContainer"><div id="zoomContainer"><img id="scanImage" alt="Obrázek archiválie" src="./{n}?1--scanImage"/></div></div></div>
<div class="footerDataPage"><div class="footerContentDataPage">
<div class="scanManipulationBox dataFotterCorrection2"><div class="footerDataPageText">3.03 MB</div></div>
<div class="scanManipulationBox dataFotterCorrection2"><a class="manipulationButton" target="_blank" href="../../wicket/resource/org.apache.wicket.Application/{arch_image_name(aid, n)}"><span>&#9660;</span></a></div>
</div></div></body></html>'''


class DemoSoapraha:
    def __init__(self):
        self.sessions: dict[str, dict] = {}
        self.log: list[tuple[str, str, bool]] = []      # (method, path?query, request carried a valid session cookie)
        self.posts: list[dict] = []
        self.mode = "normal"                            # "expired": every Wicket page answers "Page Expired"
        self.expire_next = 0                            # the next N loads of the search page answer "Page Expired"
        self.start_version = 0                          # first page version of a new session (component ids follow it)
        self.ajax: list[tuple[str, str | None]] = []    # (address, Wicket-Ajax header) of every request to a listener
        self.jpeg_override = None                       # callable (book_id, n) -> httpx.Response replacing the scan download

    def new_session(self) -> str:
        sid = uuid.uuid4().hex.upper()
        self.sessions[sid] = {"pages": set(), "counter": self.start_version, "hits": [], "version": 0, "rendered": set()}
        return sid

    def new_page(self, sid: str) -> int:
        state = self.sessions[sid]
        page = state["counter"]
        state["counter"] += 1
        state["pages"].add(page)
        return page

    def handle(self, request: httpx.Request) -> httpx.Response:
        path, raw_query = request.url.path, request.url.query.decode()
        target = path + (("?" + raw_query) if raw_query else "")
        m = re.fullmatch(r"JSESSIONID=([A-Za-z0-9]+)", request.headers.get("cookie", "").strip())
        sid = m[1] if m and m[1] in self.sessions else None
        self.log.append((request.method, target, sid is not None))
        if "-searchForm-" in raw_query or "-tableContainer-" in raw_query:
            self.ajax.append((target, request.headers.get("wicket-ajax")))
        base = re.sub(r";jsessionid=[A-Za-z0-9]+", "", path)
        def redirect(location, cookie=None):
            headers = {"location": location, **NO_STORE}
            if cookie: headers["set-cookie"] = f"JSESSIONID={cookie}; Path=/; Secure; HttpOnly"
            return httpx.Response(302, headers=headers)
        if request.method == "GET" and base == "/robots.txt":
            cookie = self.new_session()
            return redirect(f"./pages/ErrorPage;jsessionid={cookie}", cookie)
        if request.method == "GET" and base == "/pages/ErrorPage":
            if not raw_query:
                return redirect(f"./ErrorPage;jsessionid={re.search(r'jsessionid=([A-Za-z0-9]+)', path)[1]}?1")
            return httpx.Response(200, text="<html><body>We're sorry, the page you've requested doesn't exist.</body></html>", headers=HTML)
        if base == "/pages/SearchMatrikaPage":
            return self.search(request, sid, base, raw_query, redirect)
        if base == "/pages/SearchArchivaliePage":
            return self.arch_search(request, sid, raw_query, redirect)
        m = re.fullmatch(r"/pages/ArchivaliePage/archivalieId/(\d+)", base)
        if m and request.method == "GET":
            if int(m[1]) not in ARCH_BY_ID: return httpx.Response(404)
            if not raw_query:
                if sid is None:
                    cookie = self.new_session(); page = self.new_page(cookie)
                    return redirect(f"./{m[1]};jsessionid={cookie}?{page}", cookie)
                return redirect(f"./{m[1]}?{self.new_page(sid)}")
            if sid is None or not raw_query.isdigit() or int(raw_query) not in self.sessions[sid]["pages"]:
                return httpx.Response(200, text="<html><body><h1>Page Expired</h1></body></html>", headers=HTML)
            return httpx.Response(200, text=arch_detail_page(ARCH_BY_ID[int(m[1])]), headers=HTML)
        m = re.fullmatch(r"/a/(\d+)/(\d+)", base)
        if m and request.method == "GET":
            record, n = ARCH_BY_ID.get(int(m[1])), int(m[2])
            if record is None or not 1 <= n <= arch_scan_count(record): return redirect("../../pages/ErrorPage")
            if sid is None:
                cookie = self.new_session()
                return redirect(f"./{n}", cookie)
            self.sessions[sid]["rendered"].add((("a", record[0]), n))
            return httpx.Response(200, text=arch_viewer_page(record, n), headers=HTML)
        m = re.fullmatch(r"/pages/MatrikaPage/matrikaId/(\d+)", base)
        if m and request.method == "GET":
            if int(m[1]) not in BY_ID: return httpx.Response(404)
            if not raw_query:
                if sid is None:
                    cookie = self.new_session(); page = self.new_page(cookie)
                    return redirect(f"./{m[1]};jsessionid={cookie}?{page}", cookie)
                return redirect(f"./{m[1]}?{self.new_page(sid)}")
            if sid is None or not raw_query.isdigit() or int(raw_query) not in self.sessions[sid]["pages"]:
                return httpx.Response(200, text="<html><body><h1>Page Expired</h1></body></html>", headers=HTML)
            return httpx.Response(200, text=detail_page(BY_ID[int(m[1])]), headers=HTML)
        m = re.fullmatch(r"/pages/DataMatrikaPage/idx/(\d+)/matrikaId/(\d+)", base)
        if m and request.method == "GET":
            return redirect(f"../../../../../d/{m[2]}/{m[1]}")
        m = re.fullmatch(r"/d/(\d+)/(\d+)", base)
        if m and request.method == "GET":
            book, n = BY_ID.get(int(m[1])), int(m[2])
            if book is None or not 1 <= n <= book[6]: return redirect("../../pages/ErrorPage")
            if sid is None:
                cookie = self.new_session()
                return redirect(f"./{n}", cookie)
            self.sessions[sid]["rendered"].add((book[0], n))
            return httpx.Response(200, text=viewer_page(book, n), headers=HTML)
        m = re.fullmatch(r"/wicket/resource/org\.apache\.wicket\.Application/(.+)__(\d{3,4})\.jpg", base)
        if m and request.method == "GET":
            arch = next((((("a", a[0]), int(m[2])) for a in ARCHIVALIA if arch_image_name(a[0], int(m[2])) == f"{m[1]}__{m[2]}.jpg")), None)
            if arch is not None:
                if sid is None or arch not in self.sessions[sid]["rendered"]:
                    return redirect("../../../pages/ErrorPage")
                out = io.BytesIO()
                scan_image(arch[0][1], arch[1]).save(out, format="JPEG", quality=90)
                return httpx.Response(200, content=out.getvalue(), headers={
                    "content-type": "image/jpeg", "content-disposition": f'attachment; filename="{arch_image_name(arch[0][1], arch[1])}"', **NO_STORE})
            wanted = next(((b[0], int(m[2])) for b in BOOKS if image_name(b[0], int(m[2])) == f"{m[1]}__{m[2]}.jpg"), None)
            if sid is None or wanted is None or wanted not in self.sessions[sid]["rendered"]:
                return redirect("../../../pages/ErrorPage")
            if self.jpeg_override is not None:
                return self.jpeg_override(*wanted)
            out = io.BytesIO()
            scan_image(*wanted).save(out, format="JPEG", quality=90)
            return httpx.Response(200, content=out.getvalue(), headers={
                "content-type": "image/jpeg", "content-disposition": f'attachment; filename="{image_name(*wanted)}"', **NO_STORE})
        return httpx.Response(404, text="Synthetic eBadatelna fixture has no such endpoint")

    def arch_search(self, request, sid, raw_query, redirect):
        if request.method == "GET" and not raw_query:
            if sid is None:
                cookie = self.new_session()
                return redirect(f"./SearchArchivaliePage;jsessionid={cookie}?{self.new_page(cookie)}", cookie)
            return redirect(f"./SearchArchivaliePage?{self.new_page(sid)}")
        expired = httpx.Response(200, text="<html><body><h1>Page Expired</h1></body></html>", headers=HTML)
        if sid is None or self.mode == "expired":
            return expired
        state = self.sessions[sid]
        if request.method == "GET" and raw_query.isdigit():
            if self.expire_next > 0:
                self.expire_next -= 1
                return expired
            return httpx.Response(200, text=arch_search_page(int(raw_query), sid), headers=HTML) if int(raw_query) in state["pages"] else expired
        m = re.fullmatch(r"(\d+)-1\.0-searchForm-searchButton", raw_query)
        if request.method == "POST" and m:
            if int(m[1]) not in state["pages"]: return expired
            form = {k: v[0] for k, v in parse_qs(request.content.decode(), keep_blank_values=True).items()}
            self.posts.append(form)
            if sorted(form) != sorted(("searchLocality", "searchArchivalie", "typArchivalie", "mistoUlozeni", "archivniFondId", "dateYearFrom", "dateYearTo")):
                return httpx.Response(400)
            # the live application matches the locality only when it is one entry of its own list ("<name> - community <c> - district <d>")
            entry = re.fullmatch(r"(.+?) - community (.+?) - district (.+)", form["searchLocality"])
            known = {f"{n} - community {c} - district {d}" for c, d, names in LOCALITIES for n in names}
            hits = []
            if entry and form["searchLocality"] in known:
                hits = [a for a in ARCHIVALIA if entry[1] in a[6] and (not form["typArchivalie"] or dict((t, v) for v, t in TYPE_OPTIONS).get(a[2]) == form["typArchivalie"])]
            state["arch_hits"], state["arch_page"], state["arch_post_page"] = hits, 0, int(m[1])
            if not hits:
                return httpx.Response(200, headers=XML, text=arch_ajax_response({
                    "id24": '<div id="id24">\nNo results found\n</div>', "id23": '<div id="id23" hidden="" data-wicket-placeholder=""></div>'}, None))
            return httpx.Response(200, headers=XML, text=arch_ajax_response({
                "id24": '<div id="id24" hidden="" data-wicket-placeholder=""></div>', "id23": arch_results_component(hits, 0)}, int(m[1])))
        m = re.fullmatch(r"(\d+)-1\.0-tableContainer-pagination-results-nextPageUrl", raw_query)
        if request.method == "GET" and m:
            if int(m[1]) != state.get("arch_post_page"): return expired
            pages = max(1, -(-len(state["arch_hits"]) // ROWS_PER_PAGE))
            state["arch_page"] = min(state["arch_page"] + 1, pages - 1)
            return httpx.Response(200, headers=XML, text=arch_ajax_response({
                "id24": '<div id="id24" hidden="" data-wicket-placeholder=""></div>', "id23": arch_results_component(state["arch_hits"], state["arch_page"])}, int(m[1])))
        return httpx.Response(404)

    def search(self, request, sid, base, raw_query, redirect):
        if request.method == "GET" and not raw_query:
            if sid is None:
                cookie = self.new_session()
                return redirect(f"./SearchMatrikaPage;jsessionid={cookie}?{self.new_page(cookie)}", cookie)
            return redirect(f"./SearchMatrikaPage?{self.new_page(sid)}")
        expired = httpx.Response(200, text="<html><body><h1>Page Expired</h1></body></html>", headers=HTML)
        if sid is None or self.mode == "expired":
            return expired
        state = self.sessions[sid]
        if request.method == "GET" and raw_query.isdigit():
            if self.expire_next > 0:
                self.expire_next -= 1
                return expired
            return httpx.Response(200, text=search_page(int(raw_query), sid), headers=HTML) if int(raw_query) in state["pages"] else expired
        m = re.fullmatch(r"(\d+)-1\.0-searchForm-searchButton", raw_query)
        if request.method == "POST" and m:
            if int(m[1]) not in state["pages"]: return expired
            form = {k: v[0] for k, v in parse_qs(request.content.decode(), keep_blank_values=True).items()}
            self.posts.append(form)
            if sorted(form) != sorted(("searchType", "keywordBox:searchTokenIn", "keywordBox:searchToken", "dateYearFrom", "dateYearTo")):
                return httpx.Response(400)
            state["hits"] = [b for b in BOOKS if _matches(b, _tokens(form["keywordBox:searchToken"]))]
            state["page"] = 0
            if not state["hits"]:
                return httpx.Response(200, headers=XML, text=ajax_response({
                    "id27": '<div id="id27">\nNo results found\n</div>', "id26": '<div id="id26" hidden="" data-wicket-placeholder=""></div>'}, None))
            state["version"] = int(m[1]) + 1
            return httpx.Response(200, headers=XML, text=ajax_response({
                "id27": '<div id="id27" hidden="" data-wicket-placeholder=""></div>', "id26": results_component(state["hits"], 0)}, state["version"]))
        m = re.fullmatch(r"(\d+)-2\.0-tableContainer-pagination-results-nextPageUrl", raw_query)
        if request.method == "GET" and m:
            if int(m[1]) != state["version"]: return expired
            pages = max(1, -(-len(state["hits"]) // ROWS_PER_PAGE))
            state["page"] = min(state["page"] + 1, pages - 1)
            state["version"] += 1
            return httpx.Response(200, headers=XML, text=ajax_response({
                "id27": '<div id="id27" hidden="" data-wicket-placeholder=""></div>', "id26": results_component_page(state)}, state["version"]))
        return httpx.Response(404)


def results_component_page(state) -> str:
    return results_component(state["hits"], state["page"])
