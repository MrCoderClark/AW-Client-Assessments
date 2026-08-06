// ponytail: inline SVGs, no icon-library dep. Two sizes: 16 (nav/inline), 18 (topbar).
import type { SVGProps } from "react";

type P = SVGProps<SVGSVGElement> & { size?: number };

const svg = (paths: React.ReactNode, size = 16, p?: P) => (
  <svg xmlns="http://www.w3.org/2000/svg" width={p?.size ?? size} height={p?.size ?? size}
       viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth={1.75}
       strokeLinecap="round" strokeLinejoin="round" {...p}>{paths}</svg>
);

export const IconDashboard = (p?: P) => svg(<>
  <rect x="3" y="3" width="7" height="9" rx="1" />
  <rect x="14" y="3" width="7" height="5" rx="1" />
  <rect x="14" y="12" width="7" height="9" rx="1" />
  <rect x="3" y="16" width="7" height="5" rx="1" />
</>, 16, p);

export const IconFiles = (p?: P) => svg(<>
  <path d="M14 3v4a1 1 0 0 0 1 1h4" />
  <path d="M5 8V4a2 2 0 0 1 2-2h7l6 6v11a2 2 0 0 1-2 2H7a2 2 0 0 1-2-2z" />
  <path d="M9 13h6M9 17h4" />
</>, 16, p);

export const IconMonitor = (p?: P) => svg(<>
  <rect x="2" y="4" width="20" height="13" rx="2" />
  <path d="M8 21h8M12 17v4" />
</>, 16, p);

export const IconSettings = (p?: P) => svg(<>
  <path d="M12 15a3 3 0 1 0 0-6 3 3 0 0 0 0 6z" />
  <path d="M19.4 15a1.7 1.7 0 0 0 .3 1.8l.1.1a2 2 0 1 1-2.8 2.8l-.1-.1a1.7 1.7 0 0 0-1.8-.3 1.7 1.7 0 0 0-1 1.5V21a2 2 0 1 1-4 0v-.1a1.7 1.7 0 0 0-1-1.5 1.7 1.7 0 0 0-1.8.3l-.1.1a2 2 0 1 1-2.8-2.8l.1-.1a1.7 1.7 0 0 0 .3-1.8 1.7 1.7 0 0 0-1.5-1H3a2 2 0 1 1 0-4h.1a1.7 1.7 0 0 0 1.5-1 1.7 1.7 0 0 0-.3-1.8l-.1-.1a2 2 0 1 1 2.8-2.8l.1.1a1.7 1.7 0 0 0 1.8.3H9a1.7 1.7 0 0 0 1-1.5V3a2 2 0 1 1 4 0v.1a1.7 1.7 0 0 0 1 1.5 1.7 1.7 0 0 0 1.8-.3l.1-.1a2 2 0 1 1 2.8 2.8l-.1.1a1.7 1.7 0 0 0-.3 1.8V9a1.7 1.7 0 0 0 1.5 1H21a2 2 0 1 1 0 4h-.1a1.7 1.7 0 0 0-1.5 1z" />
</>, 16, p);

export const IconBell = (p?: P) => svg(<>
  <path d="M6 8a6 6 0 0 1 12 0c0 7 3 9 3 9H3s3-2 3-9" />
  <path d="M10 21a2 2 0 0 0 4 0" />
</>, 18, p);

export const IconSearch = (p?: P) => svg(<>
  <circle cx="11" cy="11" r="7" />
  <path d="m21 21-4.3-4.3" />
</>, 16, p);

export const IconRefresh = (p?: P) => svg(<>
  <path d="M3 12a9 9 0 0 1 15-6.7L21 8" />
  <path d="M21 3v5h-5" />
  <path d="M21 12a9 9 0 0 1-15 6.7L3 16" />
  <path d="M3 21v-5h5" />
</>, 16, p);

export const IconUpload = (p?: P) => svg(<>
  <path d="M21 15v4a2 2 0 0 1-2 2H5a2 2 0 0 1-2-2v-4" />
  <path d="M17 8l-5-5-5 5" />
  <path d="M12 3v12" />
</>, 16, p);

export const IconPlay = (p?: P) => svg(<>
  <polygon points="6 4 20 12 6 20 6 4" />
</>, 16, p);

export const IconCheck = (p?: P) => svg(<>
  <path d="M20 6L9 17l-5-5" />
</>, 16, p);

export const IconDot = (p?: P) => svg(<>
  <circle cx="12" cy="12" r="4" fill="currentColor" />
</>, 8, p);

export const IconLogs = (p?: P) => svg(<>
  <path d="M8 6h13M8 12h13M8 18h13" />
  <circle cx="3.5" cy="6" r="1.2" fill="currentColor" />
  <circle cx="3.5" cy="12" r="1.2" fill="currentColor" />
  <circle cx="3.5" cy="18" r="1.2" fill="currentColor" />
</>, 16, p);

export const IconUsers = (p?: P) => svg(<>
  <path d="M16 21v-2a4 4 0 0 0-4-4H6a4 4 0 0 0-4 4v2" />
  <circle cx="9" cy="7" r="4" />
  <path d="M22 21v-2a4 4 0 0 0-3-3.87" />
  <path d="M16 3.13a4 4 0 0 1 0 7.75" />
</>, 16, p);

export const IconArchive = (p?: P) => svg(<>
  <rect x="2" y="3" width="20" height="5" rx="1" />
  <path d="M4 8v11a2 2 0 0 0 2 2h12a2 2 0 0 0 2-2V8" />
  <path d="M10 12h4" />
</>, 16, p);

export const IconLayout = (p?: P) => svg(<>
  <rect x="3" y="3" width="18" height="18" rx="2" />
  <path d="M3 9h18" />
  <path d="M9 21V9" />
</>, 16, p);
