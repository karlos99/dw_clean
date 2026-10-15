#!/usr/bin/env bash
# Download pinned Highcharts build into django_pro/templates/static/vendor/highcharts/
set -euo pipefail
ROOT="$(cd "$(dirname "$0")/.." && pwd)"
VERSION="${HIGHCHARTS_VERSION:-11.4.8}"
BASE="https://cdn.jsdelivr.net/npm/highcharts@${VERSION}"
DEST="${ROOT}/django_pro/templates/static/vendor/highcharts"

mkdir -p "${DEST}/modules"
for f in highcharts.js highcharts-more.js; do
  curl -fsSL "${BASE}/${f}" -o "${DEST}/${f}"
done
for m in heatmap treemap funnel solid-gauge histogram-bellcurve exporting accessibility; do
  curl -fsSL "${BASE}/modules/${m}.js" -o "${DEST}/modules/${m}.js"
done
echo "Highcharts ${VERSION} -> ${DEST}"
