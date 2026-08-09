export type AuthUser = {
  id: number
  email: string
  name: string
  avatar: string | null
  roles: string[]
  schools: unknown
  grade_levels: unknown
  active_role: string | null
  active_school_id: string | null
  is_superuser: boolean
}

export type NavItem = {
  id: number
  name: string
  slug: string
  url_path: string
  icon: string
  menu_order: number
  parent_page_id: number | null
}

export type StudentRow = {
  stu_annual_key: string
  student_id: string | null
  student_name: string | null
  grade: string | null
  school: string | null
  school_year: string | null
  status: string | null
}

export type StudentQueryResponse = {
  columns: string[]
  rows: StudentRow[]
  total: number
  limit: number
  offset: number
}

async function request<T>(path: string, init?: RequestInit): Promise<T> {
  const res = await fetch(path, {
    credentials: 'include',
    headers: { 'Content-Type': 'application/json', ...(init?.headers || {}) },
    ...init,
  })
  if (!res.ok) {
    const body = await res.json().catch(() => ({}))
    throw new Error(body.error || res.statusText)
  }
  if (res.status === 204) return undefined as T
  return res.json()
}

export const api = {
  me: () => request<AuthUser>('/api/auth/me'),
  nav: () => request<NavItem[]>('/api/nav'),
  logout: () => request<void>('/api/auth/logout', { method: 'POST' }),
  devLogin: (email: string) =>
    request<AuthUser>('/api/auth/dev-login', {
      method: 'POST',
      body: JSON.stringify({ email }),
    }),
  switchRole: (role?: string, school_id?: string) =>
    request<AuthUser>('/api/auth/switch-role', {
      method: 'POST',
      body: JSON.stringify({ role, school_id }),
    }),
  queryStudents: (body: {
    school_year?: string
    search?: string
    limit?: number
    offset?: number
  }) =>
    request<StudentQueryResponse>('/api/v1/exploration/students/query', {
      method: 'POST',
      body: JSON.stringify(body),
    }),
}
