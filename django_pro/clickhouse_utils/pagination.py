"""Keyset (cursor) pagination helpers.

Numbered OFFSET pages stay valid. When a cursor is present, callers should
skip OFFSET and use the predicate instead so deep pages do not rescan.
"""

import base64
import json


def encode_cursor(sort_key, value, sak, direction='asc'):
    payload = {
        'k': sort_key or 'student_name',
        'v': '' if value is None else str(value),
        'sak': str(sak or ''),
        'd': 'desc' if direction == 'desc' else 'asc',
    }
    return base64.urlsafe_b64encode(
        json.dumps(payload, separators=(',', ':')).encode()
    ).decode()


def decode_cursor(raw):
    if not raw:
        return None
    try:
        parsed = json.loads(base64.urlsafe_b64decode(raw.encode()).decode())
    except (ValueError, TypeError, UnicodeDecodeError, json.JSONDecodeError):
        return None
    if not isinstance(parsed, dict):
        return None
    # Older Data Explorer cursors were {name, sak}.
    if 'name' in parsed and 'k' not in parsed:
        return {
            'k': 'student_name',
            'v': str(parsed.get('name') or ''),
            'sak': str(parsed.get('sak') or ''),
            'd': 'asc',
        }
    if parsed.get('v') is None and parsed.get('sak') is None:
        return None
    return {
        'k': str(parsed.get('k') or 'student_name'),
        'v': str(parsed.get('v') or ''),
        'sak': str(parsed.get('sak') or ''),
        'd': 'desc' if parsed.get('d') == 'desc' else 'asc',
    }


def keyset_clause(expr_sql, direction='asc', tie_sql='cs.stu_annual_key'):
    op = '<' if direction == 'desc' else '>'
    return (
        f" AND ({expr_sql} {op} %s OR "
        f"({expr_sql} = %s AND {tie_sql} {op} %s))"
    )


def cursor_from_row(row, sort_key, direction='asc', sak_key='stu_annual_key'):
    if not row:
        return ''
    key = sort_key or 'student_name'
    value = row.get(key)
    if value is None and key != 'student_name':
        value = row.get('student_name')
    sak = row.get(sak_key)
    if sak is None:
        for alias in row:
            if alias.split('.')[-1] == sak_key:
                sak = row.get(alias)
                break
    return encode_cursor(key, value, sak, direction)
