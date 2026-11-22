"""Repair municipality countries that were stored as names or left as US.

The deploy pipeline forwards ``sites.country`` verbatim and ``django_countries``
does not normalize a name to a code on write, so Canadian municipalities were
saved with values like ``"Canada"`` in a column the app reads as an ISO 3166-1
alpha-2 code.  This migration rewrites any resolvable value to its code and
fills in ``CA`` for municipalities whose state is a Canadian province.
"""

from __future__ import annotations

from django.db import migrations

from municipalities.countries import country_for_state, to_iso_code


def _repair(apps, schema_editor):
    Muni = apps.get_model("municipalities", "Muni")
    for muni in Muni.objects.all().only("pk", "country", "state"):
        current = muni.country.code if hasattr(muni.country, "code") else muni.country
        # A Canadian province implies Canada even when the stored value is a
        # resolvable-but-wrong "US": that is the bug being repaired.
        resolved = country_for_state(muni.state) or to_iso_code(current)
        if not resolved or resolved == current:
            continue
        muni.country = resolved
        muni.save(update_fields=["country"])


def _noop(apps, schema_editor):
    """Leave repaired values in place; the prior text is ambiguous."""


class Migration(migrations.Migration):
    dependencies = [
        ("municipalities", "0005_add_last_indexed"),
    ]

    operations = [
        migrations.RunPython(_repair, _noop),
    ]
