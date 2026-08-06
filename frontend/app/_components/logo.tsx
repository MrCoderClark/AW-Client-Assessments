// Same shape as app/icon.svg (browser tab) so the mark reads consistent everywhere.
export function Logo({ size = 28 }: { size?: number }) {
  return (
    <svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 32 32" width={size} height={size}
         style={{ display: "block", borderRadius: 6 }} aria-label="Client Viewer">
      <rect width="32" height="32" rx="6" fill="#2563eb"/>
      <path d="M10 8 h8 l5 5 v11 a1 1 0 0 1 -1 1 h-12 a1 1 0 0 1 -1 -1 v-15 a1 1 0 0 1 1 -1 z" fill="#ffffff"/>
      <path d="M18 8 v5 h5" fill="none" stroke="#2563eb" strokeWidth="1.5" strokeLinejoin="round"/>
      <path d="M12.5 17 h8 M12.5 20 h8 M12.5 23 h5" stroke="#94a3b8" strokeWidth="1.4" strokeLinecap="round"/>
    </svg>
  );
}
