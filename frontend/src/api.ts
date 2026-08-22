// Single backend client. URLs are relative to the origin: in production the
// FastAPI sidecar serves this UI at /app/ so /api is same-origin; in dev, Vite
// proxies /api to the sidecar (see vite.config.ts).
import type { Author, DraftCommit, Facets, Recipe, Source, Title, UserPatch } from './data'

export class ApiError extends Error {
  constructor(public status: number, message: string) {
    super(message)
    this.name = 'ApiError'
  }
}

async function req<T>(path: string, init?: RequestInit): Promise<T> {
  const res = await fetch(`/api${path}`, { credentials: 'same-origin', ...init })
  if (!res.ok) throw new ApiError(res.status, `${init?.method ?? 'GET'} ${path} → ${res.status}`)
  return res.json() as Promise<T>
}

async function reqVoid(path: string, init: RequestInit): Promise<void> {
  const res = await fetch(`/api${path}`, { credentials: 'same-origin', ...init })
  if (!res.ok) throw new ApiError(res.status, `${init.method} ${path} → ${res.status}`)
}

function jsonBody(method: string, data: unknown): RequestInit {
  return { method, headers: { 'Content-Type': 'application/json' }, body: JSON.stringify(data) }
}

export interface LibraryQuery {
  search?: string
  progress?: string
  fav?: boolean
  min_rating?: number
  sort?: string
  types?: string[]
  types_not?: string[]
  statuses?: string[]
  statuses_not?: string[]
  genres?: string[]
  genres_not?: string[]
  tags?: string[]
  tags_not?: string[]
  languages?: string[]
  languages_not?: string[]
  flags?: string[]
  flags_not?: string[]
  authors?: string[]
  authors_not?: string[]
  characters?: string[]
  characters_not?: string[]
}

function qs(params: Record<string, string | number | boolean | string[] | undefined>): string {
  const p = new URLSearchParams()
  for (const [k, v] of Object.entries(params)) {
    if (Array.isArray(v)) for (const x of v) p.append(k, x)
    else if (v !== undefined && v !== '' && v !== false && v !== 'all') p.set(k, String(v))
  }
  const s = p.toString()
  return s ? `?${s}` : ''
}

// Cover bytes captured in the page's context (the mandatory path), or a URL for
// the server-side fallback fetch when no page context is available.
export interface CoverUpload {
  data?: string        // base64 image bytes
  contentType?: string
  sourceUrl?: string
  url?: string         // fallback: the backend fetches this
  referer?: string
}

