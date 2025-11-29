from django import forms

from .models import Notebook, NotebookEntry, Tag


class NotebookForm(forms.ModelForm):
    class Meta:
        model = Notebook
        fields = ["name"]
        widgets = {
            "name": forms.TextInput(
                attrs={
                    "class": "block w-full rounded-md border-gray-300 shadow-sm focus:border-indigo-500 focus:ring-indigo-500 sm:text-sm",
                    "placeholder": "Enter notebook name",
                }
            ),
        }


class NotebookEntryForm(forms.ModelForm):
    tags = forms.ModelMultipleChoiceField(
        queryset=Tag.objects.none(),
        required=False,
        widget=forms.CheckboxSelectMultiple(
            attrs={"class": "h-4 w-4 text-indigo-600 border-gray-300 rounded"}
        ),
    )

    class Meta:
        model = NotebookEntry
        fields = ["note", "tags"]
        widgets = {
            "note": forms.Textarea(
                attrs={
                    "rows": 4,
                    "class": "block w-full rounded-md border-gray-300 shadow-sm focus:border-indigo-500 focus:ring-indigo-500 sm:text-sm",
                    "placeholder": "Add a note about this page...",
                }
            ),
        }

    def __init__(self, *args, user=None, **kwargs):
        super().__init__(*args, **kwargs)
        if user:
            self.fields["tags"].queryset = Tag.objects.filter(user=user)  # type: ignore[attr-defined]
