
from django.contrib import admin
from django.urls import path, include
from placementcards.views import save_placement_card, update_card_status

urlpatterns = [
    path('admin/', admin.site.urls),
    path('accounts/', include('allauth.urls')),
    path('users/', include('users.urls')),
    path('dataexploration/', include('dataexploration.urls')),
    path('nextyearlist/', include('nextyearlist.urls')),
    path('api/cards/save/', save_placement_card, name='save_card_root'),
    path('api/cards/status/', update_card_status, name='update_card_status_root'),
    path('dataviz/', include('dataviz.urls')),
    path('placementcards/', include('placementcards.urls')),
    path('mathr/', include('mathr.urls')),
    path('', include('landing.urls')),
]
