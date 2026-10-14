"""CCGI dashboard landing/redirect views.

The CCGI pages themselves are rendered by ``dataviz.page_view`` (the pages are
seeded as DataVizPage records by the ``seed_ccgi`` management command).  This
module only provides a small app-level entry point so ``/ccgi/`` lands the user
on the Career Explorer dashboard and lists the other CCGI pages.
"""
from django.http import Http404
from django.shortcuts import redirect
from django.urls import reverse


def index(request):
    """Redirect /ccgi/ to the CCGI Career Explorer DataViz page."""
    from dataviz.models import DataVizPage

    page = (
        DataVizPage.objects.filter(slug='ccgi-favorite-careers').first()
        or DataVizPage.objects.filter(folder__startswith='CCGI').order_by('title').first()
    )
    if not page:
        raise Http404('No CCGI pages have been seeded. Run: python manage.py seed_ccgi')
    return redirect(reverse('dataviz:page_view', kwargs={'slug': page.slug}))


def dashboard(request):
    """List every page inside the CCGI folder."""
    from django.shortcuts import render
    from dataviz.models import DataVizPage

    pages = DataVizPage.objects.filter(folder__startswith='CCGI').order_by('title')
    return render(request, 'ccgi/dashboard.html', {'pages': pages})
