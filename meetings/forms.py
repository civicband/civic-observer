from django import forms

from municipalities.models import Muni


class MeetingSearchForm(forms.Form):
    """Form for searching meeting documents with filters."""

    query = forms.CharField(
        max_length=500,
        required=False,
        help_text="Enter keywords to search for in meeting documents",
        widget=forms.TextInput(
            attrs={
                "placeholder": 'Search for keywords like "budget", "zoning", "housing"',
                "class": "w-full px-4 py-2 border border-gray-300 rounded-lg focus:ring-2 focus:ring-indigo-500 focus:border-indigo-500",
            }
        ),
    )

    municipality = forms.ModelChoiceField(
        queryset=Muni.objects.all().order_by("name"),
        required=False,
        empty_label="All municipalities",
        help_text="Filter by municipality",
        widget=forms.Select(
            attrs={
                "class": "w-full px-4 py-2 border border-gray-300 rounded-lg focus:ring-2 focus:ring-indigo-500 focus:border-indigo-500",
            }
        ),
    )

    date_from = forms.DateField(
        required=False,
        help_text="Show meetings from this date onwards",
        widget=forms.DateInput(
            attrs={
                "type": "date",
                "class": "w-full px-4 py-2 border border-gray-300 rounded-lg focus:ring-2 focus:ring-indigo-500 focus:border-indigo-500",
            }
        ),
    )

    date_to = forms.DateField(
        required=False,
        help_text="Show meetings up to this date",
        widget=forms.DateInput(
            attrs={
                "type": "date",
                "class": "w-full px-4 py-2 border border-gray-300 rounded-lg focus:ring-2 focus:ring-indigo-500 focus:border-indigo-500",
            }
        ),
    )

    document_type = forms.ChoiceField(
        choices=[
            ("", "All document types"),
            ("agenda", "Agendas"),
            ("minutes", "Minutes"),
        ],
        required=False,
        help_text="Filter by document type",
        widget=forms.Select(
            attrs={
                "class": "w-full px-4 py-2 border border-gray-300 rounded-lg focus:ring-2 focus:ring-indigo-500 focus:border-indigo-500",
            }
        ),
    )

    def clean(self):
        cleaned_data = super().clean()
        if not cleaned_data:
            return cleaned_data

        date_from = cleaned_data.get("date_from")
        date_to = cleaned_data.get("date_to")

        # Validate that date_from is not after date_to
        if date_from and date_to and date_from > date_to:
            raise forms.ValidationError(
                "Start date must be before or equal to end date."
            )

        return cleaned_data
