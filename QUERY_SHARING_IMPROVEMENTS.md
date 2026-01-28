# Query and List Saving Improvements

## Overview
This document describes the improvements made to the query and student list saving system, inspired by the `karlos99/Dexploration_DJ` repository while maintaining the folder hierarchy structure.

## Key Changes

### 1. Unified SavedQuery Model
**Before:** Separate `SavedQuery` and `StudentList` models with separate tables  
**After:** Unified `SavedQuery` model with `query_type` field

#### New Fields:
- `query_type`: 'filter' or 'student_list' - differentiates between filter queries and student lists
- `privacy`: 'private', 'shared', or 'public' - controls visibility
- `pending_shares`: JSON field storing emails of users without accounts
- `execution_count`: Tracks how many times query has been run
- `last_executed`: Timestamp of last execution
- `is_active`: Soft delete flag

#### Benefits:
- **Simpler data model**: One model instead of two separate systems
- **Unified sharing**: Same sharing mechanism for both queries and lists
- **Better tracking**: Built-in execution metadata
- **Easier updates**: Use `query_id` parameter to update existing queries

### 2. Direct Query Sharing (New QueryShare Model)
**Before:** Folder-based sharing via `FolderShare`  
**After:** Direct query sharing via `QueryShare` with granular permissions

#### QueryShare Features:
- Share individual queries/lists with specific users
- Granular permissions: `can_edit` and `can_reshare`
- Track who shared what and when: `shared_by` and `shared_at`
- Unique constraint prevents duplicate shares
- Compatible with pending shares for non-existent users

#### Benefits:
- **More flexible**: Share specific queries without sharing entire folders
- **Better permissions**: Control who can edit and who can reshare
- **Audit trail**: Know who shared what and when
- **User-friendly**: Simpler mental model for users

### 3. Privacy Levels
**New Feature**: Three-tier privacy system

#### Privacy Options:
1. **Private**: Only owner can see and use
2. **Shared**: Visible to specific users via QueryShare
3. **Public**: Visible to all users in the system

#### Benefits:
- **Flexible visibility**: Choose appropriate level for each query
- **Easy collaboration**: Share with team members
- **Public templates**: Create reusable queries for all users

### 4. Pending Shares
**New Feature**: Share with users who don't have accounts yet

#### How it works:
- Enter email addresses when sharing
- Emails stored in `pending_shares` JSON field
- When user creates account, system can automatically grant access
- Useful for onboarding new users

### 5. Save/Update Flow
**Before:** Separate save and overwrite logic with confirmation dialogs  
**After:** Unified save/update using `query_id` parameter

#### New Workflow:
```javascript
// Save new query
POST /api/save-query/
{
    name: "My Query",
    query_config: {...},
    query_type: "filter",
    privacy: "private"
}
// Returns: { query_id: 123 }

// Update existing query
POST /api/save-query/
{
    query_id: 123,  // Include ID to update
    name: "Updated Query",
    query_config: {...}
}
```

#### Benefits:
- **Cleaner UX**: No confirmation dialogs needed
- **Simpler code**: Single code path for save/update
- **Better for automation**: Easy to update queries programmatically

## API Changes

### New Endpoints:
- `GET /api/query/<id>/shares/` - Get list of users query is shared with
- `POST /api/query/<id>/share/` - Share query with a user
- `POST /api/query/<id>/unshare/<share_id>/` - Remove share

### Updated Endpoints:
- `POST /api/save-query/` - Now supports `query_id` for updates, `privacy`, `share_with_emails`, `query_type`
- `POST /api/save-list/` - Now wraps `save-query` with `query_type='student_list'`

### Legacy Endpoints (kept for backward compatibility):
- `POST /api/folder/<id>/share/` - Folder-level sharing
- `POST /api/folder/<id>/unshare/<share_id>/` - Remove folder share

## Migration Path

### Existing Data:
- `StudentList` model marked as legacy but kept for backward compatibility
- `FolderShare` model kept for existing folder-based shares
- New queries/lists saved to `SavedQuery` with appropriate `query_type`

### Migrating Old Data:
```python
# Example migration script (future improvement)
from dataexploration.models import StudentList, SavedQuery

for old_list in StudentList.objects.all():
    SavedQuery.objects.create(
        name=old_list.name,
        description=old_list.description,
        query_type='student_list',
        owner=old_list.owner,
        folder=old_list.folder,
        privacy='private',
        config={
            'student_ids': list(old_list.items.values_list('ssid', flat=True))
        }
    )
```

## Folder Structure Maintained

**Important**: The folder hierarchy is still maintained!

