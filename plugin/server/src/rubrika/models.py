from datetime import date
from typing import Annotated, Literal
from pydantic import BaseModel, ConfigDict, Field, model_validator

EventType = Literal["birth", "marriage", "death"]
SourceName = Literal["acta_publica", "aron", "census", "vca", "pf", "ahmp", "na", "soapraha", "trebon", "prihlasky", "sketch", "user"]
UnitCategory = Literal["land_book", "cadastre", "other"]
ShortText = Annotated[str, Field(max_length=500)]
Identifier = Annotated[str, Field(min_length=1, max_length=100)]


class Model(BaseModel):
    model_config = ConfigDict(extra="forbid")


class PersonConstraints(Model):
    given_name: ShortText | None = None
    surname: ShortText | None = None
    father: ShortText | None = None
    mother: ShortText | None = None
    spouse: ShortText | None = None
    house_number: ShortText | None = None
    notes: str = Field(default="", max_length=2000)


class SearchQuery(Model):
    raw_query: str = Field(min_length=1, max_length=4000)
    place_hint: ShortText | None = None
    event_type: EventType | None = None
    year_from: int | None = Field(default=None, ge=1000, le=2200)
    year_to: int | None = Field(default=None, ge=1000, le=2200)
    person: PersonConstraints = Field(default_factory=PersonConstraints)
    book_url: str | None = Field(default=None, max_length=2000)

    @model_validator(mode="after")
    def years(self):
        if self.year_from and self.year_to and self.year_from > self.year_to:
            raise ValueError("year_from must not exceed year_to")
        return self


class YearSpan(Model):
    start: int | None = None
    end: int | None = None


class Periods(Model):
    status: Literal["known", "absent", "unknown", "uncertain"] = "unknown"
    spans: list[YearSpan] = Field(default_factory=list)
    raw: str = ""


class EventCoverage(Model):
    records: Periods = Field(default_factory=Periods)
    index: Periods = Field(default_factory=Periods)


class Place(Model):
    source_id: str
    label: str
    names: list[str] = Field(default_factory=list)
    source_url: str


class Book(Model):
    source_id: str
    number: str
    creator: str
    denomination: str | None = None
    coverage: dict[EventType, EventCoverage]
    image_count: int | None = None
    village_count: int | None = None
    detail_url: str
    raw_notes: str = ""


class PfBook(Book):
    """One Porta fontium register book. `number` is the portal's title/signature; `creator` the originator (empty
    until the detail page is read). Types and dating come from the marks of the result row (* births, oo marriages,
    + deaths, i = index)."""
    archive_label: str | None = None    # "Plzeň (státní archiv)"
    places: list[str] = Field(default_factory=list)   # as listed, with German names: "Abertamy (Abertham)"
    dating_raw: str = ""
    nid: str | None = None              # viewer node id (/iipimage/<nid>)
    signature: str | None = None        # from the detail page
    fund_number: str | None = None
    fund_name: str | None = None
    types: list[str] = Field(default_factory=list)
    citation: str | None = None


class SoaBook(Book):
    """One eBadatelna register (SOA Praha). `number` is the register's title ("Kladno 01"); `creator` its originator type.
    Localities are listed as the archive writes them, with the name variants once the detail page has been read."""
    district: str | None = None         # the register's district (SOkA) as the result table shows it
    originator_type: str | None = None
    localities: list[str] = Field(default_factory=list)
    citation: str | None = None
    physical_state: str | None = None
    completeness: str | None = None
    register_type: str | None = None
    description: str | None = None       # the archive's own text ("popis archivu"), not a statement of Rubrika
    review_reason: str | None = None     # why this book can never be a candidate without a human look


class HouseNumbers(Model):
    start: int | None = None
    end: int | None = None
    raw: str = ""                    # rozpis, e.g. "1-38, 40"
    numbers: list[int] = Field(default_factory=list, max_length=2000)
    parsed: bool = False             # True only when every part of raw was understood


