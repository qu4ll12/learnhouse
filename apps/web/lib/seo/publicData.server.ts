import { getServerAPIUrl } from '@services/config/config'

/**
 * Server-side reads of the API's anonymous-safe SEO data (`/seo/...`). Every
 * call is anonymous on purpose: what feeds, llms.txt, Markdown alternates and
 * structured data expose must be what a signed-out visitor could see, whoever
 * triggers the render. Short timeouts and `null` on failure: metadata never
 * blocks or breaks a page.
 */

const TAGS = ['seo-sitemap', 'courses', 'folders', 'organizations']

async function getJson<T>(path: string, opts: { revalidate?: number; timeoutMs?: number } = {}): Promise<T | null> {
  try {
    const res = await fetch(`${getServerAPIUrl()}seo/${path}`, {
      next: { revalidate: opts.revalidate ?? 300, tags: TAGS },
      signal: AbortSignal.timeout(opts.timeoutMs ?? 8000),
    })
    if (!res.ok) return null
    return (await res.json()) as T
  } catch {
    return null
  }
}

export function fetchFeedData(orgslug: string, kind: string, opts: { key?: string } = {}) {
  const q = new URLSearchParams({ kind })
  if (opts.key) q.set('key', opts.key)
  return getJson<any>(`feed/${encodeURIComponent(orgslug)}?${q}`, { revalidate: 600 })
}

export function fetchLlmsData(orgslug: string, full: boolean) {
  return getJson<any>(`llms/${encodeURIComponent(orgslug)}${full ? '?full=true' : ''}`, { revalidate: 3600, timeoutMs: 20000 })
}

export function fetchMarkdownData(orgslug: string, type: string, key: string, sub?: string) {
  const q = sub ? `?sub=${encodeURIComponent(sub)}` : ''
  return getJson<any>(`markdown/${encodeURIComponent(orgslug)}/${type}/${encodeURIComponent(key)}${q}`, { revalidate: 600 })
}

export function fetchIndexNowKey(orgslug: string) {
  return getJson<{ key: string }>(`indexnow-key/${encodeURIComponent(orgslug)}`, { revalidate: 300, timeoutMs: 3000 })
}
