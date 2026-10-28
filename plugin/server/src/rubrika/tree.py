"""Family tree export: GEDCOM 5.5.1 and Mermaid from facts that cite saved findings. Pure, no I/O."""
from __future__ import annotations

import re
import unicodedata
from datetime import date

from . import __version__
from .errors import ResearchError
from .models import FamilyTree, TreeDate, TreeEvent, TreePerson

MONTHS=("JAN","FEB","MAR","APR","MAY","JUN","JUL","AUG","SEP","OCT","NOV","DEC")
QUALIFIERS={"exact":"","about":"ABT ","before":"BEF ","after":"AFT "}
CZECH_QUALIFIERS={"exact":"","about":"asi ","before":"před ","after":"po "}
TAGS={"birth":"BIRT","baptism":"CHR","marriage":"MARR","death":"DEAT","burial":"BURI",
      "occupation":"OCCU","residence":"RESI"}
QUAY={"documented":3,"probable":2,"hypothesis":1}
UNDOCUMENTED_NOTE={"probable":"Nedoloženo (pravděpodobný údaj).","hypothesis":"Hypotéza, nedoloženo."}
CITABLE_KINDS=("record","index_entry")
CHUNK=200
OPEN_ENDS_MAX=30


def gedcom_date(d:TreeDate) -> str:
    parts=[str(d.day)] if d.day else []
    if d.month: parts.append(MONTHS[d.month-1])
    parts.append(str(d.year))
    return QUALIFIERS[d.qualifier]+" ".join(parts)


def czech_date(d:TreeDate) -> str:
    if d.day: value=f"{d.day}. {d.month}. {d.year}"
    elif d.month: value=f"{d.month}/{d.year}"
    else: value=str(d.year)
    return CZECH_QUALIFIERS[d.qualifier]+value


def _clean(text:str) -> str:
    return "".join(" " if unicodedata.category(c)[0]=="C" and c!="\n" else c
                   for c in text.replace("\r\n","\n").replace("\r","\n")).replace("@","@@")


def gedcom_text(level:int,tag:str,text:str) -> list[str]:
    """One value over CONT (new line) and CONC (continuation); CONC never splits next to a space."""
    out=[]
    for i,para in enumerate(_clean(text).split("\n")):
        para=para.rstrip()
        chunks=[]
        while len(para)>CHUNK:
            cut=CHUNK
            while cut>CHUNK//2 and (para[cut-1]==" " or para[cut]==" " or para[cut-1]=="@"):
                cut-=1
            chunks.append(para[:cut])
            para=para[cut:]
        chunks.append(para)
        for j,chunk in enumerate(chunks):
            if i==0 and j==0: out.append(f"{level} {tag} {chunk}".rstrip())
            else: out.append(f"{level+1} {'CONC' if j else 'CONT'} {chunk}".rstrip())
    return out


def _name(p:TreePerson) -> str:
    return " ".join(x for x in (p.given_name,p.surname) if x)


def _events(tree:FamilyTree):
    for p in tree.persons:
        for e in p.events: yield _name(p),e
    names={p.id:_name(p) for p in tree.persons}
    for f in tree.families:
        if f.marriage: yield " × ".join(names[x] for x in (f.husband,f.wife) if x),f.marriage


def validate(tree:FamilyTree,observations:dict):
    def bad(message): raise ResearchError("INVALID_ARGUMENT",message)
    persons={p.id for p in tree.persons}
    if len(persons)!=len(tree.persons): bad("Id osob musí být jedinečná.")
    if len({f.id for f in tree.families})!=len(tree.families): bad("Id rodin musí být jedinečná.")
    if tree.root_person not in persons: bad("root_person neodkazuje na žádnou osobu.")
    parents={}
    for f in tree.families:
        members=[x for x in (f.husband,f.wife) if x]+f.children
        if any(m not in persons for m in members): bad(f"Rodina {f.id} odkazuje na neexistující osobu.")
        if len(set(members))!=len(members): bad(f"Osoba je v rodině {f.id} vícekrát.")
        for c in f.children:
            if c in parents: bad(f"Osoba {c} je dítětem ve dvou rodinách.")
            parents[c]=[x for x in (f.husband,f.wife) if x]
    state={}
    def visit(pid):
        if state.get(pid)==1: bad("Rodokmen obsahuje cyklus: osoba je svým vlastním předkem.")
        if state.get(pid)==2: return
        state[pid]=1
        for parent in parents.get(pid,()): visit(parent)
        state[pid]=2
    for pid in persons: visit(pid)
    for who,e in _events(tree):
        kinds=[]
        for ref in e.sources:
            o=observations.get((ref.research_id,ref.observation_id))
            if o is None:
                raise ResearchError("NOT_FOUND",f"Zápis {ref.observation_id} není uložen v hledání {ref.research_id}.")
            if o["kind"] not in CITABLE_KINDS or not o.get("evidence"):
                bad(f"{who}: citovat lze jen přepis zápisu nebo indexu s obrazovým dokladem.")
            kinds.append(o["kind"])
        if e.confidence=="documented" and "record" not in kinds:
            bad(f"{who}: údaj „{e.type}“ označený jako doložený musí citovat přepis zápisu (record), ne jen index.")