class TrebonBook(Book):
    """One register book of a parish in the DigiArchiv of SOA Třeboň. `number` is the book's number in the parish overview
    ("1", "53C"), `creator` the parish. `localities` are the places the book is kept for ("Book for"), `other_localities` those
    it also mentions. Years come from the overview's per-type table (B births, M marriages, D deaths)."""
    parish: str
    parish_id: int
    register_label: str | None = None       # "register B", "index B" as the overview writes it
    localities: list[str] = Field(default_factory=list)
    other_localities: list[str] = Field(default_factory=list)
    note: str | None = None
    citation: str | None = None
    # Land registers, census and seigniorial/urbarial books of the same application (section != "parish_registers"): the unit's own
    # row in the table of books of a fund or municipality. `parish` then holds the place or fund the row was listed under.
    section: Literal["parish_registers", "land_records", "census"] = "parish_registers"
    title: str | None = None
    fund_name: str | None = None
    storage: str | None = None
    category: UnitCategory = "other"
    dating: Periods = Field(default_factory=Periods)
    census_year: int | None = None
    house_numbers: HouseNumbers | None = None
    tree_path: list[str] = Field(default_factory=list)


class Fund(Model):
    source_id: str | None = None     # "aron:<fund uuid>"
    mark: str | None = None          # "C 17", "D 4"
    name: str | None = None
    nad: str | None = None


class ArchivalUnit(Model):
    """One ARON archival description unit (book, cadastre volume, file)."""
    source_id: str                   # "aron:<uuid>"
    title: str
    formal_title: str | None = None  # original, often German title
    fund: Fund = Field(default_factory=Fund)
    unit_type: str | None = None     # ARON enum, e.g. "pozemková kniha"
    category: UnitCategory = "other"
    dating: Periods = Field(default_factory=Periods)
    storage: str | None = None       # "7989 kniha" – practical signature
    part: str | None = None
    notes: str = ""
    places: list[str] = Field(default_factory=list)
    digitized: bool | None = None
    detail_url: str


class SketchUnit(Model):
    """One MZA indication sketch (fund D 9): a map of one cadastral community with house numbers and holders' names.
    `source_id` is `mza:sketch:<MOR id>`; the id encodes the community's signature, the year and the sheet (last digit)."""
    source_id: str
    signature: str                   # signatura of the cadastral community, e.g. "1139"
    katastr: str                     # Czech name of the cadastral community
    german_name: str | None = None
    district: str
    year: int | None = None          # None: the archive lists the sketch without a date
    sheet: int = 0                   # last digit of the id; sheets of one year are numbered 0, 1, 2 ...
    sheets_in_year: int = 1
    note: str | None = None
    other_names: str | None = None
    other_municipalities: str | None = None
    detail_url: str
    sides: list[str] = Field(default_factory=list)      # "front", "back" once the sketch is opened
    grid_tiles: dict[str, int] = Field(default_factory=dict)   # cols, rows of the tile grid once the sketch is opened
    citation: str | None = None


class CensusSet(Model):
    """One census scan set: one place (or part of a place) in one census year."""
    source_id: str                   # "census:4422"
    code: str | None = None          # "BK0232"
    year: int | None = None
    place: str | None = None
    part: str | None = None
    street: str | None = None
    house_numbers: HouseNumbers = Field(default_factory=HouseNumbers)
    archive: str | None = None
    fund: str | None = None
    has_summary: bool | None = None
    images_available: bool = True    # False for 1921 (MZA reading rooms only)
    metadata: dict[str, str] = Field(default_factory=dict, max_length=30)
    detail_url: str


class SoaUnit(Model):
    """One archival record of eBadatelna (SOA Praha), listed as census or land records: `title` is the archive's own name
    ("Sčítání lidu 1900: Okres X - obec Y, čp. 1-50", "Y: Pozemková kniha města 1619-1818"). Census fields come from that title."""
    source_id: str                        # "soapraha:unit:<archivalieId>"
    kind: Literal["census", "land_records"]
    title: str
    original_title: str | None = None
    unit_type: str | None = None          # the archive's archival type: "Sčítací operáty", "Města a obce", "Velkostatky"
    category: UnitCategory = "other"      # land_book for pozemkové/gruntovní/purkrechtní knihy; a census is "other"
    fund: str | None = None
    signature: str | None = None
    inventory_number: str | None = None
    storage_location: str | None = None
    dating: Periods = Field(default_factory=Periods)
    year: int | None = None               # census year
    district: str | None = None           # census: the political district (okres) named in the title
    municipality: str | None = None
    part: str | None = None               # census: the settlement part ("osada")
    house_numbers: HouseNumbers = Field(default_factory=HouseNumbers)
    localities: list[str] = Field(default_factory=list)
    image_count: int | None = None
    detail_url: str
    citation: str | None = None
    description: str | None = None        # the archive's own text, not a statement of Rubrika
    review_reason: str | None = None      # why this unit can never be a candidate without a human look


