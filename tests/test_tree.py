import re
from datetime import date

import pytest
from pydantic import ValidationError

from rubrika.errors import ResearchError
from rubrika.models import FamilyTree, TreeDate
from rubrika.tree import czech_date, export_family_tree, gedcom_date, gedcom_text

TODAY=date(2026,9,29)
URL='https://www.mza.cz/actapublica/matrika/detail/101?image=12'


def obs(oid,kind='record',text='Joannes filius Josephi',book='101',scan=12,folio=None,rid='r1'):
    return (rid,oid),{'observation_id':oid,'kind':kind,'text':text,'book_id':book,'historical_page':folio,
        'evidence':{'image_id':'img-'+oid,'book_id':book,'scan_number':scan,'viewer_url':URL,'citation':None}}


OBS=dict([obs('o1',folio='fol. 10'),obs('o2',scan=34),obs('o3',kind='index_entry',book='102'),
          obs('o4',kind='reviewed_region')])
BOOKS={'101':{'title':'Acta Publica, Znojmo, kniha 101','url':'https://www.mza.cz/actapublica/matrika/detail/101'},
       '102':{'title':'Acta Publica, Znojmo, kniha 102','url':None}}


def src(*oids): return [{'research_id':'r1','observation_id':o} for o in oids]


def line_tree(**over):
    tree={'title':'Přímá linie Dvořák','root_person':'jan','persons':[
        {'id':'jan','given_name':'Jan','surname':'Dvořák','sex':'M','events':[
            {'type':'baptism','date':{'year':1751,'month':3,'day':19},'place':'Znojmo','confidence':'documented','sources':src('o1')},
            {'type':'occupation','value':'čtvrtláník','confidence':'probable'}]},
        {'id':'josef','given_name':'Josef','surname':'Dvořák','sex':'M','events':[
            {'type':'death','date':{'year':1780,'month':5,'day':2},'age':'55','confidence':'documented','sources':src('o2')}]},
        {'id':'anna','given_name':'Anna','sex':'F','events':[
            {'type':'birth','date':{'year':1730,'qualifier':'about'},'confidence':'hypothesis'}]},
        {'id':'vaclav','given_name':'Václav','surname':'Dvořák','sex':'M','events':[]},
    ],'families':[
        {'id':'f1','husband':'josef','wife':'anna','children':['jan']},
        {'id':'f0','husband':'vaclav','children':['josef'],'marriage':None},
    ]}
    tree.update(over)
    return FamilyTree.model_validate(tree)


def run(tree=None,variant='all',observations=OBS,mode='live',books=BOOKS):
    return export_family_tree(tree or line_tree(),observations,books,variant=variant,source_mode=mode,today=TODAY)


def records(ged):
    """Minimal structural GEDCOM check: levels, xrefs and FAMS/FAMC reciprocity."""
    assert ged.endswith('0 TRLR\n'), 'every GEDCOM line, including TRLR, needs a terminator'
    lines=ged[:-1].split('\n')
    assert lines[0]=='0 HEAD' and lines[-1]=='0 TRLR'
    prev=0
    defined,used,fams,famc,husb,chil={},set(),set(),set(),set(),set()
    current=None
    for line in lines:
        assert len(line)<=255 and '\r' not in line
        m=re.match(r'^(\d+) (@[^@]+@ )?(\w+)( .*)?$',line)
        assert m,line
        level=int(m[1])
        assert level<=prev+1,line
        prev=level
        if m[2]:
            current=m[2].strip(); defined[current]=m[3]
        value=(m[4] or '')[1:]
        refs=re.findall(r'(?<!@)@[A-Z]\d+@(?!@)',value)
        used.update(refs)
        if m[3]=='FAMS': fams.add((current,value))
        if m[3]=='FAMC': famc.add((current,value))
        if m[3] in ('HUSB','WIFE'): husb.add((value,current))
        if m[3]=='CHIL': chil.add((value,current))
    assert used<=set(defined)
    assert fams==husb and famc==chil
    return defined


def test_gedcom_dates_and_czech_dates():
    assert gedcom_date(TreeDate(year=1751,month=3,day=19))=='19 MAR 1751'
    assert gedcom_date(TreeDate(year=1743,qualifier='about'))=='ABT 1743'
    assert gedcom_date(TreeDate(year=1750,month=3,qualifier='before'))=='BEF MAR 1750'
    assert gedcom_date(TreeDate(year=1750,qualifier='after'))=='AFT 1750'
    assert czech_date(TreeDate(year=1751,month=3,day=19))=='19. 3. 1751'
    assert czech_date(TreeDate(year=1743,qualifier='about'))=='asi 1743'
    assert czech_date(TreeDate(year=1750,month=3,qualifier='before'))=='před 3/1750'


