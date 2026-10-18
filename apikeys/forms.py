from django import forms

from municipalities.models import Muni


class APIKeyCreateForm(forms.Form):
    name = forms.CharField(
        max_length=255,
        widget=forms.TextInput(
            attrs={
                "class": "block w-full rounded-md border-gray-300 shadow-sm focus:border-indigo-500 focus:ring-indigo-500 sm:text-sm",
                "placeholder": "e.g., Production Server",
            }
        ),
    )
    expires_at = forms.DateTimeField(
        required=False,
        widget=forms.DateTimeInput(
            attrs={
                "type": "datetime-local",
                "class": "block w-full rounded-md border-gray-300 shadow-sm focus:border-indigo-500 focus:ring-indigo-500 sm:text-sm",
            }
        ),
    )
    all_sites = forms.BooleanField(
        required=False,
        initial=True,
        label="Allow all civic.band sites",
        help_text="Uncheck to limit this key to specific sites.",
        widget=forms.CheckboxInput(
            attrs={
                "class": "h-4 w-4 rounded border-gray-300 text-indigo-600 focus:ring-indigo-500",
            }
        ),
    )
    sites = forms.CharField(
        required=False,
        label="Sites",
        help_text=(
            "Comma-separated subdomains (for example alameda.ca, oakland.ca). "
            "Required when not allowing all sites."
        ),
        widget=forms.TextInput(
            attrs={
                "class": "block w-full rounded-md border-gray-300 shadow-sm focus:border-indigo-500 focus:ring-indigo-500 sm:text-sm",
                "placeholder": "alameda.ca, oakland.ca",
            }
        ),
    )

    def clean(self):
        cleaned = super().clean() or {}
        # Keep older form posts working: if the field is absent entirely,
        # treat the key as unrestricted rather than scoped with no sites.
        if "all_sites" in self.data:
            all_sites = cleaned.get("all_sites", False)
        else:
            all_sites = True
            cleaned["all_sites"] = True

        raw_sites = cleaned.get("sites", "") or ""
        subdomains = list(
            dict.fromkeys(site.strip() for site in raw_sites.split(",") if site.strip())
        )

        if all_sites:
            cleaned["site_objects"] = []
            return cleaned

        if not subdomains:
            self.add_error("sites", "Select at least one site or allow all sites.")
            return cleaned

        munis = list(Muni.objects.filter(subdomain__in=subdomains))
        found = {muni.subdomain for muni in munis}
        missing = [subdomain for subdomain in subdomains if subdomain not in found]
        if missing:
            self.add_error(
                "sites",
                f"Unknown site subdomains: {', '.join(missing)}",
            )
        cleaned["site_objects"] = munis
        return cleaned