export const api = {
  library: (q: LibraryQuery = {}) => req<Title[]>(`/library${qs({ ...q })}`),
  // the unfiltered size of the vault — never list titles just to count them
  libraryCount: () => req<{ total: number }>('/library/count'),
  facets: (q: LibraryQuery = {}) => req<Facets>(`/library/facets${qs({ ...q })}`),
  title: (id: string) => req<Title>(`/titles/${id}`),
  authors: () => req<Author[]>('/authors'),
  setAuthorFav: (id: string, value: boolean) =>
    req<Author[]>(`/authors/${id}/favorite?value=${value}`, { method: 'POST' }),
  sources: () => req<Source[]>('/sources'),
  createTitle: (draft: DraftCommit) => req<Title>('/titles', jsonBody('POST', draft)),
  commitTitle: (id: string, draft: DraftCommit) => req<Title>(`/titles/${id}`, jsonBody('PUT', draft)),
  removeTitle: (id: string) => reqVoid(`/titles/${id}`, { method: 'DELETE' }),
  patchUser: (id: string, patch: UserPatch) => req<Title>(`/titles/${id}/user`, jsonBody('PATCH', patch)),
  setCover: (id: string, body: CoverUpload) => req<Title>(`/titles/${id}/cover`, jsonBody('POST', body)),
  deleteCover: (id: string) => req<Title>(`/titles/${id}/cover`, { method: 'DELETE' }),
  recipe: (domain: string) => req<Recipe>(`/recipes/${domain}`),
  saveRecipe: (domain: string, recipe: Recipe) => req<Recipe>(`/recipes/${domain}`, jsonBody('PUT', recipe)),
  removeRecipe: (domain: string) => reqVoid(`/recipes/${domain}`, { method: 'DELETE' }),
  removeSource: (domain: string) =>
    req<{ hidden: boolean; recipeDeleted: boolean }>(`/sources/${domain}`, { method: 'DELETE' }),
  // armed downloads: the arm is consumed when a download STARTS (its chapter
  // binding is claimed immediately), so downloads run in parallel
  armDownload: (body: ArmInfo) => req<DownloadsState>('/downloads/arm', jsonBody('POST', body)),
  downloadsState: () => req<DownloadsState>('/downloads'),
  disarmDownload: () => reqVoid('/downloads/arm', { method: 'DELETE' }),
  // chapter media
  chapterPages: (tid: string, cid: string) => req<{ count: number }>(`/titles/${tid}/chapters/${cid}/pages`),
  // `w` = downscaled cached preview; `cap` crops very tall (webtoon) pages to
  // width×cap from the top — previews only, the reader always loads originals
  chapterPageSrc: (tid: string, cid: string, index: number, v: number | string = 0, w = 0, cap = 0) =>
    `/api/titles/${tid}/chapters/${cid}/pages/${index}?v=${v}${w ? `&w=${w}` : ''}${cap ? `&cap=${cap}` : ''}`,
  // the episode file itself; the server answers Range, so the player seeks
  chapterVideoSrc: (tid: string, cid: string, v: string) =>
    `/api/titles/${tid}/chapters/${cid}/video?v=${encodeURIComponent(v)}`,
  // what only a player can measure without an ffprobe we do not ship
  setVideoMeta: (tid: string, cid: string, duration: number) =>
    req<Title>(`/titles/${tid}/chapters/${cid}/video/meta`, jsonBody('POST', { duration })),
  deleteChapterPages: (tid: string, cid: string, indices: number[]) =>
    req<Title>(`/titles/${tid}/chapters/${cid}/pages/delete`, jsonBody('POST', { indices })),
  // loose images (multi-select or a whole folder) appended to the entry's
  // archive — created on first add; non-images are skipped server-side
  addChapterPages: (tid: string, cid: string, files: File[]) => {
    const fd = new FormData()
    for (const f of files) fd.append('files', f, f.name)
    return req<Title>(`/titles/${tid}/chapters/${cid}/pages/add`, { method: 'POST', body: fd })
  },
  moveChapterPages: (tid: string, cid: string, to: string, indices: number[]) =>
    req<Title>(`/titles/${tid}/chapters/${cid}/pages/move`, jsonBody('POST', { to, indices })),
  reorderChapterPages: (tid: string, cid: string, order: number[]) =>
    req<Title>(`/titles/${tid}/chapters/${cid}/pages/reorder`, jsonBody('POST', { order })),
  deleteChapterMedia: (tid: string, cid: string) => req<Title>(`/titles/${tid}/chapters/${cid}/media`, { method: 'DELETE' }),
  deleteChapterRow: (tid: string, cid: string) => req<Title>(`/titles/${tid}/chapters/${cid}`, { method: 'DELETE' }),
  // attach an already-downloaded archive from disk (the local twin of arming);
  // chapterId pins it to an exact existing row, url records the source link
  importChapterArchive: (tid: string, ch: { num: string; lang: string; group: string; url?: string; chapterId?: string }, file: File) =>
    req<Title>(`/titles/${tid}/chapters/import${qs({ num: ch.num, lang: ch.lang, group: ch.group, filename: file.name, url: ch.url, chapter_id: ch.chapterId })}`,
      { method: 'POST', body: file }),
  settings: () => req<AppSettings>('/settings'),
  setLibraryPath: (path: string) => req<AppSettings>('/settings/library-path', jsonBody('PUT', { path })),
  removeLibrary: (path: string) => req<AppSettings>('/settings/libraries', jsonBody('DELETE', { path })),
  rebuildIndex: () => req<AppSettings>('/settings/rebuild', { method: 'POST' }),
  rebuildStatus: () => req<{ running: boolean; done: number; total: number }>('/settings/rebuild/status'),
  // read while a library switch is in flight: opening a populated vault is slow
  libraryStatus: () =>
    req<{ running: boolean; path: string; done: number; total: number; changed: number }>(
      '/settings/library/status'),
  normalizeArchives: () => req<{ converted: number }>('/settings/normalize-archives', { method: 'POST' }),
  // page capture: ask what the armed entry already holds, then send only the rest
  knownPages: (titleId: string, chapterId: string, keys: string[]) =>
    req<string[]>(`/titles/${encodeURIComponent(titleId)}/chapters/${encodeURIComponent(chapterId)}/pages/known`,
      jsonBody('POST', { keys })),
  capturePages: (titleId: string, chapterId: string, pageUrl: string,
    images: { key: string; url: string; data: string; contentType: string }[]) =>
    req<Title>(`/titles/${encodeURIComponent(titleId)}/chapters/${encodeURIComponent(chapterId)}/pages/capture`,
      jsonBody('POST', { pageUrl, images })),
  setHomepage: (homepage: string) => req<AppSettings>('/settings/homepage', jsonBody('PUT', { homepage })),
}

export interface AppSettings {
  library_path: string
  title_count: number
  homepage: string
  libraries: string[]
  app: { name?: string; version?: string; updated?: string; description?: string }
}

export interface ArmInfo {
  titleId: string
  num: string
  lang: string
  group: string
}

export interface DownloadItem {
  id: string
  titleId: string
  num: string
  lang: string
  group: string
  filename: string
  fileUrl: string
  pageUrl: string
  received: number
  total: number
  state: 'downloading' | 'done' | 'failed'
  error: string
}

export interface DownloadsState {
  armed: ArmInfo | null
  items: DownloadItem[]
}
