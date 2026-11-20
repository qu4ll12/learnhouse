/**
 * API feed data (`/seo/feed/...`) → the neutral FeedDoc the builders render.
 * Pure: media URL helpers come in through `media` so tests stay offline.
 */
import type { FeedDoc, FeedFormat, FeedItem } from './feeds'
import { clipDescription } from './meta'

export type FeedMedia = {
  course: (_orgUuid: string, _uuid: string, _file: string) => string
  path: (_orgUuid: string, _uuid: string, _file: string) => string
  orgLogo: (_orgUuid: string, _file: string) => string
}

const enc = encodeURIComponent

/** Public feed URLs of a page, per format (`/feed.xml`, `/feed.atom`, `/feed.json`). */
export function feedUrls(base: string, prefix: string): Record<FeedFormat, string> {
  return { rss: `${base}${prefix}feed.xml`, atom: `${base}${prefix}feed.atom`, json: `${base}${prefix}feed.json` }
}

function image(media: FeedMedia, orgUuid: string, type: string, uuid: string, file?: string): string | undefined {
  if (!file || file.includes('/')) return undefined
  const fn = (media as any)[type]
  return typeof fn === 'function' ? fn(orgUuid, uuid, file) : undefined
}

export function feedDocFromApi(data: any, opts: { base: string; prefix: string; media: FeedMedia; lang?: string }): FeedDoc {
  const { base, media } = opts
  const org = data.org || {}
  const orgUuid = org.org_uuid || ''
  const logo = org.logo ? media.orgLogo(orgUuid, org.logo) : undefined
  const common = { feedUrls: feedUrls(base, opts.prefix), language: opts.lang, author: org.name }
  switch (data.kind) {
    default: {
      // org / courses: what's new
      const items: FeedItem[] = (data.items || []).filter((i: any) => i.type !== 'podcast').map((i: any) => {
        const segment = i.type === 'path' ? 'path' : 'course'
        const url = `${base}/${segment}/${enc(i.key)}`
        return {
          id: url,
          title: i.name,
          url,
          summary: clipDescription(i.description, 500) || undefined,
          author: i.authors?.length ? i.authors.join(', ') : undefined,
          published: i.published,
          updated: i.updated,
          categories: [segment === 'course' ? 'Course' : 'Learning path', ...String(i.tags || '').split(',').map((s: string) => s.trim()).filter(Boolean).slice(0, 5)],
          image: image(media, orgUuid, segment, i.uuid, i.image),
        }
      })
      return {
        ...common,
        title: data.kind === 'courses' ? `${org.name}: courses` : `${org.name}: what's new`,
        homeUrl: data.kind === 'courses' ? `${base}/courses` : `${base}/`,
        description: clipDescription(org.description || `New courses and learning paths from ${org.name}.`, 400),
        image: logo,
        items,
      }
    }
  }
}