def test_invalid_dates_rejected():
    for bad in ({'year':1751,'day':19},{'year':1751,'month':2,'day':30}):
        with pytest.raises(ValidationError): TreeDate.model_validate(bad)


def test_text_escapes_at_and_splits_long_lines_without_losing_text():
    text='a@b '+' '.join(['slovo']*80)+'\ndruhý řádek'
    lines=gedcom_text(1,'NOTE',text)
    assert lines[0].startswith('1 NOTE a@@b ')
    assert all(len(l)<=255 for l in lines)
    assert any(l.startswith('2 CONC ') for l in lines) and lines[-1]=='2 CONT druhý řádek'
    rebuilt=''
    for l in lines:
        level,tag,val=(l.split(' ',2)+[''])[:3]
        rebuilt+=('\n' if tag=='CONT' else '')+val
    assert rebuilt==text.replace('@','@@')
    assert not any(l.endswith(' ') for l in lines)
    assert not any(l.startswith('2 CONC  ') for l in lines)


def test_gedcom_structure_sources_and_quality():
    out=run()
    ged=out['gedcom']
    defined=records(ged)
    assert sorted(v for v in defined.values())==['FAM','FAM','INDI','INDI','INDI','INDI','REPO','SOUR']
    assert '1 CHAR UTF-8' in ged and '2 VERS 5.5.1' in ged and '1 SOUR RUBRIKA' in ged and '1 DATE 29 SEP 2026' in ged
    assert '1 NAME Jan /Dvořák/' in ged and '1 NAME Anna //' in ged
    assert '1 CHR\n2 DATE 19 MAR 1751\n2 PLAC Znojmo\n2 SOUR @S1@\n3 PAGE snímek 12, fol. 10\n3 QUAY 3' in ged
    assert '3 DATA\n4 TEXT Joannes filius Josephi' in ged and '3 NOTE '+URL in ged
    assert '1 DEAT\n2 DATE 2 MAY 1780\n2 AGE 55y' in ged
    assert '1 OCCU čtvrtláník\n2 NOTE Nedoloženo (pravděpodobný údaj).' in ged
    assert '2 NOTE Hypotéza, nedoloženo.' in ged
    assert '0 @S1@ SOUR\n1 TITL Acta Publica, Znojmo, kniha 101' in ged
    assert 'nejsou lidsky ověřené' in ged
    assert ged.count('0 @S')==1  # two citations of one book share one SOUR record


def test_gedcom_repository_per_archive():
    obs_all=dict(OBS); obs_all.update([obs('o5',book='102',scan=3)])
    tree=line_tree(persons=[
        {'id':'jan','given_name':'Jan','events':[{'type':'baptism','confidence':'documented','sources':src('o1')}]},
        {'id':'josef','given_name':'Josef','events':[{'type':'death','confidence':'documented','sources':src('o5')}]}],families=[])
    moravia,hradec='Moravský zemský archiv v Brně','Státní oblastní archiv v Hradci Králové'
    books={'101':dict(BOOKS['101'],archive=moravia),'102':dict(BOOKS['102'],archive=hradec)}
    ged=run(tree,observations=obs_all,books=books)['gedcom']
    defined=records(ged)
    assert sorted(defined.values()).count('REPO')==2
    assert f'0 @R1@ REPO\n1 NAME {moravia}\n' in ged and f'0 @R2@ REPO\n1 NAME {hradec}\n' in ged
    assert f'0 @S1@ SOUR\n1 TITL Acta Publica, Znojmo, kniha 101\n1 AUTH {moravia}\n1 REPO @R1@' in ged
    assert f'0 @S2@ SOUR\n1 TITL Acta Publica, Znojmo, kniha 102\n1 AUTH {hradec}\n1 REPO @R2@' in ged
    assert ged.index('0 @R1@ REPO')>ged.index('0 @S2@ SOUR') and ged.endswith('0 TRLR\n')


def test_gedcom_book_without_archive_is_mza():
    ged=run()['gedcom']
    assert '1 AUTH Moravský zemský archiv v Brně' in ged and ged.count('@ REPO\n')==1
    assert '0 @R1@ REPO\n1 NAME Moravský zemský archiv v Brně' in ged and '1 REPO @R1@' in ged