def _documented(e:TreeEvent|None) -> bool:
    return bool(e) and e.confidence=="documented"


def documented_only(tree:FamilyTree) -> tuple[FamilyTree,list[str]]:
    """Drop undocumented facts; keep undocumented persons only as name-only links between documented generations."""
    warnings=[]
    names={p.id:_name(p) for p in tree.persons}
    parents={}
    for f in tree.families:
        for c in f.children: parents[c]=[x for x in (f.husband,f.wife) if x]
    has_fact={p.id for p in tree.persons if any(_documented(e) for e in p.events)}
    has_fact|={x for f in tree.families if _documented(f.marriage) for x in (f.husband,f.wife) if x}
    def ancestors(pid):
        for parent in parents.get(pid,()):
            yield parent
            yield from ancestors(parent)
    line={tree.root_person,*ancestors(tree.root_person)}
    keep=set(has_fact)|{p for p in line if p in has_fact or any(a in has_fact for a in ancestors(p))}
    persons=[]
    for p in tree.persons:
        if p.id not in keep:
            warnings.append(f"Vynechána osoba {names[p.id]}: nemá doložený údaj.")
            continue
        for e in p.events:
            if not _documented(e): warnings.append(f"Vynechán údaj {names[p.id]}: {e.type} ({e.confidence}).")
        persons.append(p.model_copy(update={"events":[e for e in p.events if _documented(e)]}))
    families=[]
    for f in tree.families:
        husband,wife=(x if x in keep else None for x in (f.husband,f.wife))
        children=[c for c in f.children if c in keep]
        if not (husband or wife) or len([x for x in (husband,wife) if x])+len(children)<2: continue
        marriage=f.marriage if _documented(f.marriage) else None
        if f.marriage and not marriage:
            warnings.append(f"Vynechán údaj rodiny {f.id}: marriage ({f.marriage.confidence}).")
        families.append(f.model_copy(update={"husband":husband,"wife":wife,"children":children,"marriage":marriage}))
    return tree.model_copy(update={"persons":persons,"families":families}),warnings


def _age(age:str|None) -> tuple[str|None,str|None]:
    if not age: return None,None
    if re.fullmatch(r"\d{1,3}",age.strip()): return age.strip()+"y",None
    return None,"Věk: "+age


def _event_lines(e:TreeEvent,level:int,sources:dict,observations:dict,media:dict) -> tuple[list[str],int]:
    tag=TAGS[e.type]
    head=gedcom_text(level,tag,e.value if e.type=="occupation" and e.value else "")
    body=[]
    if e.date: body.append(f"{level+1} DATE {gedcom_date(e.date)}")
    if e.place: body+=gedcom_text(level+1,"PLAC",e.place)
    age,age_note=_age(e.age)
    if age: body.append(f"{level+1} AGE {age}")
    if e.cause: body+=gedcom_text(level+1,"CAUS",e.cause)
    notes=[x for x in (e.value if e.type!="occupation" else None,age_note,UNDOCUMENTED_NOTE.get(e.confidence),e.note) if x]
    for note in notes: body+=gedcom_text(level+1,"NOTE",note)
    for ref in e.sources:
        o=observations[(ref.research_id,ref.observation_id)]
        ev=o["evidence"]
        page=(ev.get("scan_label") or f"snímek {ev['scan_number']}")+(f", {o['historical_page']}" if o.get("historical_page") else "")
        body.append(f"{level+1} SOUR @{sources[ev['book_id']]}@")
        body+=gedcom_text(level+2,"PAGE",page)
        body.append(f"{level+2} QUAY {QUAY[e.confidence]}")
        if o.get("text"):
            body.append(f"{level+2} DATA")
            body+=gedcom_text(level+3,"TEXT",o["text"])
        user=ev.get("provenance")=="user_supplied"
        if user: body+=gedcom_text(level+2,"NOTE","Snímek dodal uživatel, odkaz neověřen.")
        if ev.get("citation"): body+=gedcom_text(level+2,"NOTE",ev["citation"])
        if ev["viewer_url"]: body+=gedcom_text(level+2,"NOTE",("Odkaz od uživatele (neověřen): " if user else "")+ev["viewer_url"])
        crop=media.get((ref.research_id,ref.observation_id))
        if crop:
            body+=[f"{level+2} OBJE",*gedcom_text(level+3,"FILE",crop),f"{level+4} FORM jpg",
                   *gedcom_text(level+3,"TITL","Výřez zápisu, "+page)]
    if not (e.date or e.place) and e.type not in ("occupation","residence"): head[0]+=" Y"
    return [*head,*body],len(e.sources)


