/**
 * Syndication feeds: RSS 2.0, Atom 1.0 and JSON Feed 1.1 from one neutral
 * description. Pure functions: the route handler fetches and maps the data.
 */
import { xmlEscape } from './sitemap'
import { toIsoDate } from './jsonld'

export type FeedFormat = 'rss' | 'atom' | 'json'

export const FEED_CONTENT_TYPES: Record<FeedFormat, string> = {
  rss: 'application/rss+xml; charset=utf-8',
  atom: 'application/atom+xml; charset=utf-8',
  json: 'application/feed+json; charset=utf-8',
}

export type FeedItem = {
  id: string
  title: string
  url: string
  summary?: string
  /** Full text as HTML (escaped by the builders). */
  contentHtml?: string
  author?: string
  published?: string | null
  updated?: string | null
  categories?: string[]
  image?: string
  enclosure?: { url: string; length: number; type: string }
  durationSeconds?: number
}

export type FeedDoc = {
  title: string
  /** The HTML page this feed mirrors. */
  homeUrl: string
  /** This feed's own URL, per format. */
  feedUrls: Partial<Record<FeedFormat, string>>
  description?: string
  language?: string
  image?: string
  author?: string
  updated?: string | null
  items: FeedItem[]
}

function rfc822(value?: string | null): string | undefined {
  const iso = toIsoDate(value)
  return iso ? new Date(iso).toUTCString() : undefined
}

function latest(doc: FeedDoc): string {
  const dates = [doc.updated, ...doc.items.map((i) => i.updated || i.published)].map((d) => toIsoDate(d)).filter(Boolean) as string[]
  return dates.sort().at(-1) || new Date(0).toISOString()
}

const cdata = (html: string) => `<![CDATA[${html.replace(/]]>/g, ']]]]><![CDATA[>')}]]>`

/** Strip characters XML 1.0 forbids (control chars break strict parsers). */
export function xmlSafe(value: string): string {
  // eslint-disable-next-line no-control-regex
  return value.replace(/[\u0000-\u0008\u000B\u000C\u000E-\u001F￾￿]/g, '')
}

const esc = (v: string | undefined | null) => xmlEscape(xmlSafe(String(v ?? '')))

export function buildRss(doc: FeedDoc): string {
  const items = doc.items
    .map((i) => {
      const parts = [
        `<title>${esc(i.title)}</title>`,
        `<link>${esc(i.url)}</link>`,
        `<guid isPermaLink="${i.id === i.url ? 'true' : 'false'}">${esc(i.id)}</guid>`,
      ]
      const date = rfc822(i.published || i.updated)
      if (date) parts.push(`<pubDate>${date}</pubDate>`)
      if (i.summary) parts.push(`<description>${esc(i.summary)}</description>`)
      if (i.contentHtml) parts.push(`<content:encoded>${cdata(xmlSafe(i.contentHtml))}</content:encoded>`)
      if (i.author) parts.push(`<dc:creator>${esc(i.author)}</dc:creator>`)
      for (const c of i.categories || []) parts.push(`<category>${esc(c)}</category>`)
      if (i.enclosure?.url) parts.push(`<enclosure url="${esc(i.enclosure.url)}" length="${Math.max(0, Math.round(i.enclosure.length || 0))}" type="${esc(i.enclosure.type)}"/>`)
      else if (i.image) parts.push(`<media:thumbnail url="${esc(i.image)}"/>`)
      return `    <item>${parts.join('')}</item>`
    })
    .join('\n')
  const self = doc.feedUrls.rss || ''
  const channel = [
    `<title>${esc(doc.title)}</title>`,
    `<link>${esc(doc.homeUrl)}</link>`,
    `<description>${esc(doc.description || doc.title)}</description>`,
    self ? `<atom:link href="${esc(self)}" rel="self" type="application/rss+xml"/>` : '',
    doc.language ? `<language>${esc(doc.language)}</language>` : '',
    `<lastBuildDate>${new Date(latest(doc)).toUTCString()}</lastBuildDate>`,
    '<generator>LearnHouse</generator>',
    doc.image ? `<image><url>${esc(doc.image)}</url><title>${esc(doc.title)}</title><link>${esc(doc.homeUrl)}</link></image>` : '',
  ]
    .filter(Boolean)
    .join('\n    ')
  return `<?xml version="1.0" encoding="UTF-8"?>
<rss version="2.0" xmlns:atom="http://www.w3.org/2005/Atom" xmlns:content="http://purl.org/rss/1.0/modules/content/" xmlns:dc="http://purl.org/dc/elements/1.1/" xmlns:media="http://search.yahoo.com/mrss/">
  <channel>
    ${channel}
${items}
  </channel>
</rss>
`
}

