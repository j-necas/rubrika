import base64
import html
import hashlib
from .errors import ResearchError
import json
from pathlib import Path


def export_report(service,sid:str,owner:str,output:Path,source_mode:str):
    """Operator-side self-contained HTML; no public evidence URL or JavaScript."""
    report=service._report(sid,owner)
    report["source_mode"]=source_mode
    observations=service.store.observations(sid,owner)
    output.parent.mkdir(parents=True,exist_ok=True)
    sections=[]
    for obs in observations:
        evidence=obs.get("evidence")
        image=""
        if evidence:
            saved=service.store.image(sid,owner,evidence["image_id"])
            raw=Path(saved["file"]).read_bytes()
            if hashlib.sha256(raw).hexdigest()!=saved["ref"]["sha256"]:
                raise ResearchError("INCOMPLETE_IMAGE", "Exportovaný obraz neprošel kontrolním součtem.")
            image='<img alt="Zdrojový výřez" src="data:image/jpeg;base64,'+base64.b64encode(raw).decode()+'">'
        sections.append('<section><h2>'+html.escape(obs["kind"])+"</h2>"+image+
            '<pre>'+html.escape(json.dumps(obs,ensure_ascii=False,indent=2))+"</pre></section>")
    document='''<!doctype html><html lang="cs"><meta charset="utf-8">
    <meta http-equiv="Content-Security-Policy" content="default-src 'none'; img-src data:; style-src 'unsafe-inline'">
    <meta name="viewport" content="width=device-width,initial-scale=1"><title>Rubrika — report</title>
    <style>body{font:16px/1.5 system-ui;max-width:1100px;margin:2rem auto;padding:0 1rem}img{max-width:100%}pre{white-space:pre-wrap;overflow-wrap:anywhere}section{border-top:1px solid #ccc;margin-top:2rem}</style>
    <h1>Rubrika — technický report</h1><p><strong>Režim: '''+html.escape(source_mode)+'''</strong>.
    Přepis ani totožnost nejsou automaticky lidsky ověřené. Syntetická data nejsou nálezem z MZA.</p><pre>'''+html.escape(json.dumps(report,ensure_ascii=False,indent=2))+"</pre>"+"".join(sections)+"</html>"
    output.write_text(document,encoding="utf-8")
    output.with_suffix(".json").write_text(json.dumps(report,ensure_ascii=False,indent=2),encoding="utf-8")