def test_event_without_details_is_marked_as_occurred():
    tree=line_tree(persons=[{'id':'jan','given_name':'Jan','events':[{'type':'death','confidence':'documented','sources':src('o1')}]}],families=[])
    ged=run(tree)['gedcom']
    assert '1 DEAT Y\n2 SOUR @S1@' in ged


def test_non_numeric_age_goes_to_note():
    tree=line_tree(persons=[{'id':'jan','given_name':'Jan','events':[
        {'type':'death','age':'3 týdny','confidence':'documented','sources':src('o1')}]}],families=[])
    ged=run(tree)['gedcom']
    assert '2 AGE' not in ged and '2 NOTE Věk: 3 týdny' in ged


def test_summary_and_filename():
    out=run()
    s=out['summary']
    assert (s['persons'],s['families'],s['citations'])==(4,2,2)
    assert {(u['person'],u['event'],u['confidence']) for u in s['undocumented']}=={
        ('Jan Dvořák','occupation','probable'),('Anna','birth','hypothesis')}
    assert out['suggested_filename']=='prima-linie-dvorak'
    assert out['variant']=='all'


def test_mermaid_has_every_person_once_and_marks_confidence():
    m=run()['mermaid']
    assert m.startswith('flowchart TB')
    for name in ('Jan Dvořák','Josef Dvořák','Anna','Václav Dvořák'):
        assert m.count(name)==1
    assert '* 1751' in m and '† 1780' in m and '* asi 1730' in m
    assert 'classDef undocumented' in m and ':::root' in m
    assert re.search(r'P\d+ --- F\d+',m) and re.search(r'F\d+ --> P\d+',m)


def test_mermaid_labels_cannot_break_out():
    tree=line_tree(persons=[{'id':'jan','given_name':'Pa"]; click P1 "x','surname':'<b>','events':[]}],families=[])
    m=run(tree)['mermaid']
    assert '"]; click' not in m and '<b>' not in m and '#quot;' in m


@pytest.mark.parametrize('change,code',[
    (lambda t: t['persons'][0]['events'][0].update(sources=[{'research_id':'r1','observation_id':'missing'}]),'NOT_FOUND'),
    (lambda t: t['persons'][0]['events'][0].update(sources=src('o4')),'INVALID_ARGUMENT'),
    (lambda t: t['persons'][0]['events'][0].update(sources=src('o3')),'INVALID_ARGUMENT'),
    (lambda t: t['persons'][0]['events'][0].update(sources=[]),'INVALID_ARGUMENT'),
    (lambda t: t['persons'].append(dict(t['persons'][0])),'INVALID_ARGUMENT'),
    (lambda t: t['families'][0].update(children=['jan','ghost']),'INVALID_ARGUMENT'),
    (lambda t: t.update(root_person='ghost'),'INVALID_ARGUMENT'),
    (lambda t: t['families'].append({'id':'f2','husband':'vaclav','children':['jan']}),'INVALID_ARGUMENT'),
    (lambda t: t['families'].append({'id':'f2','husband':'jan','children':['vaclav']}),'INVALID_ARGUMENT'),
    (lambda t: t['families'].append({'id':'f1','husband':'vaclav'}),'INVALID_ARGUMENT'),
])
def test_invalid_trees_rejected(change,code):
    raw=line_tree().model_dump()
    change(raw)
    with pytest.raises(ResearchError) as e:
        run(FamilyTree.model_validate(raw))
    assert e.value.code==code


def test_index_entry_is_enough_for_probable():
    raw=line_tree().model_dump()
    raw['persons'][0]['events'][0].update(sources=src('o3'),confidence='probable')
    ged=run(FamilyTree.model_validate(raw))['gedcom']
    assert '3 QUAY 2' in ged and 'kniha 102' in ged


def test_model_rejects_marriage_on_person_and_nameless_person():
    raw=line_tree().model_dump()
    raw['persons'][0]['events'].append({'type':'marriage','confidence':'probable'})
    with pytest.raises(ValidationError): FamilyTree.model_validate(raw)
    raw=line_tree().model_dump()
    raw['persons'][0].update(given_name=None,surname=None)
    with pytest.raises(ValidationError): FamilyTree.model_validate(raw)
    raw=line_tree().model_dump()
    raw['families'][0].update(husband=None,wife=None)
    with pytest.raises(ValidationError): FamilyTree.model_validate(raw)


