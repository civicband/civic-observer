import pytest
from django.db import IntegrityError

from municipalities.models import Muni

from .models import Search


@pytest.mark.django_db
class TestSearchModel:
    def test_create_search_with_term(self):
        muni = Muni.objects.create(
            subdomain="testcity", name="Test City", state="CA", kind="city"
        )
        search = Search.objects.create(
            muni=muni, search_term="city council", all_results=False
        )
        assert search.muni == muni
        assert search.search_term == "city council"
        assert search.all_results is False
        assert search.created is not None
        assert search.modified is not None
        assert str(search.id)  # UUID is valid

    def test_create_search_without_term(self):
        muni = Muni.objects.create(
            subdomain="testcity2", name="Test City 2", state="NY", kind="city"
        )
        search = Search.objects.create(muni=muni, all_results=True)
        assert search.muni == muni
        assert search.search_term == ""
        assert search.all_results is True

    def test_str_representation_with_term(self):
        muni = Muni(name="San Francisco", state="CA")
        search = Search(muni=muni, search_term="budget")
        assert str(search) == "Search for 'budget' in San Francisco"

    def test_str_representation_without_term(self):
        muni = Muni(name="Los Angeles", state="CA")
        search = Search(muni=muni, all_results=True)
        assert str(search) == "Search in Los Angeles (all results: True)"

    def test_muni_required(self):
        with pytest.raises(IntegrityError):
            Search.objects.create(search_term="test search")

    def test_default_values(self):
        muni = Muni.objects.create(
            subdomain="defaults", name="Default Test", state="TX", kind="city"
        )
        search = Search.objects.create(muni=muni)
        assert search.search_term == ""
        assert search.all_results is False

    def test_related_name_searches(self):
        muni = Muni.objects.create(
            subdomain="related", name="Related Test", state="FL", kind="city"
        )
        search1 = Search.objects.create(muni=muni, search_term="first")
        search2 = Search.objects.create(muni=muni, search_term="second")

        assert search1 in muni.searches.all()
        assert search2 in muni.searches.all()
        assert muni.searches.count() == 2

    def test_cascade_delete(self):
        muni = Muni.objects.create(
            subdomain="cascade", name="Cascade Test", state="WA", kind="city"
        )
        search = Search.objects.create(muni=muni, search_term="test")
        search_id = search.id

        # Delete the muni, search should be deleted too
        muni.delete()
        assert not Search.objects.filter(id=search_id).exists()

    def test_meta_options(self):
        assert Search._meta.verbose_name == "Search"
        assert Search._meta.verbose_name_plural == "Searches"
        assert Search._meta.ordering == ["-created"]
