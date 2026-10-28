"""Family package on the user's own disk: GEDCOM with the cited crops, one self-contained HTML page, and a ZIP of all."""
from __future__ import annotations

import base64
import html
import zipfile
from datetime import date
from pathlib import Path

from . import __version__
from .models import FamilyTree, TreeEvent
from .tree import _filename, _name, czech_date, prepare, summary, to_gedcom

EVENTS={"birth":"narození","baptism":"křest","marriage":"sňatek","death":"úmrtí","burial":"pohřeb",
        "occupation":"povolání","residence":"bydliště"}
CONFIDENCE={"documented":"doloženo zápisem","probable":"pravděpodobné, nedoloženo","hypothesis":"hypotéza, nedoloženo"}
MISSING={"birth":"doložené narození nebo křest","parents":"rodiče","father":"otec","mother":"matka",
         "parents_marriage":"doložený sňatek rodičů"}
CROPS="vyrezy"


def _cited(tree:FamilyTree):
    for p in tree.persons:
        for e in p.events: yield from e.sources
    for f in tree.families:
        if f.marriage: yield from f.marriage.sources


def _folder(root:Path,name:str,today:date) -> Path:
    """A new folder every time: an earlier package is never overwritten."""
    root.mkdir(parents=True,exist_ok=True)
    for n in range(1,1000):
        folder=root/(f"{name}-{today.isoformat()}"+(f"-{n}" if n>1 else ""))
        if folder.with_name(folder.name+".zip").exists(): continue
        try:
            folder.mkdir()
            return folder
        except FileExistsError:
            continue
    raise FileExistsError(name)


def _line_first(tree:FamilyTree) -> list:
    """The root and its ancestors generation by generation, then everyone else in the given order."""
    persons={p.id:p for p in tree.persons}
    family_of={c:f for f in tree.families for c in f.children}
    order,queue=[],[tree.root_person]
    while queue:
        pid=queue.pop(0)
        if pid in order or pid not in persons: continue
        order.append(pid)
        f=family_of.get(pid)
        if f: queue+=[x for x in (f.husband,f.wife) if x]
    return [persons[x] for x in order]+[p for p in tree.persons if p.id not in order]


def _esc(text) -> str:
    return html.escape(str(text),quote=True)


def _event_html(e:TreeEvent,observations:dict,books:dict,crops:dict,images:dict) -> str:
    head=" · ".join(x for x in (EVENTS[e.type]+(f": {e.value}" if e.value else ""),e.date and czech_date(e.date),e.place,
                               e.age and f"věk {e.age}",e.cause) if x)
    out=[f'<li><strong>{_esc(head)}</strong> <span class="c-{e.confidence}">{_esc(CONFIDENCE[e.confidence])}</span>']
    if e.note: out.append(f"<p>{_esc(e.note)}</p>")
    for ref in e.sources:
        o=observations[(ref.research_id,ref.observation_id)]
        ev=o["evidence"]
        page=(ev.get("scan_label") or f"snímek {ev['scan_number']}")+(f", {o['historical_page']}" if o.get("historical_page") else "")
        title=(books.get(ev["book_id"]) or {}).get("title") or f"Kniha {ev['book_id']}"
        out.append(f'<div class="src"><p>{_esc(title)}, {_esc(page)}</p>')
        crop=crops.get((ref.research_id,ref.observation_id))
        if crop: out.append(f'<img alt="Výřez zápisu" src="data:image/jpeg;base64,{images[crop]}">')
        if o.get("text"): out.append(f"<blockquote>{_esc(o['text'])}</blockquote>")
        if ev.get("provenance")=="user_supplied": out.append("<p>Snímek dodal uživatel, odkaz neověřen.</p>")
        url=ev.get("viewer_url") or ""
        if url.startswith("https://"): out.append(f'<p><a href="{_esc(url)}">{_esc(url)}</a></p>')
        elif url: out.append(f"<p>{_esc(url)}</p>")
        out.append("</div>")
    return "".join(out)+"</li>"


