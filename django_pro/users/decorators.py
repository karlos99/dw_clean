from django.shortcuts import redirect
from django.contrib import messages
from functools import wraps
from .models_pages import Page
from .context_processors import user_can_access_page


def page_access_required(page_slug):
    """
    Decorator to check if user has access to a specific page.
    Usage: @page_access_required('data-exploration')
    """
    def decorator(view_func):
        @wraps(view_func)
        def wrapped_view(request, *args, **kwargs):
            try:
                page = Page.objects.get(slug=page_slug, status='active')
                
                if not user_can_access_page(request.user, page):
                    messages.error(request, f'You do not have permission to access {page.name}.')
                    return redirect('landing:index')
                
                return view_func(request, *args, **kwargs)
            except Page.DoesNotExist:
                # If page doesn't exist in management, allow access (backward compatibility)
                return view_func(request, *args, **kwargs)
        
        return wrapped_view
    return decorator
