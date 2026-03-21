"""
Data Exploration Views

This module provides a clean interface to the refactored view_modules package.
The original monolithic views.py (2,346 lines) has been split into logical modules:
  - student_data_utils.py: Caching and data fetching utilities (196 lines)
  - column_management.py: Column configuration and management (126 lines)
  - student_summary.py: Student test summary views (77 lines)
  - student_list_views.py: Main list view and data loading (909 lines)
  - export_views.py: CSV export functionality (492 lines)
  - folder_management.py: Folder, query, and list CRUD operations (634 lines)

Total: ~2,434 lines split across 6 focused modules instead of one 2,346-line file.
"""

# Import from student list and data loading module
from .view_modules.student_list_views import (
    StudentWithTestsListView,
    load_students,
)

# Import from export module
from .view_modules.export_views import (
    export_students_csv,
)

# Import from folder management module
from .view_modules.folder_management import (
    folder_browser,
    save_query_modal,
    save_student_list_modal,
    save_query,
    save_student_list,
    delete_query,
    rename_query,
    share_query,
    unshare_query,
    get_query_shares,
    share_folder,
    unshare_folder,
    delete_student_list,
    rename_student_list,
    rename_modal,
    share_modal,
    create_folder_modal,
    create_folder,
    rename_folder,
    delete_folder,
    copy_query_to_folder,
    get_query_details,
)

# Import from student summary module
from .view_modules.student_summary import (
    StudentTestSummaryView,
    StudentTestSummaryPartialView,
    warm_student_test_summary_cache,
)

# Import from column management module
from .view_modules.column_management import (
    load_column_structures,
)

# Import from statistics module
from .view_modules.statistics_views import (
    get_column_statistics,
)

# Export all public views
__all__ = [
    # Statistics
    'get_column_statistics',
    # List and data views
    'StudentWithTestsListView',
    'load_students',
    'export_students_csv',
    # Student summary views
    'StudentTestSummaryView',
    'StudentTestSummaryPartialView',
    'warm_student_test_summary_cache',
    # Column management
    'load_column_structures',
    # Folder and query management
    'folder_browser',
    'save_query_modal',
    'save_student_list_modal',
    'save_query',
    'save_student_list',
    'delete_query',
    'rename_query',
    'share_query',
    'unshare_query',
    'get_query_shares',
    'share_folder',
    'unshare_folder',
    'delete_student_list',
    'rename_student_list',
    'rename_modal',
    'share_modal',
    'create_folder_modal',
    'create_folder',
    'rename_folder',
    'delete_folder',
    'copy_query_to_folder',
    'get_query_details',
]
