"""Shared Tailwind classes for course catalog form controls."""

# Requires `border` — color-only utilities do not show an edge in Tailwind.
CC_INPUT = (
    'block w-full rounded-lg border border-slate-300 bg-white px-3 py-2 text-sm '
    'text-slate-900 shadow-sm placeholder:text-slate-400 '
    'focus:border-blue-500 focus:outline-none focus:ring-2 focus:ring-blue-500/25'
)
CC_INPUT_SM = (
    'block rounded-lg border border-slate-300 bg-white px-2 py-1.5 text-xs '
    'text-slate-900 shadow-sm placeholder:text-slate-400 '
    'focus:border-blue-500 focus:outline-none focus:ring-2 focus:ring-blue-500/25'
)
CC_TEXTAREA = CC_INPUT + ' min-h-[5rem] resize-y'
CC_SELECT = CC_INPUT
CC_NUMBER_NARROW = CC_INPUT_SM + ' w-24'
CC_CODE_NARROW = CC_INPUT_SM + ' w-28'
