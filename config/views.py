from django.shortcuts import render


def homepage(request):
    """Homepage view for CivicObserver."""
    context = {
        "title": "CivicObserver",
        "description": "Empowering civic engagement through transparency and observation",
    }
    return render(request, "homepage.html", context)
