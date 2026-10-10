"""Entirely synthetic responses shaped like the DigiArchiv of SOA Třeboň (PHP application: a tree of parishes, one viewer page
per scan, and an image CGI that Rubrika never calls). Parishes, books, ids and images are invented;
NEVER real archive data. Excluded from the desktop bundle (file name starts with "demo")."""
import html
import re
from urllib.parse import parse_qs

import httpx

HOST = "digi.ceskearchivy.cz"
HTML = {"content-type": "text/html; charset=UTF-8"}
ROBOTS = """User-agent: *
Allow: /index.php
Allow: /index_main.php
Allow: /DA*
Disallow: /Assets/*
Disallow: /cache/*
Disallow: /help/*
Disallow: /oai/*
Disallow: /pages/*
"""
# (letter code, index label). Letters without a parish here (the live tree has more) are simply not offered.
LETTERS = [("c", "C/Č"), ("i", "CH"), ("p", "P"), ("t", "T")]
# id, tree code, (letter, Czech title, English label in the tree), district, parish district, other localities, books
# book: id, number, label, [(type letter, years)], localities, other localities, note, scans (0 = not online yet, then the year)
PARISHES = [
    (910001, "1nrcl", "České Budějovice (St. Nicholas)", "České Budějovice (sv. Mikuláš)", "České Budějovice",
     ["České Budějovice", "České Vrbné", "Čtyři Dvory", "Vidov", "Mokré"], ["Adamov", "Borovany", "Hamburk"],
     [(911001, "1", "register B", [("B", "1615-1630")], ["České Budějovice", "České Vrbné", "Čtyři Dvory"], ["Borovany"], "", 3),
      (911002, "2", "register B", [("B", "1631-1660")], ["České Budějovice", "České Vrbné", "Čtyři Dvory"], [], "s indexem", 2),
      (911003, "40", "register M", [("M", "1644-1700")], ["České Budějovice", "České Vrbné"], [], "", 2),
      (911004, "70", "register D", [("D", "1670-1720")], ["České Budějovice"], [], "", 2),
      (911005, "53C", "register BMD", [("B", "1785-1920"), ("M", "1793-1920"), ("D", "1785-1919")], ["Vidov"], [], "s indexem", 2),
      (911006, "6", "index B", [("B", "1785-1900")], ["České Budějovice", "Vidov"], [], "", 2),
      (911007, "7", "register B", [("B", "1900-1910, 1915")], ["České Budějovice"], [], "", 1),
      (911008, "8", "register B", [("B", "1911-1920")], ["Mokré"], [], "duplikát", 1),
      (911009, "9", "register B", [("B", "1921-1925")], [], ["České Budějovice"], "", 1),
      (911099, "3", "register B", [("B", "1928-1933")], ["České Budějovice"], [], "", 2033)]),
    (910002, "1nrcM", "České Budějovice (St. John of Nepomuk)", "České Budějovice (sv. Jan Nepomucký)", "České Budějovice",
     ["České Budějovice"], [], [(912001, "1", "register B", [("B", "1785-1850")], ["České Budějovice"], [], "", 2)]),
    (910003, "1nrco", "Český Krumlov", "Český Krumlov", "Český Krumlov", ["Český Krumlov", "Plešivec"], ["Větřní"],
     [(913001, "1", "register B", [("B", "1591-1620")], ["Český Krumlov"], [], "", 2),
      (913002, "2", "register M", [("M", "1591-1620")], ["Český Krumlov"], [], "", 1)]),
    (920001, "1nrta", "Tábor", "Tábor", "Tábor", ["Tábor", "Měšice", "Čekanice"], ["Sezimovo Ústí"],
     [(921001, "1", "register B", [("B", "1700-1750")], ["Tábor", "Měšice"], [], "", 2),
      (921002, "2", "register M", [("M", "1700-1750")], ["Tábor"], [], "", 1)]),
    (920002, "1nrtb", "Třeboň", "Třeboň", "Jindřichův Hradec", ["Třeboň"], [],
     [(922001, "1", "register B", [("B", "1650-1700")], ["Třeboň"], [], "", 1)]),
    (930001, "1nrpa", "Písek", "Písek", "Písek", ["Písek", "Semice"], [], [(931001, "1", "register B", [("B", "1680-1720")], ["Písek"], [], "", 2)]),
    (930002, "1nrpb", "Protivín", "Protivín", "Písek", ["Protivín"], [], [(932001, "1", "register B", [("B", "1701-1760")], ["Protivín"], [], "", 1)]),
    (940001, "1nric", "Chotoviny", "Chotoviny", "Tábor", ["Chotoviny"], [], [(941001, "1", "register B", [("B", "1710-1780")], ["Chotoviny"], [], "", 1)]),
]
# Non-Catholic congregations and civil registers (the live tree lists them directly under the confession's root, without letters).
# The confession roots that are not here answer like an unknown code, which Rubrika reports as a branch it could not read.
CONFESSION_ROOTS = {"1nC": "Evangelical Church of Czech Brethren", "1nc": "Czechoslovak Hussite Church", "1nb": "Churchless/Civil Registers"}
PARISHES += [
    (950001, "1nCa", "Slavonice", "Slavonice", "Jindřichův Hradec", ["Slavonice"], [],
     [(951001, "1", "register B", [("B", "1782-1850")], ["Slavonice"], [], "", 2), (951002, "2", "register M", [("M", "1782-1850")], ["Slavonice"], [], "", 1)]),
    (960001, "1nba", "Volary", "Volary", "Český Krumlov", ["Volary"], [], [(961001, "1", "register D", [("D", "1950-1960")], ["Volary"], [], "", 1)]),
    (970001, "1nca", "Volary", "Volary", "Český Krumlov", ["Volary"], [], [(971001, "1", "register B", [("B", "1925-1940")], ["Volary"], [], "", 1)]),
]
BY_PARISH = {p[0]: p for p in PARISHES}
BOOKS = {b[0]: (p, b) for p in PARISHES for b in p[7]}