class PfRecord(Model):
    """One Porta fontium official book (land book, urbarium, ...) or census unit. `number` is the portal's title. A land book
    is told from the other official books by the portal's own book type filter; census units are one municipality (or
    part of it) in one year, with the house numbers ("Nr.") the portal lists for it."""
    source_id: str                      # pf:amtsbuch:<archive>/<fond>/<book> | pf:census:<archive>/<year>/<place>[@<Nr.>]
    kind: Literal["land_records", "census"]
    number: str
    category: UnitCategory = "other"
    category_certain: bool = True       # False: the portal's book type was not checked (page limit), so "other" is a default
    archive_label: str | None = None
    places: list[str] = Field(default_factory=list)
    part: str | None = None             # census: part of the municipality (suburb, hamlet)
    dating_raw: str = ""
    dating: Periods = Field(default_factory=Periods)
    year: int | None = None             # census year
    house_numbers: HouseNumbers = Field(default_factory=lambda: HouseNumbers())
    nid: str | None = None              # viewer node id (/iipimage/<nid>)
    detail_path: str | None = None
    detail_url: str
    signature: str | None = None
    fund_number: str | None = None
    fund_name: str | None = None
    originator: str | None = None
    book_types: list[str] = Field(default_factory=list)
    house_nodes: dict[int, str] = Field(default_factory=dict, max_length=2000)   # census: house Nr. -> its own detail path, where the list rows expose it
    selected_house: str | None = None   # census: the house (Nr.) whose viewer the pages come from
    citation: str | None = None
    image_count: int | None = None


class Page(Model):
    page_id: str
    book_id: str
    scan_number: int
    image_key: str
    source_url: str
    viewer_url: str | None
    source_type: Literal["dzi", "iiif", "zoomify", "jpeg", "sketch", "manual", "user_supplied"]
    manifest_sha256: str
    historical_page: str | None = None
    viewer_link_verified: bool = False
    source: SourceName = "acta_publica"
    citation: str | None = None
    archive: str = "mza"
    provenance: Literal["user_supplied"] | None = None   # the user saved this scan; Rubrika never fetched or verified it
    scan_label: str | None = None                        # the user's own name for the scan ("snímek 37")


class BBox(Model):
    """Top-left origin; x,y,width,height in pixels of the supplied image."""
    x: float = Field(ge=0, allow_inf_nan=False)
    y: float = Field(ge=0, allow_inf_nan=False)
    width: float = Field(gt=0, allow_inf_nan=False)
    height: float = Field(gt=0, allow_inf_nan=False)


class ImageRef(Model):
    image_id: str
    page_id: str
    book_id: str
    source_width: int
    source_height: int
    source_bbox: tuple[int, int, int, int]
    width: int
    height: int
    role: Literal["overview", "detail"]
    sha256: str
    viewer_url: str | None
    manifest_sha256: str
    viewer_link_verified: bool = False
    scan_number: int
    source: SourceName = "acta_publica"
    citation: str | None = None
    image_key: str | None = None
    robots_disallowed: bool = False
    archive: str = "mza"
    provenance: Literal["user_supplied"] | None = None
    scan_label: str | None = None
    effective_scale: float | None = None     # sketches: rendered pixels per source pixel
    low_resolution: bool = False             # sketches: coarser than requested (thumbnail or coarse tile level); not upscaled


class ObservationLink(Model):
    observation_id: Identifier
    relation: Literal["same_person", "same_property", "related"]


