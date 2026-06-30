"""
SQL Translator: converts PostgreSQL-specific SQL to ClickHouse-compatible SQL.

Handles:
  - ILIKE           → lower(x) LIKE lower(y)
  - col->>'key'     → JSONExtractString(col, 'key')
  - col->'key'      → JSONExtractRaw(col, 'key')
  - DISTINCT ON     → row_number() window subquery
  - %s placeholders → {p0}, {p1} named params
  - = ANY(%s)       → IN expansion
  - NULLS FIRST/LAST → stripped (CH defaults acceptable)
  - BEGIN/ROLLBACK   → removed
  - string_agg()    → arrayStringConcat(groupArray())

Also converts Django-style parameter lists to ClickHouse named-parameter dicts.
"""

import re
import logging

logger = logging.getLogger(__name__)


class SQLTranslationError(Exception):
    pass


def translate_pg_to_clickhouse(sql, params):
    """
    Translate PostgreSQL SQL + params to ClickHouse SQL + params dict.

    Returns (clickhouse_sql, clickhouse_params_dict) or raises SQLTranslationError
    if the query contains untranslatable constructs.
    """
    if sql is None:
        raise SQLTranslationError("SQL is None")

    param_index = 0

    def _next_param(val):
        nonlocal param_index
        name = f'p{param_index}'
        param_index += 1
        return f'%({name})s', name, val

    ch_params = {}

    # ── Pre-process: expand ANY(%s) with list params ───────────────────
    # PostgreSQL: col = ANY(%s)  with params = [['A','B']]
    # ClickHouse: col IN ({p0},{p1})
    # We need to handle this BEFORE replacing %s placeholders.
    pos = 0
    idx = 0
    new_sql_parts = []
    new_params = []
    last_end = 0
    for m in re.finditer(r'=\s*ANY\s*\(\s*%s\s*\)', sql):
        new_sql_parts.append(sql[last_end:m.start()])
        if idx < len(params) and isinstance(params[idx], list):
            vals = params[idx]
            ph_parts = []
            for v in vals:
                ph, name, _ = _next_param(v)
                ph_parts.append(ph)
                ch_params[name] = v
            new_sql_parts.append('IN (' + ', '.join(ph_parts) + ')')
            idx += 1
        else:
            ph, name, _ = _next_param(params[idx] if idx < len(params) else None)
            ph_parts.append(ph)
            ch_params[name] = params[idx] if idx < len(params) else None
            new_sql_parts.append('IN (' + ph + ')')
            idx += 1
        last_end = m.end()

    new_sql_parts.append(sql[last_end:])
    sql = ''.join(new_sql_parts)

    # Remaining params that weren't consumed by ANY expansion
    remaining = params[idx:] if idx > 0 else params
    new_params = list(remaining)

    # ── Remove BEGIN READ ONLY / ROLLBACK ──────────────────────────────
    sql = re.sub(r'\bBEGIN\s+READ\s+ONLY\b\s*;?', '', sql, flags=re.IGNORECASE)
    sql = re.sub(r'\bROLLBACK\b\s*;?', '', sql, flags=re.IGNORECASE)
    sql = re.sub(r'\bBEGIN\b\s*;?', '', sql, flags=re.IGNORECASE)
    sql = re.sub(r'\bCOMMIT\b\s*;?', '', sql, flags=re.IGNORECASE)

    # ── ILIKE → lower(x) LIKE LOWER(y) ──────────────────────────────
    # Wrap both column and param in lower() since LIKE is case-sensitive in CH.
    sql = re.sub(
        r'(\S+(?:\s+(?:IS\s+NOT\s+NULL|IS\s+NULL))?)\s+ILIKE\s+(%s)',
        r'lower(\1) LIKE LOWER(\2)', sql, flags=re.IGNORECASE,
    )
    # Also handle column ILIKE without %s (bare patterns)
    def _replace_ilike(m):
        expr = m.group(1)
        return f'lower({expr}) LIKE'
    sql = re.sub(r'(\S+(?:\s+(?:IS\s+NOT\s+NULL|IS\s+NULL))?)\s+ILIKE\b', _replace_ilike, sql, flags=re.IGNORECASE)

    # Handle any remaining bare ILIKE keywords
    sql = re.sub(r'\bILIKE\b', 'LIKE', sql, flags=re.IGNORECASE)

    # ── JSON operators ─────────────────────────────────────────────────
    # col->>'key'  →  JSONExtractString(col, 'key')
    # col->'key'   →  JSONExtractRaw(col, 'key')
    #
    # PostgreSQL SQL uses individually-quoted identifiers like "col",
    # which break the \w+ regex. Aliases are fully-quoted like
    # "table.col->'key'" and must NOT be translated.
    #
    # Strategy:
    #   1. Unquote simple identifiers ("col" → col) that don't contain
    #      dots or arrows (aliases stay quoted).
    #   2. Split into quoted / unquoted segments on remaining quotes.
    #   3. Apply JSON translation only to unquoted segments.
    #   4. Reassemble.

    def _unquote_simple(m):
        inner = m.group(1)
        if '.' in inner or '->' in inner or '>' in inner:
            return m.group(0)
        return inner

    sql = re.sub(r'"([^"]+)"', _unquote_simple, sql)

    def _apply_json_translation(s):
        s = re.sub(
            r'(\w+(?:\.\w+)?)\s*->>\s*(\'[^\']+\')',
            lambda m: f"JSONExtractString({m.group(1)}, {m.group(2)})", s,
        )
        s = re.sub(
            r'(\w+(?:\.\w+)?)\s*->\s*(\'[^\']+\')',
            lambda m: f"JSONExtractRaw({m.group(1)}, {m.group(2)})", s,
        )
        return s

    parts = []
    buf = ''
    in_quote = False
    for ch in sql:
        if ch == '"':
            if in_quote:
                parts.append(('quoted', buf + '"'))
                buf = ''
            else:
                if buf:
                    parts.append(('unquoted', buf))
                buf = '"'
            in_quote = not in_quote
            continue
        buf += ch
    if buf:
        parts.append(('unquoted' if not in_quote else 'quoted', buf))

    sql = ''.join(
        _apply_json_translation(seg) if kind == 'unquoted' else seg
        for kind, seg in parts
    )

    # ── DISTINCT ON → row_number() window subquery ─────────────────────
    # Pattern: SELECT DISTINCT ON (cols) ... FROM ... ORDER BY ...
    sql = _translate_distinct_on(sql)

    # ── NULLS FIRST / NULLS LAST → stripped ────────────────────────────
    sql = re.sub(r'\bNULLS\s+(FIRST|LAST)\b', '', sql, flags=re.IGNORECASE)

    # ── string_agg(x, sep) → arrayStringConcat(groupArray(x), sep) ────
    def _string_agg(m):
        expr = m.group(1)
        sep = m.group(2)
        return f'arrayStringConcat(groupArray({expr}), {sep})'

    sql = re.sub(r'string_agg\s*\(\s*([^,]+)\s*,\s*([^)]+)\s*\)', _string_agg, sql, flags=re.IGNORECASE)

    # ── JSONB object keys → ClickHouse JSON functions ──────────────────
    # jsonb_object_keys(col) → arrayJoin(JSONExtractKeys(col))
    sql = re.sub(
        r'\bjsonb_object_keys\s*\(\s*(\w+(?:\.\w+)?)\s*\)',
        r'arrayJoin(JSONExtractKeys(\1))',
        sql,
        flags=re.IGNORECASE,
    )

    # ── PostgreSQL date functions → ClickHouse equivalents ─────────────
    sql = re.sub(r'\bEXTRACT\s*\(\s*YEAR\s+FROM\s+([^)]+)\s*\)', r'toYear(\1)', sql, flags=re.IGNORECASE)
    sql = re.sub(r'\bEXTRACT\s*\(\s*MONTH\s+FROM\s+([^)]+)\s*\)', r'toMonth(\1)', sql, flags=re.IGNORECASE)
    sql = re.sub(r'\bEXTRACT\s*\(\s*DAY\s+FROM\s+([^)]+)\s*\)', r'toDayOfMonth(\1)', sql, flags=re.IGNORECASE)
    sql = re.sub(r'\bDATE_PART\s*\(\s*\'year\'\s*,\s*([^)]+)\s*\)', r'toYear(\1)', sql, flags=re.IGNORECASE)
    sql = re.sub(r'\bDATE_PART\s*\(\s*\'month\'\s*,\s*([^)]+)\s*\)', r'toMonth(\1)', sql, flags=re.IGNORECASE)
    sql = re.sub(r'\bDATE_PART\s*\(\s*\'day\'\s*,\s*([^)]+)\s*\)', r'toDayOfMonth(\1)', sql, flags=re.IGNORECASE)

    # ── Replace remaining %s placeholders with %(pN)s ──────────────────
    remaining_new = list(new_params)
    rem_idx = 0

    def _replace_placeholder(m):
        nonlocal rem_idx, param_index
        if rem_idx < len(remaining_new):
            val = remaining_new[rem_idx]
            name = f'p{param_index}'
            rem_idx += 1
            param_index += 1
            ch_params[name] = val
            return f'%({name})s'
        return '%s'

    sql = re.sub(r'%s', _replace_placeholder, sql)

    # Handle ILIKE params: if original SQL had ILIKE, the LIKE pattern needs lower()
    # We already transformed ILIKE → lower() LIKE; the param value needs to be lowered too
    # for patterns: %{search}% → lower value
    for name, val in ch_params.items():
        if isinstance(val, str) and '%' in val:
            ch_params[name] = val.lower()

    return sql, ch_params