def to_gedcom(tree:FamilyTree,observations:dict,books:dict,*,variant:str,source_mode:str,today:date,
              media:dict|None=None) -> tuple[str,int]:
    """media: {(research_id, observation_id): relative path of the saved crop}; only a package written to disk has one."""
    media=media or {}
    xref={p.id:f"I{i}" for i,p in enumerate(tree.persons,1)}
    fref={f.id:f"F{i}" for i,f in enumerate(tree.families,1)}
    sources={}
    for _,e in _events(tree):
        for ref in e.sources:
            book=observations[(ref.research_id,ref.observation_id)]["evidence"]["book_id"]
            sources.setdefault(book,f"S{len(sources)+1}")
    note=(f"{tree.title}. Rubrika {__version__}, varianta: "
          f"{'jen doložené údaje' if variant=='documented_only' else 'všechny údaje včetně nedoložených'}. "
          "Přepisy a totožnost osob jsou modelové interpretace, nejsou lidsky ověřené.")
    if source_mode=="synthetic_demo":
        note+=" SYNTETICKÁ TESTOVACÍ DATA, NEJDE O NÁLEZ V ARCHIVU."
    lines=["0 HEAD","1 SOUR RUBRIKA","2 NAME Rubrika",f"2 VERS {__version__}",
           f"1 DATE {today.day} {MONTHS[today.month-1]} {today.year}","1 GEDC","2 VERS 5.5.1",
           "2 FORM LINEAGE-LINKED","1 CHAR UTF-8","1 LANG Czech",*gedcom_text(1,"NOTE",note)]
    citations=0
    fams={p.id:[] for p in tree.persons}
    famc={}
    for f in tree.families:
        for x in (f.husband,f.wife):
            if x: fams[x].append(fref[f.id])
        for c in f.children: famc[c]=fref[f.id]
    for p in tree.persons:
        given,surname=(_clean(x or "").replace("/","-") for x in (p.given_name,p.surname))
        lines+=[f"0 @{xref[p.id]}@ INDI",f"1 NAME {given} /{surname}/".replace("NAME  /","NAME /")]
        if given: lines.append(f"2 GIVN {given}")
        if surname: lines.append(f"2 SURN {surname}")
        lines.append(f"1 SEX {p.sex}")
        for e in p.events:
            block,n=_event_lines(e,1,sources,observations,media)
            lines+=block; citations+=n
        if p.note: lines+=gedcom_text(1,"NOTE",p.note)
        if p.id in famc: lines.append(f"1 FAMC @{famc[p.id]}@")
        lines+=[f"1 FAMS @{x}@" for x in fams[p.id]]
    for f in tree.families:
        lines.append(f"0 @{fref[f.id]}@ FAM")
        if f.husband: lines.append(f"1 HUSB @{xref[f.husband]}@")
        if f.wife: lines.append(f"1 WIFE @{xref[f.wife]}@")
        lines+=[f"1 CHIL @{xref[c]}@" for c in f.children]
        if f.marriage:
            block,n=_event_lines(f.marriage,1,sources,observations,media)
            lines+=block; citations+=n
    repos={}  # archive name -> R<n>, in order of first use
    for book,sid in sources.items():
        info=books.get(book) or {}
        name=info.get("archive") or "Moravský zemský archiv v Brně"
        rid=repos.setdefault(name,f"R{len(repos)+1}")
        lines.append(f"0 @{sid}@ SOUR")
        lines+=gedcom_text(1,"TITL",info.get("title") or f"Kniha {book}")
        lines+=gedcom_text(1,"AUTH",name)
        lines.append(f"1 REPO @{rid}@")
        if info.get("url"): lines+=gedcom_text(1,"NOTE",("Odkaz od uživatele (neověřen): " if info.get("url_unverified") else "")+info["url"])
        if info.get("note"): lines+=gedcom_text(1,"NOTE",info["note"])
    for name,rid in repos.items():
        lines.append(f"0 @{rid}@ REPO")
        lines+=gedcom_text(1,"NAME",name)
    lines.append("0 TRLR")
    return "\n".join(lines)+"\n",citations


def _label(text:str) -> str:
    return (text.replace("&","#amp;").replace('"',"#quot;").replace("<","#lt;").replace(">","#gt;")
            .replace("\n"," "))


def _year(events,types,sign) -> str|None:
    for t in types:
        for e in events:
            if e.type==t and e.date:
                return sign+" "+CZECH_QUALIFIERS[e.date.qualifier]+str(e.date.year)
    return None


