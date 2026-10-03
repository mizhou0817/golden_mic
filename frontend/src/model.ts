/** Shell-only state. Server task/report types remain owned by their API parsers. */
export type WorkspaceScreen = 'create' | 'processing' | 'result' | 'gone';
export type HistorySort = 'time' | 'title';
export interface ShellPreferences { introDismissed: boolean; bigFont: boolean; histSort: HistorySort }
/** An issued draft capability, never a client-generated task receipt. */
export interface DraftTaskBinding { draftTaskId?: string; draftTaskToken?: string }
export const DRAFT_SAVE_DELAY_MS = 300;
export const V2_HISTORY_LIMIT = 100;