# Branches with units other than registers. Node: code -> label (the parent is the code without its last character). Entities:
# code -> (id, label, name of the fund, localities, rows). Row: (id, number in order, inventory number, signature, title, dating, storage, scans).
NODES = {
    "1q": "Land Registers", "1qa": "SOA v Třeboni - oddělení Třeboň", "1qab": "B", "1qak": "K", "1qe": "SOkA České Budějovice", "1qeh": "H",
    "1t": "Seigniorial Registers", "1ta": "SOA v Třeboni - oddělení Třeboň", "1tab": "B",
    "1U": "Urbariums", "1Ua": "SOA v Třeboni - oddělení Třeboň", "1Uab": "B",
    "1s": "Census", "1se": "SOkA České Budějovice", "1sec": "1869", "1sed": "1880", "1sec9": "Soudní okres České Budějovice",
    "1seca": "Soudní okres Hluboká nad Vltavou", "1sec9c": "C/Č", "1secaz": "Z/Ž", "1sf": "SOkA Český Krumlov", "1sfc": "1869", "1sfc9": "Soudní okres Volary",
    "1sfc9v": "V",
}
ENTITIES = {
    "1qabl": (700001, "Blatná", "Velkostatek Blatná, (1327) 1503-1948", ["Blatná", "Buzice", "Lom"],
              [(720001, "1", "3", "", "Kniha smluv na koupě a prodeje panství Blatná", "1695–1715", "kniha 3", 2),
               (720002, "2", "5", "I B 5", "Kniha popsání polí a luk na panství Blatná", "asi 1700", "kniha 5", 1),
               (720003, "3", "6", "", "Kniha opisů emfyteutických smluv", "(1700) 1787–1857", "kniha 6", 2),
               (720004, "4", "7", "", "Kniha pronájmů jednotlivých pozemků", "1908", "kniha 9", 1),
               (720005, "5", "8", "", "Kniha, která není zveřejněna", "1910", "kniha 10", 0)]),
    "1qabo": (700002, "Borovany", "Velkostatek Borovany", ["Borovany"], [(720011, "1", "1", "", "Gruntovní kniha Borovany", "1750–1800", "kniha 1", 2)]),
    "1qakr": (700003, "Křemže", "Městský archiv Křemže", ["Křemže"], [(720021, "1", "1", "", "Pozemková kniha města Křemže", "", "kniha 1", 1)]),
    "1qehl": (700004, "Hluboká nad Vltavou", "Archiv města Hluboká nad Vltavou", ["Hluboká nad Vltavou"],
              [(720031, "1", "2", "", "Pozemková kniha Hluboká", "1800–1850", "kniha 2", 1)]),
    "1tabl": (710001, "Blatná", "Vrchnostenský úřad Blatná", ["Blatná"], [(730001, "1", "1", "", "Urbář panství Blatná", "1600–1650", "kniha 1", 1)]),
    "1Uabl": (711001, "Blatná", "Urbáře panství Blatná", ["Blatná"], [(731001, "1", "1", "", "Urbář Blatná 1580", "1580", "kniha 1", 1)]),
    "1sec9cx": (800001, "České Budějovice", "Sčítání lidu 1869, okres České Budějovice", ["České Budějovice"],
                [(810001, "1", "", "B I čp. 7", "České Budějovice, čp. 1-250", "", "kniha 1", 2), (810002, "2", "", "", "České Budějovice, čp. 251-600", "", "kniha 2", 2)]),
    "1secazl": (800002, "Zliv", "Sčítání lidu 1869, okres Hluboká nad Vltavou", ["Zliv"], [(810011, "1", "", "", "Zliv, čp. 1-90 a 100-120", "", "kniha 1", 1)]),
    "1sfc9vo": (800003, "Volary", "Sčítání lidu 1869, okres Volary", ["Volary"], [(810021, "1", "", "", "Volary", "", "kniha 1", 1)]),
}
CENSUS_YEAR = {"1sec": "1869", "1sfc": "1870"}   # the Volary page states another year than its tree node: the dating turns uncertain
UNIT_SCANS = {row[0]: row[7] for e in ENTITIES.values() for row in e[4] if row[7]}


