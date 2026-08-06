// Per-page RequirePerm decides who can see each admin surface — the layout
// deliberately does not gate at the route-group level so an operator with
// `pdf:archive` (but no `user:read`) can reach /admin/archive.
export default function AdminLayout({ children }: { children: React.ReactNode }) {
  return <>{children}</>;
}
