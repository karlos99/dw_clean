# Project Instructions for GitHub Copilot

**Django 6 · Docker · HTMX · PostgreSQL · Redis · Nginx**

You are an **expert Django 6 developer** assisting on a **performance‑critical, production‑grade Django project**.
All suggestions **must prioritize speed, efficiency, and correctness**.

---

## 1. Core Philosophy: Performance Above All Else

**Performance is the primary and non‑negotiable requirement.**

Every design and implementation decision must be evaluated through a performance lens.

### Required Principles

* Minimize database queries
* Avoid unnecessary computation
* Return the smallest possible response
* Cache aggressively and correctly

### Database Optimization (Mandatory)

* Always use `select_related()` for FK and O2O relationships.
* Always use `prefetch_related()` for M2M and reverse relationships.
* Never allow N+1 query patterns.
* Use `.only()` or `.defer()` when full model hydration is unnecessary.
* Prefer annotated querysets over Python loops.

**Bad**

```python
for obj in Model.objects.all():
    print(obj.related.name)
```

**Good**

```python
Model.objects.select_related("related").all()
```

---

## 2. Tech Stack & Infrastructure (Assumed at All Times)

Copilot must assume the following environment without restating or questioning it.

| Layer                  | Technology                    |
| ---------------------- | ----------------------------- |
| Backend                | Django 6.0+                   |
| Database               | PostgreSQL (Docker container) |
| Cache                  | Redis (Docker container)      |
| Web Server             | Nginx (Docker container)      |
| Frontend Interactivity | HTMX                          |
| Styling                | Tailwind CSS                  |

### Docker Networking Rules

* Services are accessed via container hostnames:

  * PostgreSQL → `postgres`
  * Redis → `redis`

Example Redis URL:

```text
redis://redis:6379/0
```

---

## 3. Caching Strategy (Required)

Caching is **not optional**.

### Redis Usage

Use Redis for:

* Expensive database queries
* Computed view logic
* HTMX partial responses
* Aggregations and statistics

### Guidelines

* Cache computed results, not unsafe lazy objects.
* Use deterministic cache keys.
* Always include cache invalidation logic or TTLs.

**Preferred Pattern**

```python
from django.core.cache import cache

key = f"user_dashboard:{user.id}"
data = cache.get(key)

if data is None:
    data = expensive_computation()
    cache.set(key, data, timeout=300)
```

---

## 4. Template Architecture & Django 6 Partials

This project **requires Django 6 native template partials**.

### Required Usage

* Break complex templates into reusable partials.
* Partials must be small, focused, and composable.
* HTMX requests must return **only the required partial**.

### Syntax (Required)

```django
{% partial def card item %}
<div class="p-4 border rounded">
    {{ item.name }}
</div>
{% endpartial %}

{% partial card item %}
```

### HTMX Rule

If a request is HTMX:

* ❌ Do NOT return a full page
* ✅ Return only the partial HTML

---

## 5. HTML & Template Formatting Rules (Strict)

These rules exist to prevent template corruption and improve maintainability.

### Single‑Line Tag Enforcement (MANDATORY)

Template tags **must never be split across lines**.

❌ **Invalid**

```django
{% if
   user.is_authenticated
%}
```

✅ **Valid**

```django
{% if user.is_authenticated %}
```

---

### Multiple Tags per Line (Allowed)

Multiple tags on one line are allowed **only if each tag is complete**.

```django
<span>{% if active %}Yes{% else %}No{% endif %}</span>
```

---

### Operator Spacing (MANDATORY)

Always include **one space before and after operators**.

❌ `{% if count==0 %}`
✅ `{% if count == 0 %}`

---

### Whitespace Preservation

* Do NOT collapse blocks into unreadable one‑liners
* Keep logical blocks visually separated
* Maintain readable structure

---

## 6. Static Assets & Logic Separation

Strict separation of concerns is enforced.

### Static File Locations

* JavaScript: `templates/static/js/`
* CSS: `templates/static/css/`

### Rules

