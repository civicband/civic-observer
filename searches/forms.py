from django import forms

from municipalities.models import Muni

from .models import SavedSearch, Search


class SavedSearchCreateForm(forms.ModelForm):
    """Form for creating a saved search by specifying search parameters directly."""

    municipality = forms.ModelChoiceField(
        queryset=Muni.objects.all(),
        empty_label="Select a municipality",
        help_text="Choose the municipality you want to monitor",
    )

    search_term = forms.CharField(
        max_length=500,
        required=False,
        help_text="Enter keywords to search for (leave blank for all results)",
        widget=forms.TextInput(
            attrs={"placeholder": 'e.g., "budget", "zoning", "public safety"'}
        ),
    )

    all_results = forms.BooleanField(
        required=False,
        help_text="Check to receive notifications for all new agendas and minutes",
        label="Subscribe to all updates",
    )

    class Meta:
        model = SavedSearch
        fields = ["name"]
        help_texts = {"name": "Give this saved search a memorable name"}
        widgets = {
            "name": forms.TextInput(
                attrs={"placeholder": 'e.g., "Oakland Budget Updates"'}
            )
        }

    def clean(self) -> dict | None:
        cleaned_data = super().clean()
        if not cleaned_data:
            raise forms.ValidationError(
                "You must either enter a search term or select 'Subscribe to all updates'."
            )
        search_term = cleaned_data.get("search_term", "").strip()
        all_results = cleaned_data.get("all_results", False)

        # If no search term and not all_results, that's invalid
        if not search_term and not all_results:
            raise forms.ValidationError(
                "You must either enter a search term or select 'Subscribe to all updates'."
            )

        # If both search_term and all_results, prefer search_term
        if search_term and all_results:
            cleaned_data["all_results"] = False

        return cleaned_data

    def save(self, commit=True, user=None):
        """Save the form by creating or finding the appropriate Search object."""
        if user is None:
            raise ValueError("User must be provided to save SavedSearch")

        municipality = self.cleaned_data["municipality"]
        search_term = self.cleaned_data.get("search_term", "").strip()
        all_results = self.cleaned_data.get("all_results", False)

        # If all_results is True, use empty search_term (all updates mode)
        if all_results:
            search_term = ""

        # Get or create the Search object using new signature
        search = Search.objects.get_or_create_for_params(
            search_term=search_term,
            municipalities=[municipality],  # Now expects a list
        )

        # Create the SavedSearch
        saved_search = super().save(commit=False)
        saved_search.user = user
        saved_search.search = search

        if commit:
            saved_search.save()

        return saved_search


class SavedSearchEditForm(forms.ModelForm):
    """Form for editing a saved search by specifying search parameters directly."""

    municipality = forms.ModelChoiceField(
        queryset=Muni.objects.all(),
        empty_label="Select a municipality",
        help_text="Choose the municipality you want to monitor",
    )

    search_term = forms.CharField(
        max_length=500,
        required=False,
        help_text="Enter keywords to search for (leave blank for all results)",
        widget=forms.TextInput(
            attrs={"placeholder": 'e.g., "budget", "zoning", "public safety"'}
        ),
    )

    all_results = forms.BooleanField(
        required=False,
        help_text="Check to receive notifications for all new agendas and minutes",
        label="Subscribe to all updates",
    )

    class Meta:
        model = SavedSearch
        fields = ["name"]
        help_texts = {"name": "Give this saved search a memorable name"}
        widgets = {
            "name": forms.TextInput(
                attrs={"placeholder": 'e.g., "Oakland Budget Updates"'}
            )
        }

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        if self.instance and self.instance.search:
            # Pre-populate form with current search parameters
            # Get first municipality (simplified for legacy form)
            first_muni = self.instance.search.municipalities.first()
            self.fields["municipality"].initial = first_muni
            self.fields["search_term"].initial = self.instance.search.search_term or ""
            # all_results = True if search_term is empty
            self.fields["all_results"].initial = not bool(
                self.instance.search.search_term
            )

    def clean(self):
        cleaned_data = super().clean()
        if not cleaned_data:
            raise forms.ValidationError(
                "You must either enter a search term or select 'Subscribe to all updates'."
            )
        search_term = cleaned_data.get("search_term", "").strip()
        all_results = cleaned_data.get("all_results", False)

        # If no search term and not all_results, that's invalid
        if not search_term and not all_results:
            raise forms.ValidationError(
                "You must either enter a search term or select 'Subscribe to all updates'."
            )

        # If both search_term and all_results, prefer search_term
        if search_term and all_results:
            cleaned_data["all_results"] = False

        return cleaned_data

    def save(self, commit=True, user=None):
        """Save the form by creating or finding the appropriate Search object."""
        if user is None:
            raise ValueError("User must be provided to save SavedSearch")

        municipality = self.cleaned_data["municipality"]
        search_term = self.cleaned_data.get("search_term", "").strip()
        all_results = self.cleaned_data.get("all_results", False)

        # If all_results is True, use empty search_term (all updates mode)
        if all_results:
            search_term = ""

        # Get or create the Search object using new signature
        search = Search.objects.get_or_create_for_params(
            search_term=search_term,
            municipalities=[municipality],  # Now expects a list
        )

        # Update the SavedSearch
        saved_search = super().save(commit=False)
        saved_search.user = user
        saved_search.search = search

        if commit:
            saved_search.save()

        return saved_search
