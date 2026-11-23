from functools import wraps

from django.http import JsonResponse


def api_login_required(view_func):
    """Return JSON 401 instead of an HTML login redirect for API callers."""

    @wraps(view_func)
    def _wrapped(request, *args, **kwargs):
        if not request.user.is_authenticated:
            return JsonResponse({'error': 'Authentication required'}, status=401)
        return view_func(request, *args, **kwargs)

    return _wrapped