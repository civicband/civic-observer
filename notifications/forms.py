from django import forms

from notifications.senders import get_sender

from .models import NotificationChannel


class NotificationChannelForm(forms.ModelForm):
    """Form for creating/editing notification channels."""

    class Meta:
        model = NotificationChannel
        fields = ["platform", "handle"]
        widgets = {
            "platform": forms.Select(
                attrs={
                    "class": "mt-1 block w-full rounded-md border-gray-300 shadow-sm"
                }
            ),
            "handle": forms.TextInput(
                attrs={
                    "class": "mt-1 block w-full rounded-md border-gray-300 shadow-sm",
                    "placeholder": "Enter username or webhook URL",
                }
            ),
        }

    def clean(self):
        cleaned_data = super().clean()
        if cleaned_data is None:
            return cleaned_data

        platform = cleaned_data.get("platform")
        handle = cleaned_data.get("handle")

        if platform and handle:
            sender = get_sender(platform)
            if sender and not sender.validate_handle(handle):
                self.add_error(
                    "handle",
                    f"Invalid format for {platform}. Please check the format.",
                )

        return cleaned_data
