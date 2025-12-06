from django.template import Context, Template


class TestCivicUrlTag:
    def test_adds_utm_params_to_civic_band_url(self):
        """civic_url tag adds UTM parameters to civic.band URLs."""
        template = Template(
            "{% load utm %}"
            '{% civic_url "https://alameda.ca.civic.band/meetings/agendas/123" '
            'medium="search" campaign="search_results" content="view_button" %}'
        )
        result = template.render(Context({}))

        assert "utm_source=civicobserver" in result
        assert "utm_medium=search" in result
        assert "utm_campaign=search_results" in result
        assert "utm_content=view_button" in result
        assert result.startswith("https://alameda.ca.civic.band/meetings/agendas/123?")

    def test_adds_utm_params_to_docs_civic_band(self):
        """civic_url tag adds UTM parameters to docs.civic.band URLs."""
        template = Template(
            "{% load utm %}"
            '{% civic_url "https://docs.civic.band/api/reference" '
            'medium="nav" campaign="footer" content="docs_link" %}'
        )
        result = template.render(Context({}))

        assert "utm_source=civicobserver" in result
        assert "utm_medium=nav" in result
        assert "utm_campaign=footer" in result
        assert "utm_content=docs_link" in result

    def test_non_civic_url_unchanged(self):
        """civic_url tag returns non-civic URLs unchanged."""
        template = Template(
            "{% load utm %}"
            '{% civic_url "https://google.com/search" '
            'medium="search" campaign="search_results" %}'
        )
        result = template.render(Context({}))

        assert result == "https://google.com/search"
        assert "utm_" not in result

    def test_fake_civic_domain_unchanged(self):
        """civic_url tag doesn't add UTM to domains that look like civic.band but aren't."""
        template = Template(
            "{% load utm %}"
            '{% civic_url "https://civic.band.fake.com/page" '
            'medium="search" campaign="test" %}'
        )
        result = template.render(Context({}))

        assert result == "https://civic.band.fake.com/page"
        assert "utm_" not in result

    def test_preserves_existing_query_params(self):
        """civic_url tag preserves existing query parameters."""
        template = Template(
            "{% load utm %}"
            '{% civic_url "https://civic.band/page?foo=bar&baz=qux" '
            'medium="search" campaign="test" %}'
        )
        result = template.render(Context({}))

        assert "foo=bar" in result
        assert "baz=qux" in result
        assert "utm_source=civicobserver" in result

    def test_empty_url_returns_empty(self):
        """civic_url tag returns empty string for empty URL."""
        template = Template(
            '{% load utm %}{% civic_url "" medium="search" campaign="test" %}'
        )
        result = template.render(Context({}))

        assert result == ""

    def test_default_content_value(self):
        """civic_url tag uses 'link' as default content value."""
        template = Template(
            "{% load utm %}"
            '{% civic_url "https://civic.band/page" '
            'medium="search" campaign="test" %}'
        )
        result = template.render(Context({}))

        assert "utm_content=link" in result

    def test_url_from_template_variable(self):
        """civic_url tag works with URLs from template variables."""
        template = Template(
            "{% load utm %}"
            '{% civic_url page_url medium="notebook" campaign="notebook_detail" %}'
        )
        result = template.render(
            Context({"page_url": "https://oakland.ca.civic.band/meetings/minutes/456"})
        )

        assert "utm_source=civicobserver" in result
        assert "utm_medium=notebook" in result
        assert "utm_campaign=notebook_detail" in result