- Queries/lists can still be organized in folders
- Folder-based browsing still works
- `folder` field is optional on `SavedQuery` (allows queries without folders)
- Folders provide organizational structure
- QueryShare provides access control

### Best of Both Worlds:
```
Folders: Organization & Structure
  ├── My Queries (USER folder)
  │   ├── Grade 10 Math (Query - privacy: private)
  │   └── ELL Students (Query - privacy: shared with 3 users)
  ├── District Reports (DISTRICT folder)
  │   └── Annual Assessment (Query - privacy: public)
  └── School Data (SCHOOL folder)
      └── Current Enrollment (List - privacy: shared)
```

## Performance Improvements

### Redis Caching (Already Implemented):
- Filter options cached for 5 minutes
- Test column metadata cached for 10 minutes
- Cache keys based on MD5 hash of active filters

### Database Indexes (Already Implemented):
- Indexes on frequently queried columns
- Improves filter performance significantly

## UI Improvements Needed (Future Work)

### Suggested Changes (inspired by Dexploration_DJ):

1. **Privacy Selector**: Add radio buttons or dropdown for privacy selection when saving
2. **Share Modal**: Enhanced modal showing current shares with edit/reshare permissions
3. **Query Type Icons**: Different icons for filter queries vs. student lists
4. **Execution Stats**: Show execution count and last executed time
5. **Public Query Browser**: Separate view for browsing public queries
6. **Pending Shares Indicator**: Show count of pending shares on query items

## Code Examples

### Saving a Query with Shares:
```javascript
// Frontend code (example)
const saveQuery = async () => {
    const formData = new FormData();
    formData.append('name', 'My Shared Query');
    formData.append('query_config', JSON.stringify(filters));
    formData.append('query_type', 'filter');
    formData.append('privacy', 'shared');
    formData.append('share_with_emails', 'user1@school.edu,user2@school.edu');
    
    const response = await fetch('/api/save-query/', {
        method: 'POST',
        body: formData
    });
    
    const result = await response.json();
    console.log('Query saved with ID:', result.query_id);
};
```

### Updating an Existing Query:
```javascript
// Include query_id to update
const updateQuery = async (queryId) => {
    const formData = new FormData();
    formData.append('query_id', queryId);  // KEY: Include this
    formData.append('name', 'Updated Query Name');
    formData.append('query_config', JSON.stringify(newFilters));
    
    await fetch('/api/save-query/', {
        method: 'POST',
        body: formData
    });
};
```

### Checking Query Execution:
```python
# Backend code
query = SavedQuery.objects.get(pk=query_id)
query.execute()  # Updates execution_count and last_executed
```

## Admin Interface

The Django admin now shows:
- Query type badges (Filter/Student List)
- Privacy levels
- Execution statistics
- Share management inline
- Pending shares display

## Security Considerations

1. **Permission Checks**: Only owner can share unless `can_reshare` is True
2. **Privacy Enforcement**: Views respect privacy settings
3. **Folder Permissions**: Folder-level permissions still apply
4. **Audit Trail**: All shares tracked with `shared_by` and `shared_at`

## Testing Recommendations

### Manual Tests:
1. Save new filter query with privacy='private'
2. Save new student list with privacy='shared'
3. Update existing query using query_id
4. Share query with existing user
5. Share query with non-existent user email (pending share)
6. Verify execution_count increments
7. Test can_edit permission
8. Test can_reshare permission
9. Verify folder hierarchy still works
10. Test public queries visible to all users

### Automated Tests (TODO):
```python
# Example test cases
class QuerySharingTests(TestCase):
    def test_save_new_query(self):
        """Test creating new query"""
        
    def test_update_existing_query(self):
        """Test updating query with query_id"""
        
    def test_share_with_user(self):
        """Test sharing query"""
        
    def test_pending_shares(self):
        """Test pending share for non-existent user"""
        
    def test_privacy_levels(self):
        """Test private/shared/public visibility"""
        
    def test_execution_tracking(self):
        """Test execution count and timestamp"""
```

## Summary

These improvements bring the query/list management system in line with modern best practices while preserving the useful folder hierarchy. The system is now:

✅ **Simpler**: Unified model for queries and lists  
✅ **More Flexible**: Direct sharing with granular permissions  
✅ **Better Tracked**: Execution statistics and audit trail  
✅ **User-Friendly**: Cleaner save/update flow  
✅ **Future-Proof**: Pending shares for onboarding  
✅ **Performant**: Redis caching and database indexes  
✅ **Organized**: Folder hierarchy preserved  

## Next Steps

1. Update frontend UI to expose privacy selector
2. Create share management modal
3. Add public query browser
4. Migrate existing StudentList data
5. Add automated tests
6. Update user documentation
