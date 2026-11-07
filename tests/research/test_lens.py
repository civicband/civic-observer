import uuid
from datetime import date
from urllib.parse import parse_qs

from django.http import QueryDict

from research.lens import Lens


def test_defaults():
    lens = Lens()

    assert lens.text == ""
    assert lens.dispositions == ("u", "r")
    assert lens.codes_all == ()
    assert lens.codes_any == ()
    assert lens.codes_none == ()
    assert lens.uncoded is False
    assert lens.municipality_ids == ()
    assert lens.states == ()
    assert lens.date_from is None
    assert lens.date_to is None
    assert lens.document_type == "all"
    assert lens.added_via == ()
    assert lens.order == "rank"


def test_from_querydict_parses_scalars_and_lists():
    municipality = uuid.uuid4()
    code_all = uuid.uuid4()
    code_any = uuid.uuid4()
    code_none = uuid.uuid4()
    qd = QueryDict(
        "q=housing"
        "&disposition=i,r"
        f"&state=CA,OR&municipality={municipality}"
        f"&code_all={code_all}&code_any={code_any}&code_none={code_none}"
        "&uncoded=1&date_from=2024-01-01&date_to=2024-12-31"
        "&document_type=agenda&added_via=manual,lens&order=date_desc"
    )

    lens = Lens.from_querydict(qd)

    assert lens.text == "housing"
    assert lens.dispositions == ("i", "r")
    assert lens.states == ("CA", "OR")
    assert lens.municipality_ids == (municipality,)
    assert lens.codes_all == (code_all,)
    assert lens.codes_any == (code_any,)
    assert lens.codes_none == (code_none,)
    assert lens.uncoded is True
    assert lens.date_from == date(2024, 1, 1)
    assert lens.date_to == date(2024, 12, 31)
    assert lens.document_type == "agenda"
    assert lens.added_via == ("manual", "lens")
    assert lens.order == "date_desc"


def test_from_querydict_handles_repeated_keys_and_commas():
    qd = QueryDict("state=CA,OR&state=WA&disposition=&q=")

    lens = Lens.from_querydict(qd)

    assert lens.states == ("CA", "OR", "WA")
    assert lens.dispositions == ("u", "r")
    assert lens.text == ""


def test_from_querydict_accepts_plain_mapping():
    lens = Lens.from_querydict({"state": ["CA", "OR"], "q": "zoning"})

    assert lens.states == ("CA", "OR")
    assert lens.text == "zoning"


def test_to_querystring_emits_only_non_defaults():
    assert Lens().to_querystring() == ""
    assert Lens(order="date_asc").to_querystring() == "order=date_asc"
    assert Lens(dispositions=("i",)).to_querystring() == "disposition=i"


def test_querystring_round_trip():
    municipality = uuid.uuid4()
    code_all = uuid.uuid4()
    code_any = uuid.uuid4()
    code_none = uuid.uuid4()
    lens = Lens(
        text="housing OR zoning",
        dispositions=("i",),
        codes_all=(code_all,),
        codes_any=(code_any,),
        codes_none=(code_none,),
        uncoded=True,
        municipality_ids=(municipality,),
        states=("CA", "OR"),
        date_from=date(2024, 1, 1),
        date_to=date(2024, 12, 31),
        document_type="agenda",
        added_via=("manual", "lens"),
        order="date_desc",
    )

    round_tripped = Lens.from_querydict(parse_qs(lens.to_querystring()))

    assert round_tripped == lens
