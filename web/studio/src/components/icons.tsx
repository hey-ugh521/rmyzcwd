import type { ReactNode, SVGProps } from "react";

type IconProps = SVGProps<SVGSVGElement> & { size?: number };

function Base({ size = 18, children, ...rest }: IconProps & { children: ReactNode }) {
  return (
    <svg
      width={size}
      height={size}
      viewBox="0 0 24 24"
      fill="none"
      stroke="currentColor"
      strokeWidth={1.7}
      strokeLinecap="round"
      strokeLinejoin="round"
      aria-hidden="true"
      {...rest}
    >
      {children}
    </svg>
  );
}

export const IconStudio = (p: IconProps) => (
  <Base {...p}>
    <path d="M3 10.5 12 3l9 7.5" />
    <path d="M5 9.5V20a1 1 0 0 0 1 1h4v-6h4v6h4a1 1 0 0 0 1-1V9.5" />
  </Base>
);
export const IconCreate = (p: IconProps) => (
  <Base {...p}>
    <path d="m12 3 1.9 4.6L18.5 9.5l-4.6 1.9L12 16l-1.9-4.6L5.5 9.5l4.6-1.9z" />
    <path d="M18 15.5 19 18l2.5 1-2.5 1-1 2.5-1-2.5L14.5 19l2.5-1z" />
  </Base>
);
export const IconGallery = (p: IconProps) => (
  <Base {...p}>
    <rect x="3" y="3" width="18" height="18" rx="2.5" />
    <circle cx="8.6" cy="8.6" r="1.6" />
    <path d="m21 15-4.5-4.5L7 20" />
  </Base>
);
export const IconChat = (p: IconProps) => (
  <Base {...p}>
    <path d="M21 11.5a8.4 8.4 0 0 1-9 8.4 9 9 0 0 1-3.9-.9L3 20.5l1.6-4.6A8.4 8.4 0 0 1 12 3.1a8.4 8.4 0 0 1 9 8.4z" />
    <path d="M8.5 11h.01M12 11h.01M15.5 11h.01" />
  </Base>
);
export const IconCharacters = (p: IconProps) => (
  <Base {...p}>
    <rect x="3" y="3" width="18" height="18" rx="2.5" />
    <circle cx="12" cy="9" r="2.5" />
    <path d="M6.5 18c.8-2.8 2.7-4 5.5-4s4.7 1.2 5.5 4" />
  </Base>
);
export const IconTasks = (p: IconProps) => (
  <Base {...p}>
    <path d="m3 6 2 2 3.5-3.5" />
    <path d="m3 15 2 2 3.5-3.5" />
    <path d="M12 7h9M12 16h9" />
  </Base>
);
export const IconSystem = (p: IconProps) => (
  <Base {...p}>
    <rect x="3" y="4" width="18" height="7" rx="2" />
    <rect x="3" y="13" width="18" height="7" rx="2" />
    <path d="M7 7.5h.01M7 16.5h.01" />
  </Base>
);
export const IconSettings = (p: IconProps) => (
  <Base {...p}>
    <path d="M4 6h10M18 6h2M4 12h2M10 12h10M4 18h7M15 18h5" />
    <circle cx="16" cy="6" r="2" />
    <circle cx="8" cy="12" r="2" />
    <circle cx="13" cy="18" r="2" />
  </Base>
);
export const IconSearch = (p: IconProps) => (
  <Base {...p}>
    <circle cx="11" cy="11" r="7" />
    <path d="m20 20-3.2-3.2" />
  </Base>
);
export const IconCommand = (p: IconProps) => (
  <Base {...p}>
    <path d="M9 6a3 3 0 1 0-3 3h12a3 3 0 1 0-3-3v12a3 3 0 1 0 3-3H6a3 3 0 1 0 3 3z" />
  </Base>
);
export const IconPlus = (p: IconProps) => (
  <Base {...p}><path d="M12 5v14M5 12h14" /></Base>
);
export const IconClose = (p: IconProps) => (
  <Base {...p}><path d="M18 6 6 18M6 6l12 12" /></Base>
);
export const IconCheck = (p: IconProps) => (
  <Base {...p}><path d="m20 6-11 11-5-5" /></Base>
);
export const IconChevronDown = (p: IconProps) => (
  <Base {...p}><path d="m6 9 6 6 6-6" /></Base>
);
export const IconChevronRight = (p: IconProps) => (
  <Base {...p}><path d="m9 6 6 6-6 6" /></Base>
);
export const IconChevronLeft = (p: IconProps) => (
  <Base {...p}><path d="m15 6-6 6 6 6" /></Base>
);
export const IconPlay = (p: IconProps) => (
  <Base {...p}><path d="M7 4.5v15l12-7.5z" /></Base>
);
export const IconPause = (p: IconProps) => (
  <Base {...p}><path d="M8 5v14M16 5v14" /></Base>
);
export const IconStop = (p: IconProps) => (
  <Base {...p}><rect x="6" y="6" width="12" height="12" rx="2" /></Base>
);
export const IconDownload = (p: IconProps) => (
  <Base {...p}>
    <path d="M12 3v12" /><path d="m7 11 5 5 5-5" /><path d="M4 20h16" />
  </Base>
);
export const IconRefresh = (p: IconProps) => (
  <Base {...p}>
    <path d="M21 12a9 9 0 1 1-2.6-6.4" /><path d="M21 4v5h-5" />
  </Base>
);
export const IconTrash = (p: IconProps) => (
  <Base {...p}>
    <path d="M4 7h16M9 7V5a1 1 0 0 1 1-1h4a1 1 0 0 1 1 1v2" />
    <path d="M6 7l1 13a1 1 0 0 0 1 1h8a1 1 0 0 0 1-1l1-13" />
    <path d="M10 11v6M14 11v6" />
  </Base>
);
export const IconCopy = (p: IconProps) => (
  <Base {...p}>
    <rect x="9" y="9" width="12" height="12" rx="2" />
    <path d="M5 15H4a1 1 0 0 1-1-1V4a1 1 0 0 1 1-1h10a1 1 0 0 1 1 1v1" />
  </Base>
);
export const IconDice = (p: IconProps) => (
  <Base {...p}>
    <rect x="3" y="3" width="18" height="18" rx="3" />
    <path d="M8.5 8.5h.01M15.5 8.5h.01M12 12h.01M8.5 15.5h.01M15.5 15.5h.01" />
  </Base>
);
export const IconLock = (p: IconProps) => (
  <Base {...p}>
    <rect x="4.5" y="10" width="15" height="10" rx="2" />
    <path d="M8 10V7a4 4 0 0 1 8 0v3" />
  </Base>
);
export const IconUnlock = (p: IconProps) => (
  <Base {...p}>
    <rect x="4.5" y="10" width="15" height="10" rx="2" />
    <path d="M8 10V7a4 4 0 0 1 7.5-2" />
  </Base>
);
export const IconSun = (p: IconProps) => (
  <Base {...p}>
    <circle cx="12" cy="12" r="4" />
    <path d="M12 2v2M12 20v2M4.9 4.9l1.4 1.4M17.7 17.7l1.4 1.4M2 12h2M20 12h2M4.9 19.1l1.4-1.4M17.7 6.3l1.4-1.4" />
  </Base>
);
export const IconMoon = (p: IconProps) => (
  <Base {...p}><path d="M21 12.8A9 9 0 1 1 11.2 3a7 7 0 0 0 9.8 9.8z" /></Base>
);
export const IconVolume = (p: IconProps) => (
  <Base {...p}>
    <path d="M11 5 6 9H3v6h3l5 4z" />
    <path d="M15.5 8.5a5 5 0 0 1 0 7M18.5 5.5a9 9 0 0 1 0 13" />
  </Base>
);
export const IconVolumeX = (p: IconProps) => (
  <Base {...p}>
    <path d="M11 5 6 9H3v6h3l5 4z" /><path d="m17 9 4 6M21 9l-4 6" />
  </Base>
);
export const IconCpu = (p: IconProps) => (
  <Base {...p}>
    <rect x="6" y="6" width="12" height="12" rx="2" />
    <rect x="9.5" y="9.5" width="5" height="5" rx="1" />
    <path d="M9 2v3M15 2v3M9 19v3M15 19v3M2 9h3M2 15h3M19 9h3M19 15h3" />
  </Base>
);
export const IconZap = (p: IconProps) => (
  <Base {...p}><path d="M13 2 4 14h7l-1 8 9-12h-7z" /></Base>
);
export const IconImage = (p: IconProps) => (
  <Base {...p}>
    <rect x="3" y="3" width="18" height="18" rx="2.5" />
    <circle cx="8.6" cy="8.6" r="1.6" />
    <path d="m21 15-4.5-4.5L7 20" />
  </Base>
);
export const IconMic = (p: IconProps) => (
  <Base {...p}>
    <rect x="9" y="3" width="6" height="11" rx="3" />
    <path d="M5 11a7 7 0 0 0 14 0M12 18v3" />
  </Base>
);
export const IconUpload = (p: IconProps) => (
  <Base {...p}>
    <path d="M12 16V4" /><path d="m7 9 5-5 5 5" /><path d="M4 20h16" />
  </Base>
);
export const IconFilter = (p: IconProps) => (
  <Base {...p}><path d="M3 5h18l-7 8v6l-4-2v-4z" /></Base>
);
export const IconSort = (p: IconProps) => (
  <Base {...p}>
    <path d="M7 4v16M7 20l-3-3M7 4l3 3M17 20V4M17 4l-3 3M17 20l3-3" />
  </Base>
);
export const IconGrid = (p: IconProps) => (
  <Base {...p}>
    <rect x="3" y="3" width="7.5" height="7.5" rx="1.5" />
    <rect x="13.5" y="3" width="7.5" height="7.5" rx="1.5" />
    <rect x="3" y="13.5" width="7.5" height="7.5" rx="1.5" />
    <rect x="13.5" y="13.5" width="7.5" height="7.5" rx="1.5" />
  </Base>
);
export const IconList = (p: IconProps) => (
  <Base {...p}><path d="M8 6h13M8 12h13M8 18h13M3.5 6h.01M3.5 12h.01M3.5 18h.01" /></Base>
);
export const IconExternal = (p: IconProps) => (
  <Base {...p}>
    <path d="M14 4h6v6" /><path d="M20 4 10 14" />
    <path d="M18 14v5a1 1 0 0 1-1 1H5a1 1 0 0 1-1-1V7a1 1 0 0 1 1-1h5" />
  </Base>
);
export const IconWarning = (p: IconProps) => (
  <Base {...p}>
    <path d="M10.3 3.9 1.9 18a2 2 0 0 0 1.7 3h16.8a2 2 0 0 0 1.7-3L13.7 3.9a2 2 0 0 0-3.4 0z" />
    <path d="M12 9v4M12 17h.01" />
  </Base>
);
export const IconInfo = (p: IconProps) => (
  <Base {...p}><circle cx="12" cy="12" r="9" /><path d="M12 11v5M12 8h.01" /></Base>
);
export const IconError = (p: IconProps) => (
  <Base {...p}><circle cx="12" cy="12" r="9" /><path d="m15 9-6 6M9 9l6 6" /></Base>
);
export const IconSuccess = (p: IconProps) => (
  <Base {...p}><circle cx="12" cy="12" r="9" /><path d="m8.5 12 2.5 2.5 4.5-5" /></Base>
);
export const IconClock = (p: IconProps) => (
  <Base {...p}><circle cx="12" cy="12" r="9" /><path d="M12 7v5l3 2" /></Base>
);
export const IconCalendar = (p: IconProps) => (
  <Base {...p}>
    <rect x="3" y="5" width="18" height="16" rx="2" />
    <path d="M3 10h18M8 3v4M16 3v4" />
  </Base>
);
export const IconEye = (p: IconProps) => (
  <Base {...p}>
    <path d="M2 12s3.6-7 10-7 10 7 10 7-3.6 7-10 7-10-7-10-7z" />
    <circle cx="12" cy="12" r="3" />
  </Base>
);
export const IconEdit = (p: IconProps) => (
  <Base {...p}>
    <path d="M4 20h4L19 9a2.1 2.1 0 0 0-3-3L5 17z" /><path d="m14.5 6.5 3 3" />
  </Base>
);
export const IconExpand = (p: IconProps) => (
  <Base {...p}>
    <path d="M9 3H5a2 2 0 0 0-2 2v4M15 3h4a2 2 0 0 1 2 2v4M9 21H5a2 2 0 0 1-2-2v-4M15 21h4a2 2 0 0 0 2-2v-4" />
  </Base>
);
export const IconSend = (p: IconProps) => (
  <Base {...p}><path d="M21 3 10.5 13.5M21 3l-6.8 18-3.7-7.5L3 9.8z" /></Base>
);
export const IconHeart = (p: IconProps) => (
  <Base {...p}><path d="M12 20s-7-4.4-9.2-8.4C1.2 8.7 2.6 5.5 5.6 5.1c1.9-.3 3.6.7 4.4 2.2.8-1.5 2.5-2.5 4.4-2.2 3 .4 4.4 3.6 2.8 6.5C19 15.6 12 20 12 20z" /></Base>
);
export const IconWand = (p: IconProps) => (
  <Base {...p}>
    <path d="m4 20 9-9M14.5 5.5 18 9M13 4l1 2 2 1-2 1-1 2-1-2-2-1 2-1zM19 12l.7 1.4L21 14l-1.3.6L19 16l-.7-1.4L17 14l1.3-.6z" />
  </Base>
);
export const IconMore = (p: IconProps) => (
  <Base {...p}><path d="M5 12h.01M12 12h.01M19 12h.01" /></Base>
);
export const IconArrowRight = (p: IconProps) => (
  <Base {...p}><path d="M4 12h16M14 6l6 6-6 6" /></Base>
);
export const IconArrowLeft = (p: IconProps) => (
  <Base {...p}><path d="M20 12H4M10 6l-6 6 6 6" /></Base>
);
export const IconStar = (p: IconProps) => (
  <Base {...p}><path d="m12 3 2.7 5.6 6.1.9-4.4 4.3 1 6.1-5.4-2.9-5.4 2.9 1-6.1L3.2 9.5l6.1-.9z" /></Base>
);
export const IconPin = (p: IconProps) => (
  <Base {...p}>
    <path d="M9 3h6l-1 6 3 3v2H7v-2l3-3z" /><path d="M12 14v7" />
  </Base>
);
export const IconGrip = (p: IconProps) => (
  <Base {...p}>
    <path d="M9 6h.01M9 12h.01M9 18h.01M15 6h.01M15 12h.01M15 18h.01" />
  </Base>
);
export const IconTerminal = (p: IconProps) => (
  <Base {...p}><rect x="3" y="4" width="18" height="16" rx="2" /><path d="m7 9 3 3-3 3M13 15h4" /></Base>
);
export const IconDatabase = (p: IconProps) => (
  <Base {...p}>
    <ellipse cx="12" cy="5.5" rx="8" ry="3" />
    <path d="M4 5.5v13c0 1.7 3.6 3 8 3s8-1.3 8-3v-13" />
    <path d="M4 12c0 1.7 3.6 3 8 3s8-1.3 8-3" />
  </Base>
);
export const IconPower = (p: IconProps) => (
  <Base {...p}><path d="M12 3v9" /><path d="M6.3 6.3a8 8 0 1 0 11.4 0" /></Base>
);
export const IconLayers = (p: IconProps) => (
  <Base {...p}>
    <path d="m12 3 9 5-9 5-9-5z" /><path d="m3 13 9 5 9-5" />
  </Base>
);
export const IconUser = (p: IconProps) => (
  <Base {...p}><circle cx="12" cy="8" r="4" /><path d="M4 21a8 8 0 0 1 16 0" /></Base>
);
export const IconBot = (p: IconProps) => (
  <Base {...p}>
    <rect x="4" y="8" width="16" height="12" rx="3" />
    <path d="M12 4v4M9 14h.01M15 14h.01M2 13v2M22 13v2" />
  </Base>
);
export const IconWifi = (p: IconProps) => (
  <Base {...p}>
    <path d="M2 8.5a15 15 0 0 1 20 0M5 12a10 10 0 0 1 14 0M8.5 15.5a5 5 0 0 1 7 0" />
    <path d="M12 19h.01" />
  </Base>
);
export const IconSpinner = (p: IconProps) => (
  <Base {...p} className={`animate-spin ${p.className ?? ""}`}>
    <path d="M12 3a9 9 0 1 0 9 9" />
  </Base>
);
export const IconFolder = (p: IconProps) => (
  <Base {...p}>
    <path d="M3 7a2 2 0 0 1 2-2h4l2 2.5h8a2 2 0 0 1 2 2V18a2 2 0 0 1-2 2H5a2 2 0 0 1-2-2z" />
  </Base>
);
export const IconSliders = (p: IconProps) => (
  <Base {...p}>
    <path d="M4 21v-7M4 10V3M12 21v-9M12 8V3M20 21v-5M20 12V3" />
    <path d="M1 14h6M9 8h6M17 16h6" />
  </Base>
);
export const IconTarget = (p: IconProps) => (
  <Base {...p}>
    <circle cx="12" cy="12" r="9" /><circle cx="12" cy="12" r="5" /><circle cx="12" cy="12" r="1.4" />
  </Base>
);

/** 品牌标记：Möbius 环（无限 / 循环） */
export const IconLogo = ({ size = 26, ...rest }: IconProps) => (
  <svg width={size} height={size} viewBox="0 0 32 32" fill="none" aria-hidden="true" {...rest}>
    <defs>
      <linearGradient id="mb-g" x1="4" y1="4" x2="28" y2="28" gradientUnits="userSpaceOnUse">
        <stop stopColor="#8b7cff" />
        <stop offset="0.55" stopColor="#ff4d8d" />
        <stop offset="1" stopColor="#22d3ee" />
      </linearGradient>
    </defs>
    <path
      d="M16 5c6.2 0 10.5 4.9 10.5 11S22.2 27 16 27 5.5 22.1 5.5 16 9.8 5 16 5z"
      stroke="url(#mb-g)" strokeWidth="2.4"
    />
    <path d="M8.5 10.5c5.2 3.2 9.6 7.4 15 11.2" stroke="url(#mb-g)" strokeWidth="2.4" strokeLinecap="round" />
    <circle cx="16" cy="16" r="2.1" fill="url(#mb-g)" />
  </svg>
);