def to_html(tree:FamilyTree,observations:dict,books:dict,crops:dict,images:dict,info:dict,*,variant:str,
            source_mode:str,today:date) -> str:
    names={p.id:_name(p) for p in tree.persons}
    body=[f"<h1>{_esc(tree.title)}</h1>",
          f"<p>Rubrika {_esc(__version__)}, {today.day}. {today.month}. {today.year}; "
          f"{'jen doložené údaje' if variant=='documented_only' else 'všechny údaje včetně nedoložených'}. "
          "Přepisy a totožnost osob jsou modelové interpretace, nejsou lidsky ověřené.</p>"]
    if source_mode=="synthetic_demo": body.append("<p><strong>SYNTETICKÁ TESTOVACÍ DATA, NEJDE O NÁLEZ V ARCHIVU.</strong></p>")
    family_of={c:f for f in tree.families for c in f.children}
    for p in _line_first(tree):
        f=family_of.get(p.id)
        parents=" a ".join(names[x] for x in (f.husband,f.wife) if x) if f else ""
        body.append(f'<section><h2>{_esc(names[p.id])}{" (kořen)" if p.id==tree.root_person else ""}</h2>')
        if parents: body.append(f"<p>Rodiče: {_esc(parents)}</p>")
        if p.note: body.append(f"<p>{_esc(p.note)}</p>")
        if p.events:
            body.append("<ul>"+"".join(_event_html(e,observations,books,crops,images) for e in p.events)+"</ul>")
        body.append("</section>")
    marriages=[f for f in tree.families if f.marriage]
    if marriages:
        body.append("<section><h2>Sňatky</h2><ul>")
        for f in marriages:
            couple=" × ".join(names[x] for x in (f.husband,f.wife) if x)
            body.append(f"<li><strong>{_esc(couple)}</strong><ul>{_event_html(f.marriage,observations,books,crops,images)}</ul></li>")
        body.append("</ul></section>")
    if info["open_ends"]:
        body.append("<section><h2>Kde linie končí</h2><ul>")
        for end in info["open_ends"]:
            missing=", ".join(MISSING[m] for m in end["missing"])
            body.append(f"<li>{_esc(end['person'])}: chybí {_esc(missing)}</li>")
        body.append("</ul></section>")
    if info["undocumented"] or info["warnings"]:
        body.append("<section><h2>Nedoložené údaje a vynechané</h2><ul>")
        body+=[f"<li>{_esc(u['person'])}: {_esc(EVENTS[u['event']])} ({_esc(CONFIDENCE[u['confidence']])})</li>" for u in info["undocumented"]]
        body+=[f"<li>{_esc(w)}</li>" for w in info["warnings"]]
        body.append("</ul></section>")
    return ('<!doctype html><html lang="cs"><meta charset="utf-8">'
            '<meta http-equiv="Content-Security-Policy" content="default-src \'none\'; img-src data:; style-src \'unsafe-inline\'">'
            '<meta name="viewport" content="width=device-width,initial-scale=1">'
            f"<title>{_esc(tree.title)}</title>"
            "<style>body{font:16px/1.5 system-ui,sans-serif;max-width:900px;margin:2rem auto;padding:0 1rem}"
            "img{max-width:100%;border:1px solid #ccc}blockquote{white-space:pre-wrap;margin:.5rem 0;padding-left:1rem;border-left:3px solid #ccc}"
            "section{border-top:1px solid #ddd;margin-top:1.5rem}.src{margin:.5rem 0 1rem 1rem}"
            ".c-probable,.c-hypothesis{color:#8a5a00}.c-documented{color:#1d6b33}</style>"
            +"".join(body)+"</html>")


def write_package(tree:FamilyTree,observations:dict,books:dict,load_crop,*,variant:str,source_mode:str,today:date,
                  root:Path) -> dict:
    """load_crop(research_id, image_id) -> checksum-verified JPEG bytes of the saved evidence."""
    tree,warnings,ends=prepare(tree,observations,variant)
    crops,files,images={},{},{}
    for ref in _cited(tree):
        key=(ref.research_id,ref.observation_id)
        image=(ref.research_id,observations[key]["evidence"]["image_id"])
        if image not in files:
            files[image]=f"{CROPS}/{len(files)+1:03d}.jpg"
            raw=load_crop(*image)
            images[files[image]]=raw
        crops[key]=files[image]
    gedcom,citations=to_gedcom(tree,observations,books,variant=variant,source_mode=source_mode,today=today,media=crops)
    info=summary(tree,citations,warnings,ends)
    name=_filename(tree.title)
    folder=_folder(root,name,today)
    (folder/CROPS).mkdir()
    for path,raw in images.items(): (folder/path).write_bytes(raw)
    (folder/f"{name}.ged").write_text(gedcom,encoding="utf-8",newline="\n")
    encoded={path:base64.b64encode(raw).decode() for path,raw in images.items()}
    page=to_html(tree,observations,books,crops,encoded,info,variant=variant,source_mode=source_mode,today=today)
    (folder/f"{name}.html").write_text(page,encoding="utf-8")
    archive=folder.with_name(folder.name+".zip")
    with zipfile.ZipFile(archive,"x",zipfile.ZIP_DEFLATED) as z:
        for path in sorted(folder.rglob("*")):
            if path.is_file(): z.write(path,f"{folder.name}/{path.relative_to(folder).as_posix()}")
    return {"variant":variant,"folder":str(folder),"zip":str(archive),"gedcom_file":str(folder/f"{name}.ged"),
            "html_file":str(folder/f"{name}.html"),"crops":len(images),"summary":info}