def test_documented_only_variant_keeps_line_connectors_without_facts():
    out=run(variant='documented_only')
    ged=out['gedcom']
    records(ged)
    assert 'OCCU' not in ged and 'ABT 1730' not in ged
    assert '1 NAME Václav /Dvořák/' not in ged  # no documented ancestor above Václav
    assert '1 NAME Anna //' not in ged
    assert '1 NAME Josef /Dvořák/' in ged and '1 NAME Jan /Dvořák/' in ged
    warnings=' '.join(out['summary']['warnings'])
    assert 'Václav Dvořák' in warnings and 'Anna' in warnings and 'occupation' in warnings
    assert out['summary']['undocumented']==[]


def test_documented_only_keeps_undocumented_person_between_documented_generations():
    raw=line_tree().model_dump()
    raw['persons'][1]['events']=[]  # Josef has no documented fact...
    raw['persons'][3]['events']=[{'type':'death','confidence':'documented','sources':src('o2')}]  # ...but Václav does
    out=run(FamilyTree.model_validate(raw),variant='documented_only')
    ged=out['gedcom']
    records(ged)
    assert '1 NAME Josef /Dvořák/' in ged and '1 NAME Václav /Dvořák/' in ged


def test_synthetic_mode_is_labelled():
    assert 'SYNTETICKÁ TESTOVACÍ DATA' in run(mode='synthetic_demo')['gedcom']


def test_long_occupation_stays_within_line_limit_and_long_names_are_rejected():
    tree=line_tree(persons=[{'id':'jan','given_name':'Jan','events':[
        {'type':'occupation','value':'čtvrtláník '*40,'confidence':'probable'}]}],families=[])
    records(run(tree)['gedcom'])
    raw=line_tree().model_dump()
    raw['persons'][0]['given_name']='x'*121
    with pytest.raises(ValidationError): FamilyTree.model_validate(raw)


def test_open_ends_name_where_the_direct_line_stops():
    ends=run()['summary']['open_ends']
    assert [(e['person_id'],e['generation'],e['missing']) for e in ends]==[
        ('vaclav',2,['birth','parents']),
        ('josef',1,['birth','mother','parents_marriage']),
        ('anna',1,['birth','parents']),
        ('jan',0,['parents_marriage'])]
    by={e['person_id']:e for e in ends}
    assert by['jan']['anchor']=={'event':'baptism','year':1751,'place':'Znojmo','confidence':'documented'}
    assert by['anna']['anchor']['confidence']=='hypothesis'
    assert by['vaclav']['known_from_child']=='Josef Dvořák' and 'anchor' not in by['vaclav']


def test_open_ends_skip_a_documented_generation_and_people_off_the_line():
    tree=line_tree(families=[{'id':'f1','husband':'josef','wife':'anna','children':['jan'],
        'marriage':{'type':'marriage','date':{'year':1749},'confidence':'documented','sources':src('o2')}}])
    ends=run(tree)['summary']['open_ends']
    assert 'jan' not in {e['person_id'] for e in ends}            # baptism and parents' marriage documented
    assert 'vaclav' not in {e['person_id'] for e in ends}         # no longer linked to the root
    assert next(e for e in ends if e['person_id']=='josef')['anchor']['event']=='marriage'


def test_open_ends_are_capped_and_counted():
    persons=[{'id':f'p{i}','given_name':f'P{i}','events':[]} for i in range(40)]
    families=[{'id':f'f{i}','husband':f'p{i+1}','children':[f'p{i}']} for i in range(39)]
    out=run(line_tree(root_person='p0',persons=persons,families=families))['summary']
    assert len(out['open_ends'])==30 and out['open_ends_total']==40 and out['open_ends'][0]['person_id']=='p39'
    assert 'open_ends_total' not in run()['summary']


def test_crops_are_linked_only_when_given():
    assert 'OBJE' not in run()['gedcom']
    tree,observations=line_tree(),OBS
    from rubrika.tree import to_gedcom
    ged,_=to_gedcom(tree,observations,BOOKS,variant='all',source_mode='live',today=TODAY,media={('r1','o1'):'vyrezy/001.jpg'})
    records(ged)
    assert ('3 NOTE '+URL+'\n3 OBJE\n4 FILE vyrezy/001.jpg\n5 FORM jpg\n4 TITL Výřez zápisu, snímek 12, fol. 10\n') in ged
    assert ged.count('OBJE')==1
