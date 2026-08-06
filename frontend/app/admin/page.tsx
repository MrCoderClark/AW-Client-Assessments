import { redirect } from "next/navigation";

export default function AdminIndex() {
  // Layout gate has already run; if we're here the user is an admin —
  // send them to the only current admin surface.
  redirect("/admin/users");
}