def scans_of(book_id: int) -> int:
    return UNIT_SCANS[book_id] if book_id in UNIT_SCANS else BOOKS[book_id][1][7]


def image_id(book_id: int, n: int) -> int:
    """Not monotone in n, like the live application's ids."""
    return 500_000_000 + (book_id % 100_000) * 100 + (n * 7) % 97


def wrap(inner: str, extra_head: str = "") -> str:
    return ("<!DOCTYPE HTML>\n<HTML><HEAD>\n<META content=\"text/html; charset=utf-8\" http-equiv=Content-Type>\n"
            f"<title>SOA v Třeboni - DIGITÁLNÍ ARCHIV</title>{extra_head}</HEAD><body>{inner}</body></HTML>")


def _node(node_id: str, onclick: str, label: str, cls: str = "p_p") -> str:
    return (f"<div id='pt{node_id}' class='tree_pom {cls}' onclick='{onclick}'><div style='width:0px'></div>"
            f"<i class='fas fa-caret-right'></i>{html.escape(label)}&nbsp;&nbsp;&nbsp;</div>")


def tree_top() -> str:
    """The upper part of the tree that every tree page repeats (unrelated nodes the parsers must ignore)."""
    return (_node("10", 'ope("","tree.php?menu=0&doctree=10")', "Introduction")
            + _node("1n", 'ope("","tree.php?menu=0&doctree=1n")', "Parish registers", "p_p_o")
            + _node("1nb", 'ope("","tree.php?menu=0&doctree=1nb")', "Churchless/Civil Registers")
            + _node("1nr", 'ope("","tree.php?menu=0&doctree=1nr")', "Roman Catholic Church", "p_p_o")
            + _node("1nrA", 'ope("","tree.php?menu=0&doctree=1nrA")', "Map of the parish districts of the R-C Church (1948)"))


def letters_page() -> str:
    nodes = "".join(_node(f"1nr{code}", f'ope("","tree.php?menu=0&doctree=1nr{code}")', label) for code, label in LETTERS)
    return wrap(f"<div id='menu'>{tree_top()}{nodes}<div id='vypln'></div></div>")


def letter_page(code: str) -> str:
    nodes = ""
    for code_, label in LETTERS:
        if code_ != code:
            nodes += _node(f"1nr{code_}", f'ope("","tree.php?menu=0&doctree=1nr{code_}")', label)
            continue
        nodes += _node(f"1nr{code_}", f'toggle("1nr{code_}")', label, "p_p_s")
        for pid, tree, english, *_ in (p for p in PARISHES if p[1].startswith(f"1nr{code}")):
            nodes += _node(tree, f'ope("top.","DA?menu=0&doctree={tree}&id={pid}")', english)
    return wrap(f"<div id='menu'>{tree_top()}{nodes}<div id='vypln'></div></div>")


def entities(names: list[str], span: bool = False) -> str:
    tag = "span" if span else "a"
    out = []
    for i, name in enumerate(names):
        attrs = ("class=\"entita\" data-tipped-options=\"ajax: {data:{id:'%d'}}\"" % (400000 + i)
                 + ("" if span else f" target=\"_blank\" href=\"/k/{400000 + i}\""))
        out.append(f"<{tag} {attrs}>{html.escape(name)}</{tag}>")
    return ", ".join(out)


