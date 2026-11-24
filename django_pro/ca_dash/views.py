from django.contrib.auth.decorators import login_required
from django.shortcuts import render

from ca_dash.config_helpers import district_full_name, district_short_name


@login_required
def performance_map(request):
    return render(
        request,
        'ca_dash/performance_map.html',
        {
            'district_short': district_short_name(),
            'district_full': district_full_name(),
        },
    )