def _translate_distinct_on(sql):
    """
    Translate SELECT DISTINCT ON (cols) ... FROM ... ORDER BY ...
    to a subquery with row_number() window function.

    Handles both top-level and subquery DISTINCT ON patterns.
    """
    if 'DISTINCT ON' not in sql.upper():
        return sql

    # Match DISTINCT ON subqueries: can be top-level or inside ( ... )
    # Pattern: ( SELECT DISTINCT ON (...) select_expr FROM table [WHERE ...] ORDER BY cols )
    # The subquery ends at the closing paren before an alias (e.g. ") se")
    # or at the end of string for top-level queries.

    def _replace_distinct_on_subquery(m):
        distinct_on_cols = m.group(1)
        select_expr = m.group(2)
        from_table = m.group(3)
        where_clause = m.group(4) or ''
        order_clause = m.group(5)

        order_clean = re.sub(r'\bNULLS\s+(FIRST|LAST)\b', '', order_clause, flags=re.IGNORECASE).strip()

        return (
            f'(SELECT {select_expr} FROM ('
            f'SELECT *, row_number() OVER ('
            f'PARTITION BY {distinct_on_cols} ORDER BY {order_clean}'
            f') AS _rn FROM {from_table} {where_clause}'
            f') AS _dist WHERE _rn = 1)'
        )

    # Match subquery form: ( SELECT DISTINCT ON (cols) select FROM table WHERE? ORDER BY cols )
    pattern = (
        r'\(\s*SELECT\s+DISTINCT\s+ON\s*\(([^)]+)\)\s+'
        r'(\*)\s+'
        r'FROM\s+(\S+)\s*'
        r'(.*?)\s+'
        r'ORDER\s+BY\s+([^)]+?)\s*'
        r'\)'
    )
    sql = re.sub(pattern, _replace_distinct_on_subquery, sql, flags=re.IGNORECASE | re.DOTALL)

    # If still contains DISTINCT ON after subquery handling, try top-level match
    if 'DISTINCT ON' in sql.upper():
        m = re.search(
            r'SELECT\s+DISTINCT\s+ON\s*\(([^)]+)\)\s+(.+?)\s+FROM\s+(\S+)\s+(.*?)\s+ORDER\s+BY\s+(.+?)(?:\s+LIMIT\s+\d+)?$',
            sql,
            flags=re.IGNORECASE | re.DOTALL,
        )
        if m:
            partition_cols = m.group(1).strip()
            select_cols = m.group(2).strip()
            from_table = m.group(3).strip()
            middle = m.group(4).strip()
            order_by = m.group(5).strip()

            order_by_clean = re.sub(r'\bNULLS\s+(FIRST|LAST)\b', '', order_by, flags=re.IGNORECASE).strip()

            window_sql = (
                f'SELECT *, row_number() OVER (PARTITION BY {partition_cols}'
                f' ORDER BY {order_by_clean}) AS _rn FROM {from_table}'
            )
            if middle:
                window_sql += f' {middle}'

            new_sql = f'SELECT {select_cols} FROM ({window_sql}) AS _sub WHERE _rn = 1 ORDER BY {order_by_clean}'

            limit_match = re.search(r'\bLIMIT\s+(\d+)\s*$', sql, re.IGNORECASE)
            if limit_match:
                new_sql += f' LIMIT {limit_match.group(1)}'

            return new_sql

    return sql