def _row(label: str, value_html: str) -> str:
    return (f"<tr><td align=left width=1% valign=top class='popis3b_nadpis' nowrap>{label} &nbsp;&nbsp;</td>"
            f"<td align=left class='popis3b' valign=top colspan=3>{value_html}</td></tr>")


def book_row(parish: tuple, book: tuple) -> str:
    bid, number, label, types, localities, other, note, scans = book
    if scans > 1900:            # not online yet: the overview lists the book without a link
        cells = "".join(f"<tr><td>{t}</td><td nowrap=\"\">{y}</td></tr>" for t, y in types)
        return (f"<tr class=\"p_m_p0\" id=\"{bid}\" title=\"This parish register will be available online after {scans}\">"
                f"<td class=\"m_k_t0\" width=\"15\">{number}</td><td class=\"m_k_t0\" nowrap=\"\">[{label}]</td>"
                f"<td class=\"m_k_t0\"><table border=\"0\" class=\"m_k_t0\">{cells}</table></td><td class=\"m_k_t0\">{scans}</td>"
                f"<td class=\"m_k_t0\">{html.escape(', '.join(localities))}</td><td class=\"m_k_t0\">{html.escape(note)}</td></tr>")
    go = f"onclick=\"top.location.href='DA?menu=3&amp;id={bid}'\" title=\"Open\""
    cells = "".join(f"<tr><td>{t}</td><td nowrap=\"\">{y}</td></tr>" for t, y in types)
    icon = ("<a class=\"tooltip-right\" data-tooltip=\"electronic name index\" title=\"\"><span class=\"fa-stack\"></span></a>"
            if note == "s indexem" else "&nbsp;")
    kept = f"<span class=\"mat_vedena\">{entities(localities, True)}</span>" if localities else ""
    others = f"<span class=\"mat_nevedena\"> {entities(other, True)}</span>" if other else ""
    return (f"<tr class=\"p_m_p\" id=\"{bid}\" name=\"{bid}\">"
            f"<td class=\"m_k_t\" nowrap=\"\" {go} valign=\"top\">{number}</td>"
            f"<td class=\"m_k_t\" nowrap=\"\" {go} valign=\"top\">[{label}]</td>"
            f"<td class=\"m_k_t\" {go} valign=\"top\" width=\"1%\">{icon}</td>"
            f"<td class=\"m_k_t\" nowrap=\"\" {go} valign=\"top\" width=\"95\"><table border=\"0\" class=\"m_k_t\">{cells}</table></td>"
            f"<td class=\"m_k_t c_n\" valign=\"top\"><div class=\"clickboxblank zapisymk\" id=\"zap{bid}\"><input id=\"inp{bid}\" type=\"checkbox\"/>"
            f"<label for=\"inp{bid}\"></label><div onclick='sh(\"{bid}\")'>{kept}{others}</div></div></td>"
            f"<td class=\"m_k_t c_n\" valign=\"top\">{html.escape(note)}</td>"
            f"<td class=\"m_k_t c_n\" valign=\"top\"></td></tr>")


def parish_page(parish_id: int) -> str:
    pid, tree, english, czech, district, area, other, books = BY_PARISH[parish_id]
    head = (f"<div class='nadpis_wrap'><div class='nadpis' style='display: inline-block;' title='{html.escape(czech)}'>{html.escape(czech)}</div></div>"
            "<table border=0 width='100%' class='tab_popis'>"
            + _row("Parish office", "Roman Catholic Church") + _row("District", html.escape(district))
            + _row("Původce", f"<a class='entita' href='/k/596263'>Farní úřad {html.escape(czech)}</a>")
            + _row("Parish district (1862)", entities(area)) + _row("Other localities", f"<div id='ostatni'><div>{entities(other)}</div></div>")
            + _row("HTML link", f"https://{HOST}/{pid}<button class='btn' data-clipboard-text=\"https://{HOST}/{pid}\" type='button'></button>")
            + "</table><br>")
    table = ("<table class='box_nadpis' cellspacing=0 width='100%'><tr><td align=center valign=top class='m_k_n' colspan=3>Book</td>"
             "<td valign=top nowrap class='m_k_n'>Time extent</td><td align=center valign=top class='m_k_n'>Book for | "
             "<span style='font-weight:normal'>Other localities</span></td><td align=center colspan='2' valign=top class='m_k_n'>Note</td></tr>"
             + "".join(book_row(BY_PARISH[pid], b) for b in books) + "</table>")
    return wrap(f"<div id='menu'>{tree_top()}{_node(tree, 'x', english, 'p_p_s')}</div>{head}{table}")


