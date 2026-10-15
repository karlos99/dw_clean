# Frontend vendor assets (offline / Docker)

Static copies of libraries that used to load from CDNs. Files live under
`django_pro/templates/static/vendor/`, which is included in Django
`STATICFILES_DIRS` (`config/settings.py`). In Docker Compose, `./django_pro`
is bind-mounted into the container, so these files are served by WhiteNoise
without rebuilding the image.

## Layout

| Path | Used for |
|------|----------|
| `alpine/` | Alpine.js + collapse plugin (`base.html`) |
| `echarts/` | ECharts charts (`partials/vendor_echarts.html`) |
| `gridstack/` | Dashboard/page grids (`partials/vendor_gridstack.html`) |
| `html2canvas/` | Summary-table PNG export (`partials/vendor_echarts.html`) |
| `highcharts/` | Legacy DataViz builder (`partials/vendor_highcharts.html`) |
| `htmx/` | HTMX (`base.html`) |
| `sortable/` | Drag-and-drop lists (DESI admin) |
| `tailwind/` | Tailwind browser build (`base.html`) |
| `three/` | Landing / animations WebGL |
| `fonts/inter/` | Inter `@font-face` in `css/base.css` |

Templates should use `{% static 'vendor/...' %}` or the `partials/vendor_*.html`
includes—not external script URLs.

## Refreshing Highcharts

```bash
./scripts/sync-vendor-highcharts.sh
```

Other packages were vendored manually; pin versions when updating and smoke-test
chart/dashboard pages after changes.
