import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query'
import {
  createRootRoute,
  createRoute,
  createRouter,
  Link,
  Outlet,
  RouterProvider,
} from '@tanstack/react-router'
import {
  createColumnHelper,
  flexRender,
  getCoreRowModel,
  useReactTable,
} from '@tanstack/react-table'
import { useMemo, useState } from 'react'
import { api, type StudentRow } from './api'
import './App.css'

function Shell() {
  const qc = useQueryClient()
  const me = useQuery({ queryKey: ['me'], queryFn: api.me, retry: false })
  const nav = useQuery({
    queryKey: ['nav'],
    queryFn: api.nav,
    enabled: me.isSuccess,
  })
  const logout = useMutation({
    mutationFn: api.logout,
    onSuccess: () => {
      qc.clear()
      me.refetch()
    },
  })

  if (me.isLoading) return <div className="center">Loading…</div>
  if (me.isError) return <Login onDone={() => me.refetch()} />

  const user = me.data!

  return (
    <div className="layout">
      <aside className="sidebar">
        <div className="brand">DW Platform</div>
        <nav>
          <Link to="/" className="nav-link">
            Home
          </Link>
          <Link to="/exploration" className="nav-link">
            Exploration
          </Link>
          {nav.data?.map((item) => (
            <a key={item.id} className="nav-link muted" href={item.url_path}>
              {item.name}
            </a>
          ))}
        </nav>
      </aside>
      <main>
        <header className="topbar">
          <div>
            <strong>{user.name || user.email}</strong>
            <span className="muted"> · {user.active_role || 'No role'}</span>
          </div>
          <div className="topbar-actions">
            {user.roles.length > 1 && (
              <select
                value={user.active_role || ''}
                onChange={(e) =>
                  api.switchRole(e.target.value).then(() => qc.invalidateQueries({ queryKey: ['me'] }))
                }
              >
                {user.roles.map((r) => (
                  <option key={r} value={r}>
                    {r}
                  </option>
                ))}
              </select>
            )}
            <button type="button" onClick={() => logout.mutate()}>
              Log out
            </button>
          </div>
        </header>
        <div className="content">
          <Outlet />
        </div>
      </main>
    </div>
  )
}

function Login({ onDone }: { onDone: () => void }) {
  const [email, setEmail] = useState('')
  const [error, setError] = useState<string | null>(null)
  const devLogin = useMutation({
    mutationFn: () => api.devLogin(email),
    onSuccess: () => onDone(),
    onError: (e: Error) => setError(e.message),
  })

  return (
    <div className="login">
      <h1>DW Platform</h1>
      <p className="muted">Sign in with Google (pre-provisioned accounts only).</p>
      <a className="btn primary" href="/api/auth/google/start">
        Continue with Google
      </a>
      <div className="divider">or dev login</div>
      <form
        onSubmit={(e) => {
          e.preventDefault()
          setError(null)
          devLogin.mutate()
        }}
      >
        <input
          type="email"
          placeholder="user@example.com"
          value={email}
          onChange={(e) => setEmail(e.target.value)}
          required
        />
        <button type="submit" disabled={devLogin.isPending}>
          Dev login
        </button>
      </form>
      {error && <p className="error">{error}</p>}
    </div>
  )
}

function HomePage() {
  const me = useQuery({ queryKey: ['me'], queryFn: api.me })
  return (
    <section>
      <h2>Home</h2>
      <p className="muted">
        Signed in as {me.data?.email}. Use Exploration for the first student query slice.
      </p>
    </section>
  )
}

const col = createColumnHelper<StudentRow>()

function ExplorationPage() {
  const [schoolYear, setSchoolYear] = useState('')
  const [search, setSearch] = useState('')
  const [submitted, setSubmitted] = useState({ school_year: '', search: '' })

  const query = useQuery({
    queryKey: ['students', submitted],
    queryFn: () =>
      api.queryStudents({
        school_year: submitted.school_year || undefined,
        search: submitted.search || undefined,
        limit: 50,
        offset: 0,
      }),
  })

  const columns = useMemo(
    () => [
      col.accessor('student_id', { header: 'Student ID' }),
      col.accessor('student_name', { header: 'Name' }),
      col.accessor('grade', { header: 'Grade' }),
      col.accessor('school', { header: 'School' }),
      col.accessor('school_year', { header: 'Year' }),
      col.accessor('status', { header: 'Status' }),
    ],
    [],
  )

  const table = useReactTable({
    data: query.data?.rows ?? [],
    columns,
    getCoreRowModel: getCoreRowModel(),
  })

  return (
    <section>
      <h2>Data Exploration</h2>
      <form
        className="filters"
        onSubmit={(e) => {
          e.preventDefault()
          setSubmitted({ school_year: schoolYear, search })
        }}
      >
        <input
          placeholder="School year (e.g. 2025-2026)"
          value={schoolYear}
          onChange={(e) => setSchoolYear(e.target.value)}
        />
        <input
          placeholder="Search name or ID"
          value={search}
          onChange={(e) => setSearch(e.target.value)}
        />
        <button type="submit">Query</button>
      </form>
      {query.isLoading && <p>Loading…</p>}
      {query.isError && <p className="error">{(query.error as Error).message}</p>}
      {query.data && (
        <>
          <p className="muted">{query.data.total} students</p>
          <div className="table-wrap">
            <table>
              <thead>
                {table.getHeaderGroups().map((hg) => (
                  <tr key={hg.id}>
                    {hg.headers.map((h) => (
                      <th key={h.id}>{flexRender(h.column.columnDef.header, h.getContext())}</th>
                    ))}
                  </tr>
                ))}
              </thead>
              <tbody>
                {table.getRowModel().rows.map((row) => (
                  <tr key={row.id}>
                    {row.getVisibleCells().map((cell) => (
                      <td key={cell.id}>{flexRender(cell.column.columnDef.cell, cell.getContext())}</td>
                    ))}
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
        </>
      )}
    </section>
  )
}

const rootRoute = createRootRoute({ component: Shell })
const indexRoute = createRoute({ getParentRoute: () => rootRoute, path: '/', component: HomePage })
const explorationRoute = createRoute({
  getParentRoute: () => rootRoute,
  path: '/exploration',
  component: ExplorationPage,
})
const routeTree = rootRoute.addChildren([indexRoute, explorationRoute])
const router = createRouter({ routeTree })

declare module '@tanstack/react-router' {
  interface Register {
    router: typeof router
  }
}

export default function App() {
  return <RouterProvider router={router} />
}