def confession_page(root: str) -> str:
    nodes = _node(root, f'toggle("{root}")', CONFESSION_ROOTS[root], "p_p_s")
    for pid, tree, english, *_ in (p for p in PARISHES if p[1].startswith(root) and p[1] != root):
        nodes += _node(tree, f'ope("top.","DA?menu=0&doctree={tree}&id={pid}")', english)
    return wrap(f"<div id='menu'>{tree_top()}{nodes}<div id='vypln'></div></div>")


def section_page(code: str) -> str:
    """A node of the land, seigniorial, urbarial or census branch: its children (groups and entities with ids)."""
    nodes = _node(code, f'toggle("{code}")', NODES[code], "p_p_s")
    for child, label in NODES.items():
        if len(child) == len(code) + 1 and child.startswith(code):
            nodes += _node(child, f'ope("","tree.php?menu=0&doctree={child}")', label)
    for child, (eid, label, *_) in ENTITIES.items():
        if child.startswith(code) and len(child) == len(code) + 1:
            nodes += _node(child, f'ope("top.","DA?menu=0&doctree={child}&id={eid}")', label)
    return wrap(f"<div id='menu'>{tree_top()}{nodes}<div id='vypln'></div></div>")


HEADER = ("<tr class=\"header\">" + "".join(
    f"<th id=\"{col}\">{label}<div id=\"d{col}\">{label}</div></th>" for col, label in (
        ("col_poradove_cislo", "Pořadové číslo"), ("col_inventarni_cislo", "Inventární číslo"), ("col_signatura", "Signatura"),
        ("col_nazev", "Název"), ("col_datace_od", "Datace"), ("col_ukladaci_jednotka", "Ukládací jednotka"), ("col_zapisy_pro", "Lokality"))) + "</tr>")


CENSUS_HEADER = ("<tr class=\"header\">" + "".join(
    f"<th id=\"col_\">{label}<div id=\"dcol_\">{label}</div></th>" for label in
    ("Inventární číslo<br/>Refer. označení", "Signatura", "Sčítací obec", "Obsah", "Ukládací<br/>jednotka", "")) + "</tr>")


def unit_page(code: str) -> str:
    """A fund/estate page (land branches: header cells with column ids, closed rows) or a census municipality page (as the live one:
    every header id is just "col_", columns known by their text, unclosed rows opened by `top.location.href='DA?menu=3&id=..'`)."""
    eid, label, fund, localities, rows = ENTITIES[code]
    census = code.startswith("1s")
    head = (f"<div class='nadpis_wrap'><div class='nadpis' style='display: inline-block;'>{html.escape(label)}</div></div>"
            "<table border=0 width='100%' class='tab_popis'>"
            + (_row("Rok", CENSUS_YEAR[code[:4]]) + _row("Okres", html.escape(fund)) if census else
               _row("Název archivního souboru", f"<a href='/f/21200001000390' title='Archivní soubor'>{html.escape(fund)}</a>")
               + _row("Lokality", f"<div class='clickboxblank' id='knihy'><div>{entities(localities)}</div></div>")
               + _row("Počet knih", str(len(rows))))
            + _row("HTML odkaz", f"https://{HOST}/{eid}<button class='btn' data-clipboard-text=\"https://{HOST}/{eid}\" type='button'></button>")
            + "</table><br>")
    body = ""
    for rid, order, inv, sign, title, dating, storage, scans in rows:
        if census:
            go = f" onclick=\"top.location.href='DA?menu=3&amp;id={rid}'\"" if scans else ""
            body += (f"<tr id='{rid}' title='Otevřít'><td class=\"center\"{go}>{inv}</td><td class=\"nowrap\"{go}>{sign} </td>"
                     f"<td class=\"nowrap center\"{go}> </td><td{go}>{html.escape(title)}</td><td class=\"center\"{go}>{storage}</td>"
                     "<td style=\"white-space:nowrap\"></td>")            # the live table leaves its rows unclosed
            continue
        go = f" onclick=\"o({rid})\" title=\"Otevřít\"" if scans else ""
        body += (f"<tr id=\"{rid}\"><td class=\"center\"{go}>{order}</td><td class=\"center\"{go}>{inv}</td><td{go}>{sign}</td>"
                 f"<td{go}>{html.escape(title)}</td><td class=\"center\"{go}>{dating}</td><td class=\"center\"{go}>{storage}</td>"
                 f"<td><div id=\"obce{rid}\"><div onclick=\"h_o({rid});\">Seznam lokalit</div></div></td><td width=\"1%\"></td></tr>")
    return wrap(f"<div id='menu'>{tree_top()}</div>{head}<table class='tab_knihy'>{CENSUS_HEADER if census else HEADER}{body}</table>")


