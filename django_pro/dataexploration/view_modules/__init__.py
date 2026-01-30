"""
Data Exploration Views Package

This package splits the large views.py into logical, manageable modules:
- student_data_utils: Cache and data fetching utilities
- column_management: Column configuration and management
- student_list: Main list view with filters and export  
- student_summary: Individual student test summary views
- folder_management: Folder, query, and list CRUD operations
"""

# Import utilities (private functions with _ prefix)
from .student_data_utils import (
    get_student_info_cached as _get_student_info_cached,
    get_test_records_cached as _get_test_records_cached,
    get_student_summary_partial_html_cached as _get_student_summary_partial_html_cached,
    get_current_student_columns as _get_current_student_columns,
    get_student_enroll_columns as _get_student_enroll_columns,
)

from .column_management import (
    get_saved_default_columns as _get_saved_default_columns,
    get_column_expression as _get_column_expression,
    user_can_manage_defaults as _user_can_manage_defaults,
    manage_default_columns,
    load_column_structures,
)

from .student_summary import (
    StudentTestSummaryView,
    StudentTestSummaryPartialView,
    warm_student_test_summary_cache,
)

# These will be imported once we create the files
# from .student_list import (
#     StudentWithTestsListView,
#     load_students,
#     export_students_csv,
# )

# from .folder_management import (
#     folder_browser,
#     save_query_modal,
#     save_student_list_modal,
#     save_query,
#     save_student_list,
#     delete_query,
#     rename_query,
#     share_query,
#     unshare_query,
#     get_query_shares,
#     share_folder,
#     unshare_folder,
#     delete_student_list,
#     rename_student_list,
#     rename_modal,
#     share_modal,
#     create_folder_modal,
#     create_folder,
#     rename_folder,
#     delete_folder,
#     copy_query_to_folder,
#     get_query_details,
# )

__all__ = [
    # Utilities (exported with _ prefix for use in same package)
    '_get_student_info_cached',
    '_get_test_records_cached',
    '_get_student_summary_partial_html_cached',
    '_get_current_student_columns',
    '_get_student_enroll_columns',
    '_get_saved_default_columns',
    '_get_column_expression',
    '_user_can_manage_defaults',
    # Views
    'manage_default_columns',
    'load_column_structures',
    'StudentTestSummaryView',
    'StudentTestSummaryPartialView',
    'warm_student_test_summary_cache',
    # To be added:
    # 'StudentWithTestsListView',
    # 'load_students',
    # 'export_students_csv',
    # ... folder management views
]
