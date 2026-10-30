// Line icons from the wireframes, drawn with currentColor so they follow the text colour.
type P = { size?: number };

export function CameraIcon({ size = 20 }: P) {
  return (
    <svg width={size} height={size} viewBox="0 0 20 20" fill="none" stroke="currentColor" strokeWidth="2" aria-hidden="true">
      <rect x="2" y="5" width="16" height="12" rx="2" /><circle cx="10" cy="11" r="3" /><path d="M7 5l1.5-2h3L13 5" />
    </svg>
  );
}

export function CloseIcon({ size = 18 }: P) {
  return (
    <svg width={size} height={size} viewBox="0 0 18 18" fill="none" stroke="currentColor" strokeWidth="2" aria-hidden="true">
      <path d="M4 4l10 10M14 4L4 14" />
    </svg>
  );
}

export function CheckIcon({ size = 12 }: P) {
  return (
    <svg width={size} height={size} viewBox="0 0 16 16" fill="none" stroke="#fff" strokeWidth="2.5" aria-hidden="true">
      <path d="M3 8.5l3 3 7-7" />
    </svg>
  );
}

export function BackIcon({ size = 20 }: P) {
  return (
    <svg width={size} height={size} viewBox="0 0 20 20" fill="none" stroke="currentColor" strokeWidth="2" aria-hidden="true">
      <path d="M12 4l-6 6 6 6" />
    </svg>
  );
}

export function ChevronIcon({ size = 16 }: P) {
  return (
    <svg className="chev" width={size} height={size} viewBox="0 0 16 16" fill="none" stroke="currentColor" strokeWidth="2" aria-hidden="true">
      <path d="M6 3l5 5-5 5" />
    </svg>
  );
}

export function WarnIcon({ size = 16 }: P) {
  return (
    <svg width={size} height={size} viewBox="0 0 16 16" fill="none" stroke="currentColor" strokeWidth="2" aria-hidden="true">
      <path d="M8 2l6.5 12h-13z" /><path d="M8 6.5v3.5M8 12v.5" />
    </svg>
  );
}
