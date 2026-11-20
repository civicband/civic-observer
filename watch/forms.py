"""Forms for staff triage actions, place corrections, and backlog notes."""

from __future__ import annotations

from django import forms
from django.db import models

from watch.models import PlaceNote, ThreadPlace


class TriageActionForm(forms.Form):
    """Validate a triage action posted for one thread.

    Fields the action does not use are optional, so a single form covers all
    buttons.  The view enforces the per-action requirements (for example,
    ``place_id`` for ``promote`` and ``query`` for ``edit_query``).
    """

    class Action(models.TextChoices):
        REPLIED = "replied", "Replied"
        DISMISS = "dismiss", "Dismiss"
        SNOOZE = "snooze", "Snooze"
        BACKLOG = "backlog", "Backlog"
        PROMOTE = "promote", "Promote"
        MUTE_SUBREDDIT = "mute_subreddit", "Mute subreddit"
        MUTE_COUNTRY = "mute_country", "Mute country"
        EDIT_QUERY = "edit_query", "Edit query"

    action = forms.ChoiceField(choices=Action.choices)
    days = forms.IntegerField(min_value=1, max_value=90, required=False)
    place_id = forms.UUIDField(required=False)
    country = forms.CharField(max_length=2, required=False)
    query = forms.CharField(max_length=500, required=False)


class PlaceCorrectionForm(forms.Form):
    """Validate a manual correction of a thread's place assignment."""

    place_id = forms.UUIDField()
    matched_text = forms.CharField(max_length=255, required=False)
    role = forms.ChoiceField(
        choices=ThreadPlace.Role.choices,
        required=False,
        initial=ThreadPlace.Role.SUBJECT,
    )


class PlaceNoteForm(forms.ModelForm):
    """Validate a note added from one backlog row."""

    class Meta:
        model = PlaceNote
        fields = ("text", "status")
        widgets = {
            "text": forms.Textarea(
                attrs={
                    "rows": 2,
                    "placeholder": "Add a note",
                    "class": "w-full rounded-md border-gray-300 text-sm shadow-sm focus:border-indigo-500 focus:ring-indigo-500 dark:border-gray-600 dark:bg-gray-800 dark:text-gray-100 dark:placeholder-gray-400",
                }
            ),
            "status": forms.Select(
                attrs={
                    "class": "rounded-md border-gray-300 text-sm shadow-sm focus:border-indigo-500 focus:ring-indigo-500 dark:border-gray-600 dark:bg-gray-800 dark:text-gray-100"
                }
            ),
        }