class Observation(Model):
    kind: Literal["layout", "index_entry", "record", "reviewed_region", "next_step"]
    image_id: Identifier | None = None
    book_id: Identifier | None = None
    text: str = Field(default="", max_length=8000)
    fields: dict[str, ShortText | None] = Field(default_factory=dict, max_length=30)
    normalized_fields: dict[str, ShortText | None] = Field(default_factory=dict, max_length=30)
    uncertainties: list[ShortText] = Field(default_factory=list, max_length=30)
    matches: list[ShortText] = Field(default_factory=list, max_length=30)
    contradictions: list[ShortText] = Field(default_factory=list, max_length=30)
    is_candidate: bool = False
    coverage_status: Literal["overview_only", "read", "unreadable", "partial"] | None = None
    historical_page: ShortText | None = None
    year: int | None = Field(default=None, ge=1000, le=2200)
    section: ShortText | None = None
    skill_version: str = Field(default="0.14.0", max_length=30)
    links: list[ObservationLink] = Field(default_factory=list, max_length=10)

    @model_validator(mode="after")
    def source_required(self):
        if self.kind != "next_step" and not self.image_id:
            raise ValueError("Image evidence is required for every observation except next_step")
        if self.is_candidate and self.kind not in ("record", "index_entry"):
            raise ValueError("Only a record or index_entry can be a candidate")
        if self.kind == "reviewed_region" and self.coverage_status is None:
            raise ValueError("reviewed_region requires coverage_status")
        if any(len(k) > 80 for k in [*self.fields, *self.normalized_fields]):
            raise ValueError("Field names must be <=80 characters")
        return self


class TreeDate(Model):
    year: int = Field(ge=1000, le=2200)
    month: int | None = Field(default=None, ge=1, le=12)
    day: int | None = Field(default=None, ge=1, le=31)
    qualifier: Literal["exact", "about", "before", "after"] = "exact"

    @model_validator(mode="after")
    def calendar(self):
        if self.day is not None:
            if self.month is None:
                raise ValueError("day requires month")
            date(self.year, self.month, self.day)
        return self


class FindingRef(Model):
    research_id: Identifier
    observation_id: Identifier


class TreeEvent(Model):
    type: Literal["birth", "baptism", "marriage", "death", "burial", "occupation", "residence"]
    date: TreeDate | None = None
    place: ShortText | None = None
    value: ShortText | None = None
    age: ShortText | None = None
    cause: ShortText | None = None
    confidence: Literal["documented", "probable", "hypothesis"]
    sources: list[FindingRef] = Field(default_factory=list, max_length=10)
    note: str = Field(default="", max_length=2000)


class TreePerson(Model):
    id: Identifier
    given_name: str | None = Field(default=None, max_length=120)
    surname: str | None = Field(default=None, max_length=120)
    sex: Literal["M", "F", "U"] = "U"
    events: list[TreeEvent] = Field(default_factory=list, max_length=20)
    note: str = Field(default="", max_length=2000)

    @model_validator(mode="after")
    def named(self):
        if not (self.given_name or self.surname):
            raise ValueError("A person needs a given name or a surname")
        if any(e.type == "marriage" for e in self.events):
            raise ValueError("A marriage belongs to a family, not to a person")
        return self


class TreeFamily(Model):
    id: Identifier
    husband: Identifier | None = None
    wife: Identifier | None = None
    children: list[Identifier] = Field(default_factory=list, max_length=30)
    marriage: TreeEvent | None = None

    @model_validator(mode="after")
    def spouse(self):
        if not (self.husband or self.wife):
            raise ValueError("A family needs a husband or a wife")
        if self.marriage and self.marriage.type != "marriage":
            raise ValueError("Family event must be a marriage")
        return self


class FamilyTree(Model):
    """Persons and families assembled by the model; every documented fact cites a saved finding."""
    title: str = Field(min_length=1, max_length=200)
    root_person: Identifier
    persons: list[TreePerson] = Field(min_length=1, max_length=200)
    families: list[TreeFamily] = Field(default_factory=list, max_length=150)


class ToolOutput(Model):
    """SDK-independent intermediate result; converted at the transport boundary."""
    data: dict
    image_b64: str | None = None
    mime_type: str | None = None
    is_error: bool = False

    def wire(self) -> dict:
        import json
        content = [{"type": "text", "text": json.dumps(self.data, ensure_ascii=False)}]
        if self.image_b64:
            content.append({"type": "image", "data": self.image_b64, "mimeType": self.mime_type})
        # Do not duplicate large reports/images in structuredContent.
        return {"content": content, "isError": self.is_error}
