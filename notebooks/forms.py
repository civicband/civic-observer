from django import forms

from .models import Notebook


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
