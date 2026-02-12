from django.shortcuts import render, redirect
from dataexploration.models import SavedQuery

# Create your views here.
def index(request):
    if request.user.is_authenticated:
        # Dashboard data
        context = {
            'saved_queries': SavedQuery.objects.filter(owner=request.user).order_by('-updated_at')[:5],
            'shared_queries': SavedQuery.objects.filter(shares__shared_with=request.user).order_by('-updated_at')[:5],
            'school_count': len(request.user.schools) if isinstance(request.user.schools, list) else 0,
        }
        return render(request, 'landing_dashboard.html', context)
            
    return render(request, 'landing.html')

