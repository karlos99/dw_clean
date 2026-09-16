"""
SQL Translator: converts PostgreSQL-specific SQL to ClickHouse-compatible SQL.

Handles:
  - ILIKE           → lower(x) LIKE lower(y)
  - col->>'key'     → JSONExtractString(col, 'key')
  - col->'key'      → JSONExtractRaw(col, 'key')
  - DISTINCT ON     → QUALIFY row_number() = 1
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


def _strip_pg_casts(ref: str) -> str:
    """Drop PG casts/quotes so a column ref is ClickHouse-safe."""
    ref = re.sub(r'::\w+', '', ref or '')
    return ref.replace('"', '')


def translate_pg_to_clickhouse(sql, params):
    """
    Translate PostgreSQL SQL + params to ClickHouse SQL + params dict.

    Returns (clickhouse_sql, clickhouse_params_dict) or raises SQLTranslationError
    if the query contains untranslatable constructs.
    """
    if sql is None:
        raise SQLTranslationError("SQL is None")

    # Pre-processing block removed. Placeholders will be processed in a single pass at the end.

    # ── Remove BEGIN READ ONLY / ROLLBACK ──────────────────────────────
    sql = re.sub(r'\bBEGIN\s+READ\s+ONLY\b\s*;?', '', sql, flags=re.IGNORECASE)
    sql = re.sub(r'\bROLLBACK\b\s*;?', '', sql, flags=re.IGNORECASE)
    sql = re.sub(r'\bBEGIN\b\s*;?', '', sql, flags=re.IGNORECASE)
    sql = re.sub(r'\bCOMMIT\b\s*;?', '', sql, flags=re.IGNORECASE)

    # ── ILIKE → lower(x) LIKE LOWER(y) ──────────────────────────────
    # Wrap both column and param in lower() since LIKE is case-sensitive in CH.
    # Use [\w.]+ for the column side — \S+ incorrectly captures leading "("
    # from grouped OR clauses like "(cs.name ILIKE %s OR cs.ssid ILIKE %s)".
    if 'ILIKE' in sql.upper():
        _ilike_col = r'[\w.]+'
        _ilike_col_group = rf'({_ilike_col}(?:\s+(?:IS\s+NOT\s+NULL|IS\s+NULL))?)'
        sql = re.sub(
            rf'{_ilike_col_group}\s+ILIKE\s+(%s)',
            r'lower(\1) LIKE LOWER(\2)', sql, flags=re.IGNORECASE,
        )
        # String literals — must lower both sides (custom SQL uses ILIKE 'ELPAC', etc.)
        sql = re.sub(
            rf'{_ilike_col_group}\s+ILIKE\s+(\'[^\']*\')',
            r'lower(\1) LIKE lower(\2)', sql, flags=re.IGNORECASE,
        )
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

    # JSON-list idioms (before general JSON rewrite) — keep CH on the fast path.
    # Matches the CASE text[] normalizer from json_list_array_sql().
    _rl_ref = r'((?:\"[^\"]+\"|\w+)(?:\.(?:\"[^\"]+\"|\w+))?)'
    _rl_key = r'([A-Za-z_][A-Za-z0-9_]*)'
    _rl_case = (
        rf'\(\s*CASE\s+'
        rf'WHEN\s+{_rl_ref}\s+IS\s+NULL\s+OR\s+\1->\'{_rl_key}\'\s+IS\s+NULL\s+'
        rf'OR\s+jsonb_typeof\s*\(\s*\1->\'\2\'\s*\)\s*=\s*\'null\'\s+'
        rf'THEN\s+ARRAY\s*\[\s*\]\s*::\s*text\s*\[\s*\]\s+'
        rf'WHEN\s+jsonb_typeof\s*\(\s*\1->\'\2\'\s*\)\s*=\s*\'array\'\s+'
        rf'THEN\s+ARRAY\s*\(\s*SELECT\s+jsonb_array_elements_text\s*\(\s*\1->\'\2\'\s*\)\s*\)\s+'
        rf'WHEN\s+jsonb_typeof\s*\(\s*\1->\'\2\'\s*\)\s*=\s*\'string\'\s+'
        rf'AND\s+BTRIM\s*\(\s*COALESCE\s*\(\s*\1->>\'\2\'\s*,\s*\'\'\s*\)\s*\)\s*<>\s*\'\'\s+'
        rf'THEN\s+regexp_split_to_array\s*\(\s*BTRIM\s*\(\s*\1->>\'\2\'\s*\)\s*,\s*'
        rf'\'\[\[:space:\]\]\*,\[\[:space:\]\]\*\'\s*\)\s+'
        rf'ELSE\s+ARRAY\s*\[\s*\]\s*::\s*text\s*\[\s*\]\s+END\s*\)'
    )

    def _ch_json_list_arr(ref: str, key: str) -> str:
        # ifNull the JSON source first — JSONExtract(..., 'Array(String)') on a
        # Nullable JSON column is typed Nullable(Array(String)), which ClickHouse
        # rejects ("Nested type Array cannot be inside Nullable").
        r = f"ifNull({_strip_pg_casts(ref)}, '{{}}')"
        return (
            f"if(JSONType(JSONExtractRaw({r}, '{key}')) = 'Array', "
            f"JSONExtract({r}, '{key}', 'Array(String)'), "
            f"if(JSONType(JSONExtractRaw({r}, '{key}')) = 'String' "
            f"AND trim(BOTH ' ' FROM JSONExtractString({r}, '{key}')) != '', "
            f"splitByRegexp('\\\\s*,\\\\s*', trim(BOTH ' ' FROM JSONExtractString({r}, '{key}'))), "
            f"emptyArrayString()))"
        )

    sql = re.sub(
        rf'array_to_string\s*\(\s*{_rl_case}\s*,\s*\',\s*\'\s*\)',
        lambda m: f"arrayStringConcat({_ch_json_list_arr(m.group(1), m.group(2))}, ', ')",
        sql,
        flags=re.IGNORECASE | re.DOTALL,
    )
    sql = re.sub(
        rf'\(\s*{_rl_case}\s*&&\s*%s::text\s*\[\s*\]\s*\)',
        lambda m: f"hasAny({_ch_json_list_arr(m.group(1), m.group(2))}, __HASANY_ARR__%s)",
        sql,
        flags=re.IGNORECASE | re.DOTALL,
    )
    sql = re.sub(
        rf'\(\s*cardinality\s*\(\s*{_rl_case}\s*\)\s*=\s*0\s*\)',
        lambda m: f"empty({_ch_json_list_arr(m.group(1), m.group(2))})",
        sql,
        flags=re.IGNORECASE | re.DOTALL,
    )
    sql = re.sub(
        rf'unnest\s*\(\s*{_rl_case}\s*\)',
        lambda m: f"arrayJoin({_ch_json_list_arr(m.group(1), m.group(2))})",
        sql,
        flags=re.IGNORECASE | re.DOTALL,
    )

    # PostgreSQL casts are common in scalar JSON expressions. Remove them
    # after the JSON-list idiom has been rewritten, so that idiom's matcher
    # can still recognize the original PostgreSQL CASE expression.
    sql = re.sub(r'::\s*(?:jsonb|json|text\[\]|text)\b', '', sql, flags=re.IGNORECASE)

    def _unquote_simple(m):
        inner = m.group(1)
        if '.' in inner or '->' in inner or '>' in inner:
            return m.group(0)
        return inner

    sql = re.sub(r'"([^"]+)"', _unquote_simple, sql)

    def _apply_json_translation(s):
        s = re.sub(
            r'(\w+(?:\.\w+)?)\s*->>\s*(\'[^\']+\'|%s)',
            lambda m: f"JSONExtractString({m.group(1)}, {m.group(2)})", s,
        )
        s = re.sub(
            r'(\w+(?:\.\w+)?)\s*->\s*(\'[^\']+\'|%s)',
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

    # ── PG scalar helpers used in Explorer ORDER BY / CASE sorts ──────
    sql = re.sub(r'\bbtrim\s*\(', "trim(BOTH ' ' FROM ", sql, flags=re.IGNORECASE)
    sql = re.sub(
        r"regexp_replace\s*\(\s*(.+?)\s*,\s*('[^']*')\s*,\s*('[^']*')\s*,\s*'g'\s*\)",
        r'replaceRegexpAll(\1, \2, \3)',
        sql,
        flags=re.IGNORECASE | re.DOTALL,
    )
    sql = re.sub(r'\bAS\s+NUMERIC\b', 'AS Nullable(Float64)', sql, flags=re.IGNORECASE)

    # ── string_agg(x, sep) → arrayStringConcat(groupArray(x), sep) ────
    def _string_agg(m):
        expr = m.group(1)
        sep = m.group(2)
        return f'arrayStringConcat(groupArray({expr}), {sep})'

    sql = re.sub(r'string_agg\s*\(\s*([^,]+)\s*,\s*([^)]+)\s*\)', _string_agg, sql, flags=re.IGNORECASE)

    # ── JSONB object keys → ClickHouse JSON functions ──────────────────
    # CROSS JOIN LATERAL jsonb_object_keys(col) AS k(key) → ARRAY JOIN
    # JSONExtractKeys(assumeNotNull(col)) AS k, then k.key → k.
    _oj_aliases = []

    def _oj_repl(m):
        expr = m.group(1)
        alias = m.group(2)
        col = m.group(3)
        _oj_aliases.append((alias, col))
        return f"ARRAY JOIN JSONExtractKeys(assumeNotNull({expr})) AS {alias}"

    sql = re.sub(
        r'\bCROSS\s+JOIN\s+LATERAL\s+jsonb_object_keys\s*\(\s*([^)]+?)\s*\)\s+AS\s+(\w+)\s*\(\s*(\w+)\s*\)',
        _oj_repl, sql, flags=re.IGNORECASE,
    )
    for _alias, _col in _oj_aliases:
        sql = re.sub(rf'\b{_alias}\.{_col}\b', _alias, sql)

    # Bare jsonb_object_keys(col) → arrayJoin(JSONExtractKeys(col))
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

    # ── Replace all placeholders (both ANY and %s) in a single pass ───
    param_index = 0
    ch_params = {}
    
    pos = 0
    new_sql_parts = []
    idx = 0
    
    # Matches: '= ANY(%s)' OR '__HASANY_ARR__%s' (Race List) OR '%s'
    placeholder_re = re.compile(
        r'(?i)=\s*ANY\s*\(\s*%s\s*\)|__HASANY_ARR__%s|%s'
    )
    
    for m in placeholder_re.finditer(sql):
        new_sql_parts.append(sql[pos:m.start()])
        matched_str = m.group(0)
        
        if idx < len(params):
            val = params[idx]
        else:
            val = None
            
        is_has_any = matched_str.upper().startswith('__HASANY_ARR__')

        if is_has_any or matched_str.startswith('='):
            # '= ANY(%s)' or '__HASANY_ARR__%s' — expand list params
            if isinstance(val, (list, tuple, set)):
                if not val:
                    new_sql_parts.append('[]' if is_has_any else 'IN (NULL)')
                else:
                    ph_parts = []
                    for v in val:
                        name = f'p{param_index}'
                        param_index += 1
                        ph_parts.append(f'%({name})s')
                        ch_params[name] = v
                    if is_has_any:
                        new_sql_parts.append('[' + ', '.join(ph_parts) + ']')
                    else:
                        new_sql_parts.append('IN (' + ', '.join(ph_parts) + ')')
            else:
                name = f'p{param_index}'
                param_index += 1
                if is_has_any:
                    new_sql_parts.append(f'[%({name})s]')
                else:
                    new_sql_parts.append(f'IN (%({name})s)')
                ch_params[name] = val
        else:
            # It's a '%s' placeholder
            name = f'p{param_index}'
            param_index += 1
            new_sql_parts.append(f'%({name})s')
            ch_params[name] = val
            
        idx += 1
        pos = m.end()
        
    new_sql_parts.append(sql[pos:])
    sql = ''.join(new_sql_parts)

    # Handle ILIKE params: if original SQL had ILIKE, the LIKE pattern needs lower()
    # We already transformed ILIKE → lower() LIKE; the param value needs to be lowered too
    # for patterns: %{search}% → lower value
    for name, val in list(ch_params.items()):
        if isinstance(val, (tuple, set)):
            ch_params[name] = list(val)
        elif isinstance(val, str) and '%' in val:
            ch_params[name] = val.lower()

    # psycopg escaped percents in LIKE literals (novice%% → novice%)
    sql = sql.replace('%%', '%')

    return sql, ch_params


def _translate_distinct_on(sql):
    """
    Translate SELECT DISTINCT ON (cols) ... FROM ... ORDER BY ...
    to QUALIFY row_number() = 1.

    Nested ``SELECT *, row_number() AS _rn`` wrappers hide source columns
    from ClickHouse join aliases (``se.ell`` cannot be resolved).
    """
    if 'DISTINCT ON' not in sql.upper():
        return sql

    def _replace_distinct_on_subquery(m):
        distinct_on_cols = m.group(1)
        select_expr = m.group(2)
        from_table = m.group(3)
        where_clause = m.group(4) or ''
        order_clause = m.group(5)

        order_clean = re.sub(r'\bNULLS\s+(FIRST|LAST)\b', '', order_clause, flags=re.IGNORECASE).strip()
        where_sql = f' {where_clause.strip()}' if where_clause and where_clause.strip() else ''

        return (
            f'(SELECT {select_expr} FROM {from_table}{where_sql}'
            f' QUALIFY row_number() OVER ('
            f'PARTITION BY {distinct_on_cols} ORDER BY {order_clean}'
            f') = 1)'
        )

    # Match subquery form: ( SELECT DISTINCT ON (cols) select FROM table WHERE? ORDER BY cols )
    # The optional clause between FROM and ORDER BY is anchored to a literal
    # WHERE so it can't silently swallow text past the subquery's own ORDER
    # BY (e.g. an outer query's later "ORDER BY ... LIMIT n)") — a bare
    # `(.*?)` there is unbounded and will happily match across clause/paren
    # boundaries when this subquery is embedded in a larger statement.
    pattern = (
        r'\(\s*SELECT\s+DISTINCT\s+ON\s*\(([^)]+)\)\s+'
        r'(\*)\s+'
        r'FROM\s+(\S+)\s*'
        r'(WHERE\s+.*?)?\s*'
        r'ORDER\s+BY\s+([^)]+?)\s*'
        r'\)'
    )
    sql = re.sub(pattern, _replace_distinct_on_subquery, sql, flags=re.IGNORECASE | re.DOTALL)

    # If still contains DISTINCT ON after subquery handling, try top-level match
    if 'DISTINCT ON' in sql.upper():
        m = re.search(
            r'SELECT\s+DISTINCT\s+ON\s*\(([^)]+)\)\s+(.+?)\s+FROM\s+(\S+)\s+(WHERE\s+.*?)?\s*ORDER\s+BY\s+(.+?)(?:\s+LIMIT\s+\d+)?$',
            sql,
            flags=re.IGNORECASE | re.DOTALL,
        )
        if m:
            partition_cols = m.group(1).strip()
            select_cols = m.group(2).strip()
            from_table = m.group(3).strip()
            middle = (m.group(4) or '').strip()
            order_by = m.group(5).strip()

            order_by_clean = re.sub(r'\bNULLS\s+(FIRST|LAST)\b', '', order_by, flags=re.IGNORECASE).strip()
            middle_sql = f' {middle}' if middle else ''

            new_sql = (
                f'SELECT {select_cols} FROM {from_table}{middle_sql}'
                f' QUALIFY row_number() OVER (PARTITION BY {partition_cols}'
                f' ORDER BY {order_by_clean}) = 1'
                f' ORDER BY {order_by_clean}'
            )

            limit_match = re.search(r'\bLIMIT\s+(\d+)\s*$', sql, re.IGNORECASE)
            if limit_match:
                new_sql += f' LIMIT {limit_match.group(1)}'

            return new_sql

    return sql