def to_mermaid(tree:FamilyTree) -> str:
    node={p.id:f"P{i}" for i,p in enumerate(tree.persons,1)}
    documented={p.id for p in tree.persons if any(_documented(e) for e in p.events)}
    documented|={x for f in tree.families if _documented(f.marriage) for x in (f.husband,f.wife) if x}
    out=["flowchart TB",f"  %% {_label(tree.title)}"]
    for p in tree.persons:
        years=" ".join(x for x in (_year(p.events,("birth","baptism"),"*"),_year(p.events,("death","burial"),"†")) if x)
        label=_label(_name(p))+("<br/>"+years if years else "")
        cls="root" if p.id==tree.root_person else ("documented" if p.id in documented else "undocumented")
        out.append(f'  {node[p.id]}["{label}"]:::{cls}')
    for i,f in enumerate(tree.families,1):
        when=f" {f.marriage.date.year}" if f.marriage and f.marriage.date else ""
        out.append(f'  F{i}(("⚭{when}"))')
        out+=[f"  {node[x]} --- F{i}" for x in (f.husband,f.wife) if x]
        out+=[f"  F{i} --> {node[c]}" for c in f.children]
    out+=["  classDef documented stroke-width:2px",
          "  classDef undocumented stroke-dasharray:5 5",
          "  classDef root stroke-width:3px,font-weight:bold"]
    return "\n".join(out)


def _anchor(p:TreePerson,tree:FamilyTree) -> dict|None:
    """The person's earliest dated event, their marriage included: where the next search starts."""
    dated=[e for e in p.events if e.date]
    dated+=[f.marriage for f in tree.families if p.id in (f.husband,f.wife) and f.marriage and f.marriage.date]
    if not dated: return None
    e=min(dated,key=lambda e:e.date.year)
    return {"event":e.type,"year":e.date.year,"place":e.place,"confidence":e.confidence}


def open_ends(tree:FamilyTree) -> list[dict]:
    """Where the direct line stops: the root and its ancestors without a documented birth or baptism, without
    known parents, or whose parents' marriage is not documented. Deepest generation first."""
    persons={p.id:p for p in tree.persons}
    family_of={c:f for f in tree.families for c in f.children}
    out,seen,queue=[],set(),[(tree.root_person,0,None)]
    while queue:
        pid,generation,child=queue.pop(0)
        if pid in seen: continue
        seen.add(pid)
        p,f=persons[pid],family_of.get(pid)
        missing=[] if any(_documented(e) for e in p.events if e.type in ("birth","baptism")) else ["birth"]
        if not f: missing.append("parents")
        else:
            missing+=[role for role,x in (("father",f.husband),("mother",f.wife)) if not x]
            if not _documented(f.marriage): missing.append("parents_marriage")
        if missing:
            anchor=_anchor(p,tree)
            out.append({"person":_name(p),"person_id":pid,"generation":generation,"missing":missing,
                        **({"anchor":anchor} if anchor else {"known_from_child":child} if child else {})})
        if f: queue+=[(x,generation+1,_name(p)) for x in (f.husband,f.wife) if x]
    return sorted(out,key=lambda x:-x["generation"])


def prepare(tree:FamilyTree,observations:dict,variant:str) -> tuple[FamilyTree,list[str],list[dict]]:
    """Validate, note where the line ends (on the whole tree) and apply the variant."""
    validate(tree,observations)
    ends=open_ends(tree)
    warnings=[]
    if variant=="documented_only": tree,warnings=documented_only(tree)
    return tree,warnings,ends


def summary(tree:FamilyTree,citations:int,warnings:list[str],ends:list[dict]) -> dict:
    return {"persons":len(tree.persons),"families":len(tree.families),"citations":citations,
            "undocumented":[{"person":who,"event":e.type,"confidence":e.confidence}
                            for who,e in _events(tree) if not _documented(e)],
            "warnings":warnings,"open_ends":ends[:OPEN_ENDS_MAX],
            **({"open_ends_total":len(ends)} if len(ends)>OPEN_ENDS_MAX else {})}


def _filename(title:str) -> str:
    ascii_=unicodedata.normalize("NFKD",title).encode("ascii","ignore").decode()
    return re.sub(r"[^a-z0-9]+","-",ascii_.lower()).strip("-")[:60].strip("-") or "rodokmen"


def export_family_tree(tree:FamilyTree,observations:dict,books:dict,*,variant:str,source_mode:str,today:date) -> dict:
    """observations: {(research_id, observation_id): saved finding}; books: {book_id: {title, url}}."""
    tree,warnings,ends=prepare(tree,observations,variant)
    gedcom,citations=to_gedcom(tree,observations,books,variant=variant,source_mode=source_mode,today=today)
    return {"variant":variant,"gedcom":gedcom,"mermaid":to_mermaid(tree),
            "suggested_filename":_filename(tree.title),"summary":summary(tree,citations,warnings,ends)}
