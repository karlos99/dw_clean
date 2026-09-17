import mimetypes

from django.contrib.auth.decorators import login_required
from django.http import FileResponse, HttpResponse
from django.views.decorators.http import require_GET

from .pictures import PLACEHOLDER_SVG, picture_path


@login_required
@require_GET
def student_picture(request, ssid):
    path = picture_path(ssid)
    if path is None:
        return HttpResponse(PLACEHOLDER_SVG, content_type='image/svg+xml')
    content_type, _ = mimetypes.guess_type(str(path))
    return FileResponse(path.open('rb'), content_type=content_type or 'image/jpeg')