def viewer_page(book_id: int, n: int) -> str:
    total = scans_of(book_id)
    options = "".join(f"<option value='{k}'{' selected' if k == n else ''}>Image {k}</option>" for k in range(1, total + 1))
    thumbs = "".join(
        f"<div style=\\'background: #000000;background-image: url(\\\"/cgi-bin/isrv6.cgi?data1/{image_id(book_id, k)}.0.thumb\\\");\\' "
        f"title=\\'{k}\\' onclick=\\'page_go({k});return false;\\'  class=\\'nahled_viewer\\'></div>"
        for k in range(max(1, n - 1), min(total, n + 1) + 1))
    return wrap(
        f"<div id='stranka1'><form name='formular' action='view.php' method=get><input type=hidden name='doctree' value='1nrcl'>"
        f"<input type=hidden name='id' value='{book_id}'><input type=hidden name='menu' value='3'>"
        f"<select name='page' id='page' class='prohlizec_vstup_select'>{options}</select></form></div>"
        "<script language=\"JavaScript\" type=\"text/javascript\">\n"
        f"var urlposinit=true;if (typeof (window.history.pushState) != 'undefined') {{ top.window.history.pushState(null,null,\"/{book_id}/{n}\");}}\n"
        f"Z.showImage(\"myContainer\",\"data1/{image_id(book_id, n)}\",{{ \"zMeasureVisible\":\"1\",\"zSkinPath\":\"Assets/Skins/Martin\"+cesta}});\n"
        f"new popUp(coothm.x,coothm.y,140,423,\"thumbs\",\"Thumbnails\",\"{thumbs}\",\"winview\",false);\n</script>")


class DemoTrebon:
    def __init__(self):
        self.log: list[tuple[str, str]] = []        # (path, query) of every request to the synthetic archive
        self.robots = ROBOTS

    def handle(self, request: httpx.Request) -> httpx.Response:
        path, raw = request.url.path, request.url.query.decode()
        self.log.append((path, raw))
        if request.method != "GET":
            return httpx.Response(405)
        if path == "/robots.txt":
            return httpx.Response(200, text=self.robots, headers={"content-type": "text/plain"})
        query = {k: v[0] for k, v in parse_qs(raw).items()}
        if path == "/tree.php":
            doctree = query.get("doctree", "")
            if doctree == "1nr":
                return httpx.Response(200, text=letters_page(), headers=HTML)
            if doctree in CONFESSION_ROOTS and "id" not in query:
                return httpx.Response(200, text=confession_page(doctree), headers=HTML)
            if doctree in NODES and "id" not in query:
                return httpx.Response(200, text=section_page(doctree), headers=HTML)
            if doctree in ENTITIES and query.get("id") == str(ENTITIES[doctree][0]):
                return httpx.Response(200, text=unit_page(doctree), headers=HTML)
            if re.fullmatch(r"1nr[a-z]", doctree) and doctree[3] in dict(LETTERS):
                return httpx.Response(200, text=letter_page(doctree[3]), headers=HTML)
            pid = int(query["id"]) if query.get("id", "").isdigit() else None
            if pid in BY_PARISH and BY_PARISH[pid][1] == doctree:
                return httpx.Response(200, text=parish_page(pid), headers=HTML)
            return httpx.Response(200, text=wrap("<div class='chyba'>The page does not exist</div>"), headers=HTML)
        if path == "/view.php":
            book, n = int(query.get("id", 0)), int(query.get("page", 0))
            if (book not in BOOKS and book not in UNIT_SCANS) or scans_of(book) > 1900 or not 1 <= n <= scans_of(book):
                return httpx.Response(404)
            return httpx.Response(200, text=viewer_page(book, n), headers=HTML)
        return httpx.Response(404, text="Synthetic fixture has no such endpoint")
