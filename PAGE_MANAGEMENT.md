# Page Management System

This system allows Project Admins to manage all pages and views in the application dynamically.

## Features

- **Dynamic Navigation**: Menu items are automatically generated from the Page database
- **Role-Based Access**: Control which roles can access each page
- **Status Management**: Pages can be Active, Inactive, or Draft
- **Menu Control**: Show/hide pages in navigation and control display order
- **Hierarchical Structure**: Support for parent-child page relationships

## Usage

### Managing Pages via UI

1. Navigate to **Admin → Pages** (Project Admin only)
2. Add, edit, or delete pages through the interface
3. Configure:
   - Page name and description
   - URL path
   - Icon for menu display
   - Status (Active/Inactive/Draft)
   - Required roles for access
   - Menu visibility and order

### Managing Pages via Command Line

Add a new page:
```bash
python manage.py add_page "My Page" /my-page/ --icon fa-star --roles "Project Admin" "School Admin"
```

Initialize sample pages:
```bash
python manage.py init_pages
```

### Using in Templates

Pages are automatically available in all templates via the `navigation_pages` context variable:

```html
{% for page in navigation_pages %}
    <a href="{{ page.url_path }}">{{ page.name }}</a>
{% endfor %}
```

### Using the Decorator

Protect views with role-based access control:

```python
from users.decorators import page_access_required

@page_access_required('data-exploration')
def my_view(request):
    # Only accessible if user has permission for 'data-exploration' page
    pass
```

## Database Model

**Page Model Fields:**
- `name`: Display name
- `slug`: URL-friendly identifier
- `description`: Optional description
- `url_path`: Internal URL path
- `icon`: CSS class for icon (e.g., 'fa-home')
- `status`: active, inactive, or draft
- `required_roles`: List of role names (empty = all authenticated users)
- `is_public`: Allow unauthenticated access
- `show_in_menu`: Display in navigation
- `menu_order`: Display order (lower = first)
- `parent_page`: For hierarchical menus

## Access Control Logic

1. **Public pages**: Accessible to everyone (including anonymous users)
2. **No required roles**: Accessible to any authenticated user
3. **With required roles**: User must have at least one of the specified roles
4. **Inactive/Draft**: Not accessible even if user has permissions

## Current Pages

Run this to see current pages:
```bash
python manage.py shell -c "from users.models_pages import Page; [print(f'{p.name}: {p.url_path}') for p in Page.objects.filter(status='active')]"
```
