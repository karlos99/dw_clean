from django.shortcuts import redirect
from django.contrib import messages
from functools import wraps
from .models_pages import Page
from .context_processors import user_can_access_page, _is_project_admin_user


def page_access_required(page_slug):
    """
    Decorator to check if user has access to a specific page.
    Usage: @page_access_required('data-exploration')

    Draft/inactive pages are visible only to Project Admins and superusers until published.
    """
    def decorator(view_func):
        @wraps(view_func)
        def wrapped_view(request, *args, **kwargs):
            page = Page.objects.filter(slug=page_slug).first()
            if page is None:
                return view_func(request, *args, **kwargs)

            if page.status != 'active':
                if not _is_project_admin_user(request.user):
                    messages.error(request, f'{page.name} is not published yet.')
                    return redirect('landing:index')
                return view_func(request, *args, **kwargs)

            if not user_can_access_page(request.user, page):
                messages.error(request, f'You do not have permission to access {page.name}.')
                return redirect('landing:index')

            return view_func(request, *args, **kwargs)
        
        return wrapped_view
    return decorator