export function buildAtom(doc: FeedDoc): string {
  const self = doc.feedUrls.atom || doc.homeUrl
  const entries = doc.items
    .map((i) => {
      const parts = [
        `<id>${esc(i.id.startsWith('http') || i.id.startsWith('urn:') ? i.id : `urn:learnhouse:${i.id}`)}</id>`,
        `<title>${esc(i.title)}</title>`,
        `<link rel="alternate" type="text/html" href="${esc(i.url)}"/>`,
        `<updated>${toIsoDate(i.updated || i.published) || latest(doc)}</updated>`,
      ]
      const published = toIsoDate(i.published)
      if (published) parts.push(`<published>${published}</published>`)
      if (i.author) parts.push(`<author><name>${esc(i.author)}</name></author>`)
      if (i.summary) parts.push(`<summary>${esc(i.summary)}</summary>`)
      if (i.contentHtml) parts.push(`<content type="html">${esc(i.contentHtml)}</content>`)
      for (const c of i.categories || []) parts.push(`<category term="${esc(c)}"/>`)
      if (i.enclosure?.url) parts.push(`<link rel="enclosure" type="${esc(i.enclosure.type)}" length="${Math.max(0, Math.round(i.enclosure.length || 0))}" href="${esc(i.enclosure.url)}"/>`)
      return `  <entry>${parts.join('')}</entry>`
    })
    .join('\n')
  const head = [
    `<id>${esc(self)}</id>`,
    `<title>${esc(doc.title)}</title>`,
    doc.description ? `<subtitle>${esc(doc.description)}</subtitle>` : '',
    `<updated>${latest(doc)}</updated>`,
    `<link rel="self" type="application/atom+xml" href="${esc(self)}"/>`,
    `<link rel="alternate" type="text/html" href="${esc(doc.homeUrl)}"/>`,
    doc.author ? `<author><name>${esc(doc.author)}</name></author>` : `<author><name>${esc(doc.title)}</name></author>`,
    doc.image ? `<logo>${esc(doc.image)}</logo>` : '',
    '<generator>LearnHouse</generator>',
  ]
    .filter(Boolean)
    .join('\n  ')
  return `<?xml version="1.0" encoding="UTF-8"?>
<feed xmlns="http://www.w3.org/2005/Atom"${doc.language ? ` xml:lang="${esc(doc.language)}"` : ''}>
  ${head}
${entries}
</feed>
`
}

export function buildJsonFeed(doc: FeedDoc): string {
  const feed = {
    version: 'https://jsonfeed.org/version/1.1',
    title: doc.title,
    home_page_url: doc.homeUrl,
    ...(doc.feedUrls.json ? { feed_url: doc.feedUrls.json } : {}),
    ...(doc.description ? { description: doc.description } : {}),
    ...(doc.image ? { icon: doc.image } : {}),
    ...(doc.language ? { language: doc.language } : {}),
    ...(doc.author ? { authors: [{ name: doc.author }] } : {}),
    items: doc.items.map((i) => ({
      id: i.id,
      url: i.url,
      title: i.title,
      ...(i.contentHtml ? { content_html: i.contentHtml } : { content_text: i.summary || i.title }),
      ...(i.summary ? { summary: i.summary } : {}),
      ...(i.image ? { image: i.image } : {}),
      ...(toIsoDate(i.published) ? { date_published: toIsoDate(i.published) } : {}),
      ...(toIsoDate(i.updated) ? { date_modified: toIsoDate(i.updated) } : {}),
      ...(i.author ? { authors: [{ name: i.author }] } : {}),
      ...(i.categories?.length ? { tags: i.categories } : {}),
      ...(i.enclosure?.url
        ? {
            attachments: [
              {
                url: i.enclosure.url,
                mime_type: i.enclosure.type,
                ...(i.enclosure.length ? { size_in_bytes: i.enclosure.length } : {}),
                ...(i.durationSeconds ? { duration_in_seconds: i.durationSeconds } : {}),
              },
            ],
          }
        : {}),
    })),
  }
  return `${JSON.stringify(feed, null, 2)}\n`
}

export function buildFeed(format: FeedFormat, doc: FeedDoc): string {
  return format === 'atom' ? buildAtom(doc) : format === 'json' ? buildJsonFeed(doc) : buildRss(doc)
}

/** `HH:MM:SS` for itunes:duration. */
/** Audio MIME type from a file name (Apple rejects a wrong enclosure type). */
