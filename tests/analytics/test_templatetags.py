from django.template import Context, Template


class TestTrackEventFilter:
    def test_track_event_outputs_data_attribute(self):
        """track_event filter outputs data-umami-event attribute."""
        template = Template(
            '{% load analytics %}<button {{ "click_me"|track_event }}>Click</button>'
        )
        result = template.render(Context({}))

        assert 'data-umami-event="click_me"' in result

    def test_track_event_escapes_quotes(self):
        """track_event filter handles event names safely."""
        template = Template(
            '{% load analytics %}<button {{ "test_event"|track_event }}>Test</button>'
        )
        result = template.render(Context({}))

        assert 'data-umami-event="test_event"' in result


class TestTrackEventDataFilter:
    def test_track_event_data_outputs_both_attributes(self):
        """track_event_data filter outputs event and data attributes."""
        template = Template(
            '{% load analytics %}<a {{ "muni_viewed"|track_event_data:"alameda" }}>Link</a>'
        )
        result = template.render(Context({}))

        assert 'data-umami-event="muni_viewed"' in result
        assert 'data-umami-event-data="alameda"' in result

    def test_track_event_data_with_variable(self):
        """track_event_data filter works with template variables."""
        template = Template(
            '{% load analytics %}<a {{ "muni_viewed"|track_event_data:slug }}>Link</a>'
        )
        result = template.render(Context({"slug": "oakland"}))

        assert 'data-umami-event="muni_viewed"' in result
        assert 'data-umami-event-data="oakland"' in result
