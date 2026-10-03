import styles from './Primitives.module.css';

export type IconName = 'mic' | 'microphone' | 'voiceover' | 'mixed' | 'original' | 'chat' | 'upload' | 'pencil' | 'arrow' | 'check' | 'intro' | 'processing';
/** Paths copied verbatim from handoff appendix A; sizing numbers are pixels at
 * the 16px baseline and emitted in rem. Icons are decorative, not labels. */
export function Icon({ name, size = 24, color = 'currentColor' }: { name: IconName; size?: number; color?: string }) {
  const intro = name === 'intro', mixed = name === 'mixed', processing = name === 'processing';
  return <svg className={styles.icon} aria-hidden="true" focusable="false" width={`${size / 16}rem`}
    height={`${size / 16 * (intro ? 110 / 132 : mixed ? 24 / 34 : 1)}rem`}
    viewBox={intro ? '0 0 132 110' : mixed ? '0 0 34 24' : processing ? '0 0 40 40' : '0 0 24 24'}
    fill="none" stroke={color} strokeWidth={name === 'check' ? 3.2 : name === 'arrow' ? 3 : intro || processing ? 2.6 : name === 'upload' ? 2 : 2.4} strokeLinecap="round" strokeLinejoin="round">
    {['mic', 'microphone', 'voiceover'].includes(name) && <><rect x="8.5" y="3" width="7" height="12" rx="3.5" fill={color} /><path d="M5.5 11.5a6.5 6.5 0 0 0 13 0" /><path d="M12 18v3M9 21h6" /></>}
    {mixed && <><rect x="4.5" y="3" width="6" height="11" rx="3" fill={color} /><path d="M2 11a5.5 5.5 0 0 0 11 0M7.5 17v3" /><path d="M19 4h11a2 2 0 0 1 2 2v7a2 2 0 0 1-2 2h-6l-4 3v-3h-1a2 2 0 0 1-2-2V6a2 2 0 0 1 2-2z" /></>}
    {(name === 'original' || name === 'chat') && <><path d="M5 4h14a2 2 0 0 1 2 2v8a2 2 0 0 1-2 2h-7l-5 4v-4H5a2 2 0 0 1-2-2V6a2 2 0 0 1 2-2z" /><path d="M8 9h8M8 12.5h5" /></>}
    {name === 'upload' && <><path d="M7 18a4.5 4.5 0 0 1-.8-8.9A6 6 0 0 1 17.7 8 4 4 0 0 1 17 18" /><path d="M12 21v-8M8.5 16.5 12 13l3.5 3.5" /></>}
    {name === 'pencil' && <><path d="M4 20l4.5-1 10-10-3.5-3.5-10 10z" /><path d="M13.5 7l3.5 3.5" /></>}
    {name === 'arrow' && <path d="M4 12h15M13 6l6 6-6 6" />}
    {name === 'check' && <path d="M5 12.5l4.5 4.5L19 7" />}
    {intro && <><g transform="rotate(-8 40 60)"><rect x="14" y="40" width="52" height="40" rx="8" fill="var(--white)" /><path d="M14 52h52" /><rect x="14" y="34" width="52" height="14" rx="6" fill="var(--gold)" /><path d="M22 34l6 14M34 34l6 14M46 34l6 14" /><path d="M26 62h28M26 70h18" /></g><g transform="rotate(10 96 52)"><rect x="86" y="14" width="22" height="38" rx="11" fill={color} /><path d="M78 40a19 19 0 0 0 38 0M97 60v12M88 72h18" /><path d="M70 30v18M124 30v18" /></g><circle cx="24" cy="18" r="4" fill="var(--teal)" stroke="none" /><circle cx="118" cy="92" r="5" fill="var(--gold)" stroke="none" /><path d="M6 96l6-6 6 6" stroke="var(--red)" /></>}
    {processing && <><rect x="14.5" y="6" width="11" height="17" rx="5.5" fill={color} /><path d="M10 18a10 10 0 0 0 20 0M20 28v5M15 33h10" />{[['M6 12v10', '6px 17px', '0s'], ['M3 14v6', '3px 17px', '.3s'], ['M34 12v10', '34px 17px', '.15s'], ['M37 14v6', '37px 17px', '.45s']].map(([d, transformOrigin, animationDelay]) => <g key={d} className={styles.wave} style={{ transformOrigin, animationDelay }}><path d={d} /></g>)}</>}
  </svg>;
}
export default Icon;