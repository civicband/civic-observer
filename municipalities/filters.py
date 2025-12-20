import django_filters

from .models import Muni


class MuniFilter(django_filters.FilterSet):
    state = django_filters.CharFilter(field_name="state", lookup_expr="exact")

    class Meta:
        model = Muni
        fields = ["state"]