* ❌ No inline `<script>` tags
* ❌ No inline `<style>` blocks
* ❌ No business logic in templates
* ✅ Use HTMX attributes for behavior
* ✅ Use JavaScript only when HTMX cannot solve the problem

Nginx is assumed to serve all static files in production.

---

## 7. HTMX Best Practices

HTMX is the **primary interaction layer**.

### Guidelines

* Keep responses minimal
* Prefer server‑rendered HTML
* Avoid JSON unless absolutely required
* Use `hx-target`, `hx-swap`, and `hx-trigger` explicitly
* Avoid unnecessary full DOM replacements

---

## 8. Code Generation Priorities (Ordered)

When generating code, follow this priority order:

1. Performance & query efficiency
2. Correct caching strategy
3. Docker compatibility and networking
4. DRY templates and partial reuse
5. Type hints and readability
6. Maintainability

---

## 9. Python & Django Standards

### Required

* Use Python type hints wherever possible
* Prefer function‑based views for HTMX endpoints unless CBVs provide clear value
* Keep views thin; move logic to services or selectors
* Avoid premature abstraction

---

## 10. Documentation Reference

All Django behavior must align with **official Django 6 documentation**:

📘 [https://docs.djangoproject.com/en/6.0/](https://docs.djangoproject.com/en/6.0/)

---

## Final Instruction to GitHub Copilot

> If a suggestion trades performance for convenience, reject it.
> If a solution adds complexity without measurable benefit, reject it.
> **Optimize first. Always.**


## Roles
- Project Admin: (superUser)
- District Admin: (All Students, can edit all folders)
- District View: (All Students View)
- School Admin: School level Students. Can add Schoool level Roles(school view, Grade view). Can edit School folder
- School View: School level Students. 
- School Gr view: School gr level view. 
- Teacher (default): students base on the enrollment table(school year)


## Placement Cards
 - the placement cards application allow teacher to enter notes about the students that exist in their class, and give the school admin or designated user to place them in a next year classsrooms.
 - only the elementary student, schools (tk, kn, 1 -5 grade)
 - the list of students will include information that is include the in formation from the student summary from the data exploration app.
 - the school admin will select what questions the teacher will be entering in for the notes, but will also have the ability to create new questions, but only for their school. 
 - the level of student access will match similar to the logic from the dataexploration application.
   - differents in logic will be the ability to the school administrator to add grade level access that will give a user the ability to assing students to a next year classroom.
- school administrator will be able to create next year classrooms that will not require a teacher assing to them during the placements but the then can be updated to then be able to assign a teacher.
- the school adiminstrations will have the control of what questions the teachers will see, and will also allow to create and edit the questions that the school sees, and also edit the type of questions for the notes or the teacher. 
- the teacher will then be able to change the focus to see the current students list or the next year list, by focusion on a the school year. ex if the current school year is 2025-2025, and option to see the current students and once the principal has assign a clasroom for 2026-2027 a list of those students too, with the notes from the other teachers(read only).
- do not touch any of the existing models or edit existing database tables only add new. 
-  ### Teacher notes (base)
    - this is the list of questions that the teacher will be entering in the form
            - Academic
              -- Reading Standard (muli-choice) [Above, At, Below, Well Below ]
              -- Math Standard (muli-choice) [Above, At, Below, Well Below ]
            - Behavior & Study Habits
              -- Study/Work Habits (textbox)
              -- classroom Behavior
            - Physical & Health
              -- Adaptive PE (checkbox)
              -- Physical Handicap (checbox)
              -- Wears Glasses (checkbox)
              -- Allegies (checbox) if checked expand texbox to details if any
              -- Medication (checkbox)
              -- Counseling (checkbox) if check expand texbox to include detail if any
            - Behavior & Attendance
              -- Playground Habits (texbox)
              -- Tardiness (textbox)
              -- SARB (textbox)

            - Parent Involment
              -- Parent Teacher Conference (checkbox)
              -- Classroom Involment (checkbox)
              -- Field Trips
            
            -Additional Information
              - should Not be in the same Class with (list of the school Students ) [list]
                -notes about the student not to be in same class
              - Other information (textbox)
              - Additional Comments (textbox)
    ## classroom break down 
      - one created a clasroom the user that is placing the student in the classroom needs to have a breakdown of the statictic of the classroom, using varius messurments that the user will select. 


Plan: Placement Cards Application (Updated with Tagging System)
TL;DR: Create a new Django app placementcards that allows teachers to enter configurable notes about elementary students (TK-5) and enables school admins to place students into next year classrooms. The app mirrors dataexploration's access control pattern, uses Redis caching for performance, leverages HTMX for interactivity, and integrates with existing student summary data. School admins can create color-coded tags for fast filtering during placement. All functionality is implemented through new models—no modifications to existing tables.

Key Decisions:

Configurable question system allows school admins to customize teacher note fields while providing a base set of standard questions (Academic, Behavior, Health, Attendance, Parent Involvement). Questions stored as JSONField configurations with validation rules, enabling flexible form rendering without database migrations for each change.
Tag system with color + text labels enables rapid visual filtering during placement; tags are school-scoped to prevent cross-school conflicts; M2M relationship allows multiple tags per student; tags cached aggressively (1-hour TTL) since they change infrequently.
Steps

1. Create New Django App and Base Structure
Create app: python manage.py startapp placementcards
Add to config/settings.py INSTALLED_APPS
Create modular structure:
placementcards/view_modules/ (card_views, question_views, classroom_views, placement_views, tag_views)
placementcards/templatetags/placementcards_tags.py
placementcards/templates/placementcards/ with partials subdirectory
placementcards/static/placementcards/js/ and css/
2. Design Core Models
In placementcards/models.py, create new models (no modifications to existing):

PlacementTag (NEW): School-managed tags for student categorization

school ForeignKey to studata.School (school-specific tags)
name CharField(max_length=50) - e.g. "Accelerated", "Needs Support", "Behavioral Watch", "ELL Priority"
color CharField(max_length=20) - Tailwind color classes or hex codes (e.g., "bg-blue-500", "bg-red-500", "#3B82F6")
description TextField (optional explanation)
is_active BooleanField (for soft delete)
display_order IntegerField (for consistent ordering)
created_by ForeignKey to User
created_at, updated_at DateTimeFields
Unique constraint on (school, name)
Meta: ordering = ['display_order', 'name']
QuestionTemplate: Stores configurable questions per school

ForeignKey to studata.School (school-specific configuration)
question_category (Academic, Behavior, Physical, Attendance, Parent, Additional)
question_text, question_type (text, textarea, checkbox, choice, multiselect, student_list)
field_options JSONField (choices for select fields)
is_active, display_order
requires_detail BooleanField (for conditional fields like Allergies → detail textbox)
created_by ForeignKey to User
PlacementCard: Main card storing teacher notes

stu_annual_key CharField (links to CurrentStudent without FK to avoid constraints)
ssid CharField (indexed for lookups)
school_year CharField (e.g., "2024-2025")
responses JSONField (stores all question responses as {question_id: value})
tags ManyToManyField to PlacementTag (NEW - allows multiple tags per student)
completed_by ForeignKey to User (teacher who filled it)
is_complete BooleanField
last_updated DateTimeField
Unique constraint on (stu_annual_key, school_year)
NextYearClassroom: Classrooms for placement

school ForeignKey to studata.School
school_year CharField (e.g., "2025-2026" for next year)
grade CharField (TK, KN, 01-05)
classroom_name CharField (e.g., "Mrs. Smith's Class", "Room 101")
assigned_teacher_name CharField (nullable—can be assigned later)
max_capacity IntegerField (optional limit)
created_by ForeignKey to User
StudentPlacement: Assignment of students to classrooms

student_ssid CharField (indexed)
classroom ForeignKey to NextYearClassroom
placed_by ForeignKey to User
placed_at DateTimeField
notes TextField (placement notes)
Unique constraint on (student_ssid, classroom__school_year)
ClassroomStatisticPreference: User's preferred metrics for classroom breakdowns

user ForeignKey to User
preferred_metrics JSONField (list of metrics: gender, race, ell, sed, sped, gate, reading_level, math_level, tags)
3. Implement Access Control (Mirror Dataexploration Pattern)
In placementcards/view_modules/card_data_utils.py:

Function get_accessible_students(request, school_year, grades=['TK','KN','01','02','03','04','05']):

Check is_unrestricted (superuser, Project Admin, District roles)
Apply active_school filter if set
Apply schools list filter for school-level roles
Apply grade_levels restriction for School Gr View roles
Apply get_accessible_student_keys() for Teacher role (Period 0 homeroom only)
Return optimized queryset with .select_related() and .only() for performance
Add query hint for PostgreSQL optimizer
Function can_user_edit_placement(user, student): Determine if user can place student in classroom

School Admin → can place any student in their school(s)
Grade-level access → can place students in assigned grades only
Default → read-only access
Function can_user_manage_tags(user, school) (NEW): Check tag management permission

School Admin for that school → True
School Gr View for that school → True
Otherwise → False
4. Build Tag Management Interface (NEW)
In placementcards/view_modules/tag_views.py:

View manage_tags(request): School admin/grade-level interface to manage tags

Require School Admin or School Gr View role via can_user_manage_tags()
Load PlacementTag objects for active school, ordered by display_order
Display in table with color preview, name, description, usage count
HTMX-driven: add/edit/delete/reorder tags without page reload
Cache invalidation on tag changes: cache.delete(f"placement_tags:{school_id}")
View create_tag_modal(request): HTMX modal for tag creation

Form: name, color (color picker or predefined Tailwind classes), description
Color options: Provide preset palette (blue, green, yellow, red, purple, pink, gray) with visual preview
Validate uniqueness per school
Save to PlacementTag, return updated tag list partial
View edit_tag_modal(request, tag_id): HTMX modal for tag editing

Load existing tag, ensure user has permission
Same form as create, update on save
Return updated tag list partial
View delete_tag(request, tag_id): HTMX POST to delete tag

Soft delete: set is_active=False (preserve historical data)
Remove from all PlacementCard.tags relationships
Invalidate caches, return updated tag list partial
View reorder_tags(request): HTMX POST with new order

Receive JSON array of tag IDs in new order
Update display_order fields atomically
Return success response
In placementcards/templates/placementcards/manage_tags.html:

Table showing all school tags with drag-to-reorder capability
Color swatches rendered inline
Usage count showing how many students currently have each tag
Quick filter preview showing what clicking tag would show
5. Build Question Management Interface
In placementcards/view_modules/question_views.py:

View manage_questions(request): School admin interface to configure questions

Load active school's QuestionTemplate objects grouped by category
HTMX-driven: add/edit/delete questions without page reload
Cache invalidation on question changes: cache.delete(f"questions:{school_id}")
View save_question(request): HTMX POST endpoint

Validate question data, save to QuestionTemplate
Return updated question list partial
Seed default questions on first access per school (via migration or view logic)

Academic: Reading Standard (choice: Above/At/Below/Well Below), Math Standard (same)
Behavior: Study Habits (textarea), Classroom Behavior (textarea)
Physical: Adaptive PE, Physical Handicap, Glasses, Allergies (w/ detail), Medication, Counseling (w/ detail) - all checkboxes
Attendance: Playground Habits (textarea), Tardiness (textarea), SARB (textarea)
Parent: Parent Conference, Classroom Involvement, Field Trips - all checkboxes
Additional: "Should not be with" (student_list), Other info (textarea), Additional comments (textarea)
6. Create Teacher Note Entry Interface
In placementcards/templates/placementcards/teacher_notes.html:

Main page: School year selector (current or next year if classrooms exist)
Tag filter bar (NEW): Horizontal list of tag badges at top, click to toggle filter
HTMX: hx-get with tag_ids parameter to reload filtered student list
Visual feedback: selected tags highlighted, show count of filtered students
"Clear filters" button
Student list with access control applied, showing student summary data (integrate via cached get_student_info_cached())
Each student row shows their tags (NEW): Small colored badges next to name
Click student → open HTMX modal with note entry form
In placementcards/view_modules/card_views.py:

View teacher_notes_page(request): Main page rendering

Apply get_accessible_students() with school year filter
Load school tags (NEW): cache.get_or_set(f"placement_tags:{school_id}", qs, 3600)
Apply tag filters if present (NEW): Filter students by tags__id__in=selected_tag_ids if URL param exists
Cache student list count: cache.get_or_set(f"placement_count:{hash}", count, 300)
Load PlacementCard completion status and tags for each student (use .prefetch_related('tags'))
View edit_placement_card_modal(request, ssid): HTMX modal

Load cached student summary from dataexploration pattern
Load school's QuestionTemplate objects (cached: cache.get_or_set(f"questions:{school_id}", qs, 3600))
Load school's PlacementTag objects (NEW): Display tag selector in modal
Load existing PlacementCard.responses and .tags if exists
Render dynamic form based on question types
Tag selector: Multi-select with color badges (HTMX or Alpine.js for dynamic add/remove)
For "should not be with" field: HTMX-powered student search in same grade/school
View save_placement_card(request): HTMX POST

Validate and save responses to PlacementCard.responses JSONField
Save selected tags (NEW): card.tags.set(selected_tag_ids)
Set is_complete=True, completed_by=request.user
Invalidate related caches
Return success message partial
View quick_tag_student(request) (NEW): HTMX POST for rapid tagging

Minimal endpoint: {ssid, tag_id, action: 'add'|'remove'}
Add/remove tag from student's placement card
Return updated student row partial with new tag badges
Enable bulk tagging: accept multiple SSIDs for batch operations
7. Implement Classroom Management (School Admin)
In placementcards/view_modules/classroom_views.py:

View manage_classrooms(request): School admin interface

List NextYearClassroom for selected school/year
HTMX buttons: create, edit, delete classroom
Display current assignments count per classroom
Show tag distribution per classroom (NEW): Quick stats on which tagged students are in each classroom
View create_classroom_modal(request): HTMX modal

Form: school_year (next year auto-calculated), grade, classroom_name, assigned_teacher_name (optional), max_capacity
Save to NextYearClassroom
View edit_classroom(request, classroom_id): HTMX POST

Update classroom details, invalidate related caches
8. Build Student Placement Interface
In placementcards/templates/placementcards/place_students.html:

Tag filter bar at top (NEW): Same as teacher notes view, but with richer interactions
Click tag → filter student list in real-time via HTMX
Multi-tag filter mode toggle: "Any" (OR) vs "All" (AND) - filter students with any selected tag or all selected tags
Show filtered count: "Showing 23 of 145 students"
Split view: Left = student list with placement cards (notes visible), Right = classroom boxes
Each student card shows colored tag badges (NEW): Visual identification at a glance
Drag-and-drop or click-to-place interaction (HTMX POST on placement)
Filter by grade, search by name
Show completion status (green checkmark if PlacementCard.is_complete)
Quick tag buttons (NEW): Right-click or button on student card to show tag menu for rapid retagging during placement
In placementcards/view_modules/placement_views.py:

View placement_page(request): Main placement interface

Load students with get_accessible_students() + placement permission check
Load school tags (NEW): cache.get_or_set(f"placement_tags:{school_id}", qs, 3600)
Apply tag filters (NEW): Support both OR and AND filter modes based on URL params
Load NextYearClassroom for school/year
Prefetch existing StudentPlacement assignments
Prefetch PlacementCard.tags (NEW): Use .prefetch_related('tags') for efficiency
Cache heavily: cache.get_or_set(f"placement_data:{school}:{year}:{user}:{tag_filter_hash}", data, 600)
View assign_student_to_classroom(request): HTMX POST

Create/update StudentPlacement
Return updated classroom partial with new student count and tag distribution
View remove_student_from_classroom(request): HTMX POST

Delete StudentPlacement, return updated partial
View filter_students_by_tags(request) (NEW): HTMX GET endpoint

Accept tag_ids (list) and filter_mode ('any'|'all') params
Query students:
ANY mode: .filter(placementcard__tags__id__in=tag_ids).distinct()
ALL mode: Annotate count and filter where count equals number of tags
Return student list partial with filtered results
Cache with tag filter hash in key
9. Implement Classroom Statistics Breakdown
In placementcards/view_modules/classroom_views.py:

View classroom_statistics(request, classroom_id): HTMX partial

Query StudentPlacement → join CurrentStudent for demographics
Use .annotate() for aggregations (gender count, race distribution, ell count, etc.)
Calculate tag distribution (NEW): Count students per tag in classroom
Query: PlacementCard.objects.filter(ssid__in=classroom_student_ssids).prefetch_related('tags')
Aggregate: {tag.name: count} dictionary
Render as color-coded bar chart or badge list
Calculate reading/math level distribution from latest test scores (cached via dataexploration pattern)
Load user's ClassroomStatisticPreference to determine which metrics to show (including tags if selected)
Render statistics partial with charts (use simple HTML/CSS bars, or Tailwind width percentages)
View update_statistic_preferences(request): HTMX POST

Save user's preferred metrics to ClassroomStatisticPreference
Return updated statistics partial
10. Add Print/Export Functionality
In placementcards/view_modules/print_views.py:

View print_placement_cards(request): Generate printable HTML

Query students with completed placement cards
Include tag badges in printout (NEW): Show color blocks or text labels for each student's tags
Render compact card layout (2-3 per page)
Include key info: name, grade, current teacher, tags, notes summary, placement assignment
CSS media query for @media print (ensure colors print correctly)
View export_placements_csv(request): Streaming CSV export

Similar pattern to dataexploration/view_modules/export_views.py
Columns: ssid, student_name, current_grade, next_classroom, placement_date, completed_by, tags (comma-separated) (NEW)
Include selected question responses as columns
Tag export options (NEW): Include tag column showing all tags for each student (e.g., "Accelerated,ELL Priority")
11. Create URL Routing and Navigation
In placementcards/urls.py:


app_name = 'placementcards'urlpatterns = [    # Main views    path('teacher-notes/', teacher_notes_page, name='teacher_notes'),    path('place-students/', placement_page, name='place_students'),    path('manage-classrooms/', manage_classrooms, name='manage_classrooms'),    path('manage-questions/', manage_questions, name='manage_questions'),    path('manage-tags/', manage_tags, name='manage_tags'),  # NEW        # HTMX API endpoints    path('api/cards/<str:ssid>/', edit_placement_card_modal, name='edit_card'),    path('api/cards/save/', save_placement_card, name='save_card'),    path('api/classrooms/create/', create_classroom_modal, name='create_classroom'),    path('api/placement/assign/', assign_student_to_classroom, name='assign_student'),    path('api/classroom/<int:classroom_id>/stats/', classroom_statistics, name='classroom_stats'),        # Tag endpoints (NEW)    path('api/tags/create/', create_tag_modal, name='create_tag'),    path('api/tags/<int:tag_id>/edit/', edit_tag_modal, name='edit_tag'),    path('api/tags/<int:tag_id>/delete/', delete_tag, name='delete_tag'),    path('api/tags/reorder/', reorder_tags, name='reorder_tags'),    path('api/tags/quick-tag/', quick_tag_student, name='quick_tag'),    path('api/students/filter-by-tags/', filter_students_by_tags, name='filter_by_tags'),        # Export    path('print/', print_placement_cards, name='print_cards'),    path('export/csv/', export_placements_csv, name='export_csv'),]
Update config/urls.py: Add path('placementcards/', include('placementcards.urls'))

Update main navigation: Add "Placement Cards" link in templates/base.html for users with Teacher+ roles, plus "Manage Tags" in dropdown for School Admin

12. Build Templates with Django 6 Partials
Create placementcards/templates/placementcards/teacher_notes.html:

School year selector (dropdown)
Tag filter bar (NEW): {% partial def tag_filter_bar %} with tag badges
Student grid/table with HTMX loading
Include {% partial def student_card %} for reusable card rendering with tag badges
Create placementcards/templates/placementcards/partials/placement_card_form.html:

Dynamic form rendering based on QuestionTemplate objects
Tag selector section (NEW): Multi-select tag chips with colors, HTMX to add/remove
Conditional fields (via Alpine.js or HTMX events)
Student summary sidebar (integrate cached data from dataexploration)
Create placementcards/templates/placementcards/place_students.html:

Tag filter bar with AND/OR toggle (NEW)
Two-column layout: students + classrooms
Student cards with visible tag badges (NEW)
HTMX swap on placement
Use {% partial def classroom_box %} for classroom display with tag distribution stats
Create placementcards/templates/placementcards/manage_tags.html (NEW):

Table of tags with color previews
Drag-to-reorder rows
HTMX modals for create/edit
Usage statistics per tag
Create print template placementcards/templates/placementcards/print_cards.html:

Print-optimized CSS with color support
Tag badges rendered with colors (NEW)
Page breaks between cards
Create placementcards/templates/placementcards/partials/tag_badge.html (NEW):

Reusable partial for rendering single tag badge
Color + text, sized appropriately for context (list vs card vs filter)
13. Add Caching Strategy Throughout
Question templates: Cache per school, TTL 3600s, key placement_questions:{school_id}

Tags (NEW): Cache per school, TTL 3600s, key placement_tags:{school_id} (tags change infrequently)

Student lists: Cache per user/school/year/tag_filter_hash (NEW), TTL 300s, key placement_students:{hash}

Student summary data: Reuse existing dataexploration cache (get_student_info_cached())

Classroom statistics: Cache per classroom, TTL 600s, key classroom_stats:{classroom_id} (includes tag distribution)

Placement assignments: Cache per school/year, TTL 300s, key placements:{school_id}:{year}

Tag usage counts (NEW): Cache per school, TTL 300s, key tag_usage:{school_id} (count of students per tag)

Invalidate on:

Question changes → cache.delete(f"placement_questions:{school_id}")
Tag changes (NEW) → cache.delete(f"placement_tags:{school_id}") + cache.delete(f"tag_usage:{school_id}")
Tag assignment to card (NEW) → cache.delete_pattern(f"placement_students:*") (or targeted hash invalidation)
Placement assignment → cache.delete(f"placements:{school_id}:{year}") + cache.delete(f"classroom_stats:{classroom_id}")
Classroom edit/delete → related placement cache keys
14. Create Database Migrations
Run python manage.py makemigrations placementcards
Review migration file to ensure no alterations to existing tables
Create data migration to seed default QuestionTemplate objects for existing schools (optional, can be done on-demand)
No default tags (NEW): Tags are user-created, so no seed data needed
Run python manage.py migrate
15. Write Tests
Create placementcards/tests.py:

Test access control: Teacher sees only their students, School Admin sees all school students
Test question CRUD: Create/edit/delete questions as School Admin
Test tag CRUD (NEW): Create/edit/delete/reorder tags as School Admin, verify school isolation
Test tag filtering (NEW): Filter students by single tag, multiple tags (OR/AND), verify query efficiency
Test quick tagging (NEW): Add/remove tags via quick-tag endpoint, verify cache invalidation
Test placement card save/load with tags
Test classroom creation and assignment
Test statistics calculation accuracy (including tag distribution)
Test CSV export content (including tag column)
16. Add Admin Interface
In placementcards/admin.py:

Register models with Django admin for superuser management
PlacementTag admin (NEW): List display with colored indicators, school filter
Inline for StudentPlacement in NextYearClassroom admin
Read-only views for PlacementCard (JSONField display, tags shown as colored badges)
17. Performance Optimization Pass
Add database indices:

PlacementTag (NEW): (school, is_active, display_order), (school, name)
PlacementCard: (stu_annual_key, school_year), (ssid), (completed_by)
PlacementCard_tags M2M table (NEW): Django auto-creates, but verify indices on both FKs
NextYearClassroom: (school, school_year, grade)
StudentPlacement: (student_ssid), (classroom, placed_at)
QuestionTemplate: (school, is_active, display_order)
Query optimization:

Use .select_related('classroom', 'placed_by') on StudentPlacement queries
Use .prefetch_related('placements') when loading classrooms with assignments
Use .prefetch_related('tags') on PlacementCard queries (NEW): Prevents N+1 when rendering tag badges
Use .only() to limit fields when rendering lists
Add COUNT aggregation caching for classroom assignment counts
Tag filter queries: Use .distinct() when filtering by tags to avoid duplicates in OR queries
Ensure all views follow instructions: minimize queries, cache aggressively, return minimal HTML partials for HTMX

Verification

Access Control Test: Login as Teacher → only see Period 0 homeroom students; login as School Admin → see all school's elementary students
Question Configuration Test: School Admin creates custom question → Teacher sees it in note form
Tag Management Test (NEW): School Admin creates tags with colors → Tags visible in teacher/placement views → Edit tag color → UI updates → Soft delete tag → Removed from all views but preserved in historical records
Tag Filtering Test (NEW): Create 3 tags, assign various combinations to students → Filter by single tag (shows correct students) → Filter by 2 tags in OR mode (shows students with either tag) → Filter by 2 tags in AND mode (shows only students with both tags) → Performance under 500ms with 500+ students
Quick Tagging Test (NEW): During placement, click student → Add tag via quick-tag → Badge appears immediately → Remove tag → Badge disappears → Bulk tag 20 students → All updated within 1 second
Placement Flow Test: Teacher completes notes with tags → School Admin filters by tag during placement → Places student in next year classroom → View classroom statistics showing tag distribution → Balanced placement across tags
Multi-Year Test: Current year (2024-2025) students with notes and tags → Next year (2025-2026) placement creation → Teacher switches view to see next year assignments (read-only notes from other teachers, tags visible)
Performance Test: Load 500+ student list under 2 seconds, tag filter under 300ms, placement card save with tags under 200ms, classroom statistics with tag breakdown under 500ms (via caching)
Export Test: CSV export includes all selected columns, question responses, and tag column with comma-separated tag names; completes download successfully
Decisions

JSONField for responses: Storing question responses in JSONField allows flexible question configuration without schema migrations; trade-off is less efficient querying, but access patterns are primarily by student (acceptable)
No FK to CurrentStudent: Using CharField for stu_annual_key/ssid instead of ForeignKey avoids modification to existing tables and prevents cascade issues if student records are archived
Separate classroom model: NextYearClassroom is independent from Schedule table to allow creation before teacher assignments are finalized
Role-based placement permission: Only School Admin+ can place students; Teachers can only enter notes—prevents unauthorized placements while allowing data collection
Reuse dataexploration patterns: Access control logic, caching strategy, and HTMX patterns are replicated for consistency and to leverage proven performance optimizations
Tags as separate model vs JSONField (NEW): Used separate PlacementTag model with M2M relationship instead of JSONField on PlacementCard because: (1) enables efficient filtering with database indices, (2) maintains referential integrity, (3) allows eager loading with prefetch_related, (4) supports tag reuse and centralized management—performance critical for filtering 500+ students
Soft delete for tags (NEW): Set is_active=False instead of hard delete to preserve historical context; placement cards from previous years should still show which tags were assigned even if tag is later removed
Color storage (NEW): Store Tailwind classes (e.g., "bg-blue-500") rather than hex codes to leverage existing utility classes and maintain design consistency; provide preset palette to avoid arbitrary color choices that harm UX
Tag cache TTL 3600s (NEW): Tags change much less frequently than student lists (typically set once at year start, occasionally adjusted), so longer cache minimizes database hits; invalidate on any tag CRUD operation
