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
