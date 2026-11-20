import { describe, expect, test } from "bun:test";
import { spawnSync } from "node:child_process";

import { buildAtom, buildFeed, buildJsonFeed, buildRss, xmlSafe } from "../lib/seo/feeds.ts";
import { feedDocFromApi, feedUrls } from "../lib/seo/feedMapping.ts";
import { buildLlmsTxt, canonicalPathFor, markdownFor, markdownPath, parseMarkdownPath, pmToMarkdown } from "../lib/seo/markdown.ts";
import { seoRouteFor } from "../lib/seo/publicRoutes.ts";
import { AI_ANSWER_BOTS, AI_TRAINING_BOTS, blockedAiBots, buildRobotsTxt, contentSignal, sanitizeRobotsRules } from "../lib/seo/robots.ts";
import { buildManifest, imageMime, shortName } from "../lib/seo/manifest.ts";
import { landingFaq } from "../lib/seo/landing.ts";
import { featureIndexable, feedsEnabled, llmsEnabled } from "../lib/seo/meta.ts";
import * as ld from "../lib/seo/jsonld.ts";
import { validateJsonLd } from "./fixtures/schemaorg.mjs";

/** Strict XML well-formedness (expat via python3); skipped where python3 is missing. */
function assertWellFormedXml(xml) {
  const res = spawnSync("python3", ["-c", "import sys,xml.dom.minidom as m; m.parseString(sys.stdin.buffer.read())"], { input: xml });
  if (res.error) return; // no python3 on this machine
  expect(res.stderr.toString()).toBe("");
  expect(res.status).toBe(0);
}

const SECRET = "Secret-Title-Never-Public";
const media = {
  course: (o, u, f) => `https://cdn/${o}/c/${u}/${f}`,
  path: (o, u, f) => `https://cdn/${o}/p/${u}/${f}`,
  orgLogo: (o, f) => `https://cdn/${o}/logo/${f}`,
};
const org = { slug: "acme", org_uuid: "org_1", name: "Acme & Co", description: "Learn <fast>", logo: "l.png" };
const base = "https://learn.acme.com";

// ─── feeds ──────────────────────────────────────────────────────────────────

const doc = {
  title: "Acme & Co: what's new",
  homeUrl: `${base}/`,
  feedUrls: feedUrls(base, "/"),
  description: "New <things>",
  language: "en",
  image: "https://cdn/logo.png",
  author: "Acme",
  items: [
    {
      id: `${base}/course/a`, title: "A & B", url: `${base}/course/a`, summary: "Sum <x>", contentHtml: "<p>Hi ]]> there\u0001</p>",
      author: "Ada", published: "2026-11-05 01:33:02.1", updated: "2026-11-06 00:00:00", categories: ["Course", "Q&A"], image: "https://cdn/a.png",
    },
    { id: "episode_1", title: "Ep", url: `${base}/podcast/p#episode-1`, enclosure: { url: "https://cdn/a.mp3", length: 1234.4, type: "audio/mpeg" } },
  ],
};

describe("feeds", () => {
  test("RSS 2.0 is strict XML with self link, guid, content and enclosure", () => {
    const xml = buildRss(doc);
    assertWellFormedXml(xml);
    expect(xml).toContain('<atom:link href="https://learn.acme.com/feed.xml" rel="self" type="application/rss+xml"/>');
    expect(xml).toContain('<guid isPermaLink="true">https://learn.acme.com/course/a</guid>');
    expect(xml).toContain('<guid isPermaLink="false">episode_1</guid>');
    expect(xml).toContain("<pubDate>Thu, 05 Nov 2026 01:33:02 GMT</pubDate>");
    expect(xml).toContain("<category>Q&amp;A</category>");
    expect(xml).toContain('<enclosure url="https://cdn/a.mp3" length="1234" type="audio/mpeg"/>');
    expect(xml).toContain('<media:thumbnail url="https://cdn/a.png"/>');
    expect(xml).not.toContain("\u0001");
    expect(xml).toContain("<lastBuildDate>Fri, 06 Nov 2026 00:00:00 GMT</lastBuildDate>");
  });
  test("Atom 1.0 and JSON Feed 1.1", () => {
    const atom = buildAtom(doc);
    assertWellFormedXml(atom);
    expect(atom).toContain('<feed xmlns="http://www.w3.org/2005/Atom" xml:lang="en">');
    expect(atom).toContain("<id>urn:learnhouse:episode_1</id>");
    expect(atom).toContain('<link rel="enclosure" type="audio/mpeg" length="1234" href="https://cdn/a.mp3"/>');
    expect(atom).toContain("<updated>2026-11-06T00:00:00.000Z</updated>");
    const minimal = buildAtom({ title: "T", homeUrl: base, feedUrls: {}, items: [] });
    assertWellFormedXml(minimal);
    expect(minimal).toContain("<author><name>T</name></author>");
    expect(minimal).toContain("<updated>1970-01-01T00:00:00.000Z</updated>");

    const json = JSON.parse(buildJsonFeed(doc));
    expect(json.version).toBe("https://jsonfeed.org/version/1.1");
    expect(json.feed_url).toBe("https://learn.acme.com/feed.json");
    expect(json.items[0]).toMatchObject({ id: `${base}/course/a`, content_html: "<p>Hi ]]> there\u0001</p>", tags: ["Course", "Q&A"], date_published: "2026-11-05T01:33:02.100Z" });
    expect(json.items[1]).toMatchObject({ content_text: "Ep", attachments: [{ url: "https://cdn/a.mp3", mime_type: "audio/mpeg", size_in_bytes: 1234.4 }] });
    expect(JSON.parse(buildJsonFeed({ title: "T", homeUrl: base, feedUrls: {}, items: [] }))).toEqual({ version: "https://jsonfeed.org/version/1.1", title: "T", home_page_url: base, items: [] });
    expect(buildFeed("atom", doc)).toBe(atom);
    expect(buildFeed("json", doc)).toContain("jsonfeed");
    expect(buildFeed("rss", doc)).toContain("<rss");
  });
  test("RSS without optional fields", () => {
    expect(xmlSafe("a\u0000b\u000bc\td")).toBe("abc\td");
    const xml = buildRss({ title: "T", homeUrl: base, feedUrls: {}, items: [{ id: "x", title: "X", url: `${base}/x` }] });
    assertWellFormedXml(xml);
    expect(xml).not.toContain("atom:link href");
    expect(xml).toContain("<description>T</description>");
  });
});

describe("feed mapping", () => {
  test("what's new: courses and paths with absolute URLs; ignores old podcast items", () => {
    const d = feedDocFromApi(
      {
        org, kind: "org",
        items: [
          { type: "course", key: "py thon", uuid: "course_1", name: "Py", description: "d", image: "t.png", authors: ["Ada", "Bob"], tags: "a, b", published: "2026-01-01" },
          { type: "path", key: "p", uuid: "path_1", name: "Path", image: "x/y.png" },
          { type: "podcast", key: "pod", uuid: "podcast_1", name: "Pod" },
        ],
      },
      { base, prefix: "/", media, lang: "en" },
    );
    expect(d.title).toBe("Acme & Co: what's new");
    expect(d.homeUrl).toBe(`${base}/`);
    expect(d.image).toBe("https://cdn/org_1/logo/l.png");
    expect(d.items.map((i) => i.url)).toEqual([`${base}/course/py%20thon`, `${base}/path/p`]);
    expect(d.items[0]).toMatchObject({ author: "Ada, Bob", categories: ["Course", "a", "b"], image: "https://cdn/org_1/c/course_1/t.png" });
    expect(d.items[1].image).toBeUndefined();
    const courses = feedDocFromApi({ org: { ...org, logo: "" }, kind: "courses", items: [] }, { base, prefix: "/courses/", media });
    expect(courses.homeUrl).toBe(`${base}/courses`);
    expect(courses.feedUrls.atom).toBe(`${base}/courses/feed.atom`);
    expect(courses.image).toBeUndefined();
  });
});

// ─── Markdown + llms.txt ────────────────────────────────────────────────────

const pmDoc = {
  type: "doc",
  content: [
    { type: "heading", attrs: { level: 1 }, content: [{ type: "text", text: "Intro" }] },
    { type: "paragraph", content: [
      { type: "text", text: "Bold", marks: [{ type: "bold" }] }, { type: "text", text: " and " },
      { type: "text", text: "it", marks: [{ type: "italic" }] }, { type: "text", text: " " },
      { type: "text", text: "link", marks: [{ type: "link", attrs: { href: "https://x.com/a b" } }] },
      { type: "text", text: " bad", marks: [{ type: "link", attrs: { href: "javascript:alert(1)" } }] },
      { type: "hardBreak" }, { type: "text", text: "a`b", marks: [{ type: "code" }] }, { type: "text", text: "gone", marks: [{ type: "strike" }] },
      { type: "mention", attrs: { label: "maya" } }, { type: "inlineMath", attrs: { latex: "x^2" } }, { type: "unknownInline", content: [{ type: "text", text: "[x]" }] },
    ] },
    { type: "bulletList", content: [{ type: "listItem", content: [{ type: "paragraph", content: [{ type: "text", text: "one" }] }, { type: "bulletList", content: [{ type: "listItem", content: [{ type: "paragraph", content: [{ type: "text", text: "nested" }] }] }] }] }] },
    { type: "orderedList", attrs: { start: 3 }, content: [{ type: "listItem", content: [{ type: "paragraph", content: [{ type: "text", text: "three" }] }] }] },
    { type: "blockquote", content: [{ type: "paragraph", content: [{ type: "text", text: "quote" }] }] },
    { type: "codeBlock", attrs: { language: "py<>" }, content: [{ type: "text", text: "print(1)\n```" }] },
    { type: "horizontalRule" },
    { type: "image", attrs: { src: "https://cdn/i.png", alt: "Alt" } },
    { type: "image", attrs: { src: "data:x" } },
    { type: "table", content: [
      { type: "tableRow", content: [{ type: "tableHeader", content: [{ type: "paragraph", content: [{ type: "text", text: "H|1" }] }] }, { type: "tableHeader", content: [{ type: "paragraph", content: [{ type: "text", text: "H2" }] }] }] },
      { type: "tableRow", content: [{ type: "tableCell", content: [{ type: "paragraph", content: [{ type: "text", text: "c" }] }] }] },
    ] },
    { type: "table", content: [] },
    { type: "videoEmbed", attrs: { src: "https://youtu.be/x", title: "Clip" } },
    { type: "callout", content: [{ type: "paragraph", content: [{ type: "text", text: "Note" }] }] },
    { type: "paragraph", content: [] },
  ],
};

describe("markdown", () => {
  test("ProseMirror to Markdown", () => {
    const md = pmToMarkdown(pmDoc);
    expect(md).toContain("## Intro");
    expect(md).toContain("**Bold** and *it* [link](https://x.com/a%20b) bad  \n`aˋb`~~gone~~@maya$x^2$\\[x\\]");
    expect(md).toContain("- one\n  - nested");
    expect(md).toContain("3. three");
    expect(md).toContain("> quote");
    expect(md).toContain("```py\nprint(1)\nˋˋˋ\n```");
    expect(md).toContain("---");
    expect(md).toContain("![Alt](https://cdn/i.png)");
    expect(md).not.toContain("data:x");
    expect(md).toContain("| H\\|1 | H2 |\n| --- | --- |\n| c |  |");
    expect(md).toContain("[Clip](https://youtu.be/x)");
    expect(md).toContain("Note");
    expect(pmToMarkdown(JSON.stringify(pmDoc))).toBe(md);
    expect(pmToMarkdown("plain *text*")).toBe("plain *text*");
    expect(pmToMarkdown("  ")).toBe("");
    expect(pmToMarkdown(null)).toBe("");
    expect(pmToMarkdown({ content: [{ type: "paragraph", content: [{ type: "text", text: "no type" }] }] })).toBe("no type");
  });

  const orgInfo = { org: { name: "Acme" } };
  const ctx = { base, canonical: `${base}/x` };
  test("course, lesson and path pages", () => {
    const course = markdownFor({
      ...orgInfo, type: "course",
      course: { key: "py", name: "Py", description: "Learn", authors: ["Ada"], learnings: '["Loops", {"text":"Types"}]', about: "About text", updated: "2026-11-01 10:00:00", language: "fr",
        outline: [{ chapter: "C1", key: "a1", name: "L1", public: true }, { chapter: "C1", key: "a2", name: "L2", public: false }, { chapter: "C2", key: "a3", name: "L3", public: true }] },
    }, ctx);
    expect(course).toStartWith('---\ntitle: "Py"\nsource: "https://learn.acme.com/x"\nsite: "Acme"\nupdated: "2026-11-01"\nlanguage: "fr"\n---');
    expect(course).toContain("Instructors: Ada");
    expect(course).toContain("## What you will learn\n\n- Loops\n- Types");
    expect(course).toContain("### C1\n\n- [L1](https://learn.acme.com/course/py/activity/a1)\n- L2 (members only)\n### C2");
    expect(markdownFor({ ...orgInfo, type: "course", course: { key: "k", name: "Bare" } }, ctx)).not.toContain("outline");

    const lesson = markdownFor({ ...orgInfo, type: "activity", course: { key: "py", name: "Py" }, activity: { name: "L1", chapter: "C1", content: pmDoc, updated: "" } }, ctx);
    expect(lesson).toContain("Part of [Py](https://learn.acme.com/course/py), chapter “C1”.");
    expect(lesson).toContain("## Intro");
    const video = markdownFor({ ...orgInfo, type: "activity", course: { key: "py", name: "Py" }, activity: { name: "V", type: "TYPE_VIDEO", content: null } }, ctx);
    expect(video).toContain("This lesson is a video activity. Open it on the website: https://learn.acme.com/x");

    const path = markdownFor({ ...orgInfo, type: "path", path: { key: "p", name: "Path", description: "d", level: "Beginner", about: "a", courses: [{ key: "c", name: "C", description: "cd", required: false }, { key: "d", name: "D" }] } }, ctx);
    expect(path).toContain("Level: Beginner");
    expect(path).toContain("1. [C](https://learn.acme.com/course/c) (elective): cd\n2. [D](https://learn.acme.com/course/d)");
    expect(markdownFor({ ...orgInfo, type: "path", path: { key: "p", name: "P" } }, ctx)).not.toContain("Courses in this path");

  });
  test("removed page types render nothing", () => {
    expect(markdownFor({ type: "community", community: { key: "c", name: "C" } }, ctx)).toBe("");
    expect(markdownFor({ type: "nope" }, ctx)).toBe("");
  });
  test("canonical paths and URL parsing", () => {
    expect(canonicalPathFor({ type: "course", course: { key: "a b" } })).toBe("/course/a%20b");
    expect(canonicalPathFor({ type: "activity", course: { key: "c" }, activity: { key: "x" } })).toBe("/course/c/activity/x");
    expect(canonicalPathFor({ type: "path", path: { key: "p" } })).toBe("/path/p");
    expect(canonicalPathFor({ type: "community", community: { key: "c" } })).toBeNull();
    expect(canonicalPathFor(null)).toBeNull();
    expect(markdownPath("/course/x/?a=1")).toBe("/course/x.md");
    expect(markdownPath("/")).toBe("/index.md");
    expect(parseMarkdownPath("/course/x/activity/y.md")).toEqual({ type: "activity", key: "x", sub: "y" });
    expect(parseMarkdownPath("/community/c/12-hello.md")).toBeNull();
    expect(parseMarkdownPath("/community/c.md")).toBeNull();
    expect(parseMarkdownPath("/podcast/p%20q.md")).toBeNull();
    expect(parseMarkdownPath("/course/%E0%A4%A.md")).toEqual({ type: "course", key: "%E0%A4%A" });
    expect(parseMarkdownPath("/index.md")).toEqual({ type: "home", key: "" });
    expect(parseMarkdownPath("/dash/x.md")).toBeNull();
  });
  test("llms.txt", () => {
    const data = {
      org: { name: "Acme", description: "Short", about: "Longer about" },
      seo: { default_meta_description: "" },
      courses: [{ key: "py", name: "Py", description: "Learn", authors: ["Ada"], outline: [{ key: "a1", name: "L1", public: true }, { key: "a2", name: "L2", public: false }] }],
      paths: [{ key: "p", name: "Path", description: "d", courses: [{ name: "Py", required: false }, { name: "Go" }] }],
      folders: [{ key: "f", name: "Shelf" }],
    };
    const short = buildLlmsTxt(data, base, { extra: [{ title: "Pages", links: [{ name: "About", url: `${base}/p/about`, note: "Who we are" }] }, { title: "Empty", links: [] }] });
    expect(short).toStartWith("# Acme\n\n> Short\n\nLonger about\n\n");
    expect(short).toContain("## Courses\n\n- [Py](https://learn.acme.com/course/py): Learn\n");
    expect(short).not.toContain("L1");
    expect(short).toContain("## Pages\n\n- [About](https://learn.acme.com/p/about): Who we are");
    expect(short).not.toContain("## Empty");
    expect(short).toContain("- [Full content index](https://learn.acme.com/llms-full.txt)");
    const full = buildLlmsTxt(data, base, { full: true });
    expect(full).toContain("  - Instructors: Ada\n  - [L1](https://learn.acme.com/course/py/activity/a1)");
    expect(full).not.toContain("L2");
    expect(full).toContain("  - Py (elective)\n  - Go");
    expect(full).not.toContain("Full content index");
    expect(buildLlmsTxt({ org: { name: "X" } }, base)).toContain("> X learning platform");
    expect(buildLlmsTxt({ org: { name: "X", about: "A", description: "A" } }, base)).not.toContain("\n\nA\n\nLearning");
  });
});

// ─── routing, robots, manifest ──────────────────────────────────────────────

describe("public SEO routes", () => {
  test("map to internal handlers", () => {
    expect(seoRouteFor("/llms.txt")).toBe("/api/seo/llms");
    expect(seoRouteFor("/llms-full.txt")).toBe("/api/seo/llms?full=1");
    expect(seoRouteFor("/manifest.webmanifest")).toBe("/api/seo/manifest");
    expect(seoRouteFor("/rss.xml")).toBe("/api/seo/feed?kind=org&format=rss");
    expect(seoRouteFor("/feed.atom")).toBe("/api/seo/feed?kind=org&format=atom");
    expect(seoRouteFor("/communities/feed.json")).toBeNull();
    expect(seoRouteFor("/community/my%20club/feed.xml")).toBeNull();
    expect(seoRouteFor("/newsletter/weekly/feed.atom")).toBe("/api/seo/feed?kind=newsletter&key=weekly&format=atom");
    expect(seoRouteFor("/podcast/show/feed")).toBeNull();
    expect(seoRouteFor("/0123456789abcdef.txt")).toBe("/api/seo/indexnow?key=0123456789abcdef"); // gitleaks:allow
    expect(seoRouteFor("/course/x.md")).toBe("/api/seo/markdown?path=%2Fcourse%2Fx.md");
    expect(seoRouteFor("/index.md")).toBe("/api/seo/markdown?path=%2Findex.md");
    for (const p of ["/robots.txt", "/ads.txt", "/course/x", "/dash/a.md", "/feed.rss"]) expect(seoRouteFor(p)).toBeNull();
  });
});

describe("robots.txt AI policy", () => {
  test("allow: content signals only", () => {
    const txt = buildRobotsTxt({ sitemapUrl: `${base}/sitemap.xml`, llmsUrl: `${base}/llms.txt` });
    expect(txt).toStartWith("# Content-Signal: search=yes, ai-input=yes, ai-train=yes\nUser-agent: *");
    expect(txt).not.toContain("GPTBot");
    expect(txt).toContain("# LLM-readable summary: https://learn.acme.com/llms.txt");
  });
  test("block training keeps answer engines", () => {
    const seo = { ai_crawler_policy: "block_training", ai_crawlers_blocked: ["MyBot", "bad name", "GPTBot"] };
    const txt = buildRobotsTxt({ sitemapUrl: "x", seo });
    expect(txt).toContain("# Content-Signal: search=yes, ai-input=yes, ai-train=no");
    expect(txt).toContain("User-agent: GPTBot\nUser-agent: ClaudeBot");
    expect(txt).toContain("User-agent: MyBot\nDisallow: /");
    expect(txt).not.toContain("OAI-SearchBot");
    expect(txt).not.toContain("bad name");
    expect(blockedAiBots(seo).filter((b) => b === "GPTBot")).toHaveLength(1);
  });
  test("block all", () => {
    const seo = { ai_crawler_policy: "block_all" };
    expect(blockedAiBots(seo)).toEqual([...AI_TRAINING_BOTS, ...AI_ANSWER_BOTS]);
    expect(contentSignal(seo)).toBe("# Content-Signal: search=yes, ai-input=no, ai-train=no");
    expect(blockedAiBots(undefined)).toEqual([]);
    expect(sanitizeRobotsRules("Content-Signal: ai-train=no\nDisallow: /x\nfoo")).toEqual(["Disallow: /x"]);
    expect(buildRobotsTxt({ sitemapUrl: "x", seo: { ...seo, indexing_disabled: true } })).toBe("User-agent: *\nDisallow: /\n");
  });
});

describe("org switches", () => {
  test("features, feeds and llms", () => {
    expect(featureIndexable({}, "courses")).toBe(true);
    expect(featureIndexable({ indexable_features: { courses: false } }, "courses")).toBe(false);
    expect(featureIndexable({ indexing_disabled: true }, "live")).toBe(false);
    expect(llmsEnabled({})).toBe(true);
    expect(llmsEnabled({ ai_crawler_policy: "block_all" })).toBe(false);
    expect(llmsEnabled({ llms_txt_enabled: false })).toBe(false);
    expect(feedsEnabled({ feeds_enabled: false })).toBe(false);
    expect(feedsEnabled(undefined)).toBe(true);
  });
});

describe("manifest", () => {
  test("org name, colors and icons; never LearnHouse icons", () => {
    const m = buildManifest({ name: "Kitebase Academy", description: "d".repeat(400), lang: "fr", themeColor: "#123456", icons: [{ src: "https://cdn/a.png", purpose: "any" }, { src: "https://cdn/a.png" }, { src: "https://cdn/b.ico" }, { src: "https://cdn/c" }] });
    expect(m).toMatchObject({ name: "Kitebase Academy", short_name: "Kitebase", lang: "fr", theme_color: "#123456", start_url: "/", display: "standalone" });
    expect(m.description).toHaveLength(300);
    expect(m.icons).toEqual([
      { src: "https://cdn/a.png", sizes: "any", type: "image/png", purpose: "any" },
      { src: "https://cdn/b.ico", sizes: "any", type: "image/x-icon" },
      { src: "https://cdn/c", sizes: "any" },
    ]);
    expect(buildManifest({ name: "X", icons: [] })).not.toHaveProperty("icons");
    expect(buildManifest({ name: "X", icons: [] })).toMatchObject({ lang: "en", theme_color: "#ffffff" });
    expect(shortName("Short")).toBe("Short");
    expect(shortName("Supercalifragilistic Academy")).toBe("Supercalifra");
    expect(imageMime("a.svg?v=1")).toBe("image/svg+xml");
  });
});

describe("landing FAQ", () => {
  test("visible FAQ blocks only", () => {
    const doc = {
      root: [
        { type: "section", children: [{ type: "faq", props: { items: [{ question: " Q1 ", answer: "A1" }, { question: "", answer: "x" }] } }] },
        { type: "faq", visibility: "logged_in", props: { items: [{ question: SECRET, answer: "x" }] } },
        { type: "faq", hidden: { desktop: true, tablet: true, mobile: true }, props: { items: [{ question: SECRET, answer: "x" }] } },
        { type: "faq", hidden: { mobile: true }, props: { items: [{ question: "Q2", answer: "A2" }] } },
        null,
      ],
    };
    expect(landingFaq(doc)).toEqual([{ question: "Q1", answer: "A1" }, { question: "Q2", answer: "A2" }]);
    expect(landingFaq(null)).toEqual([]);
  });
});

// ─── structured data validity ───────────────────────────────────────────────

describe("JSON-LD is valid schema.org", () => {
  const o = { name: "Acme", url: base, logo: "https://cdn/logo.png" };
  const samples = {
    organization: ld.organizationJsonLd({ ...o, description: "d", sameAs: ["https://x.com/acme"], type: "CollegeOrUniversity" }),
    website: ld.websiteJsonLd({ name: "Acme", url: base, inLanguage: "en", searchUrlTemplate: `${base}/search?q={search_term_string}` }),
    breadcrumb: ld.breadcrumbJsonLd([{ name: "Home", url: base }, { name: "C", url: `${base}/c` }]),
    itemList: ld.itemListJsonLd("Courses", [{ url: `${base}/a`, name: "A" }]),
    course: ld.courseJsonLd({
      name: "Py", url: `${base}/course/py`, description: "Learn", image: "https://cdn/c.png", org: o, authors: [{ name: "Ada" }],
      inLanguage: "en", educationalLevel: "beginner", workloadMinutes: 150, offers: [{ price: 10, currency: "USD" }], teaches: ["Loops"],
      prerequisites: "None", audience: "Beginners", datePublished: "2026-01-01", dateModified: "2026-02-01", keywords: "py",
      syllabus: [{ name: "C1", description: "First" }], availableLanguage: ["en", "fr"], about: "About", aggregateRating: { ratingValue: 4.5, ratingCount: 10 }, enrollmentCount: 12,
    }),
    lesson: ld.learningResourceJsonLd({ name: "L", url: `${base}/l`, description: "d", courseName: "Py", courseUrl: `${base}/course/py`, dateModified: "2026-01-01", inLanguage: "en", isFree: true }),
    path: ld.pathJsonLd({ name: "P", url: `${base}/path/p`, description: "d", org: o, courses: [{ name: "C", url: `${base}/c` }], educationalLevel: "beginner", inLanguage: "en" }),
    faq: ld.faqJsonLd([{ question: "Q", answer: "A" }]),
    event: ld.eventJsonLd({ name: "AMA", url: `${base}/live/x`, startDate: "2026-11-25T00:33:00Z", endDate: "2026-11-25T01:33:00Z", org: o, performers: [{ name: "Ada" }], isFree: true }),
    collection: ld.collectionJsonLd({ name: "Shelf", url: `${base}/f`, org: o, items: [{ name: "A", url: `${base}/a` }] }),
    credential: ld.credentialJsonLd({ name: "Certified", url: `${base}/certificates/X/verify`, description: "d", issuedOn: "2026-10-31 11:12:46", org: o, courseName: "Py", courseUrl: `${base}/course/py`, credentialId: "X" }),
    product: ld.productJsonLd({ name: "Pro", url: `${base}/store/offers/1`, description: "d", price: 19, currency: "eur", org: o }),
  };
  for (const [name, data] of Object.entries(samples)) {
    test(name, () => {
      expect(data).not.toBeNull();
      expect(validateJsonLd(data)).toEqual([]);
    });
  }
  test("product offer price formatting; no offer without a price", () => {
    expect(samples.product.offers).toMatchObject({ price: "19.00", priceCurrency: "EUR" });
    expect(ld.productJsonLd({ name: "Free", url: base, org: o }).offers).toBeUndefined();
    expect(ld.credentialJsonLd({ name: "C", url: base, org: o }).about).toBeUndefined();
  });
  test("the validator catches mistakes", () => {
    expect(validateJsonLd({ "@type": "Nope" })).toEqual(["$: @context must be https://schema.org", "$: unknown type Nope"]);
    expect(validateJsonLd({ "@context": "https://schema.org", "@type": "Event", name: "" })).toContain("$: Event is missing startDate");
    expect(validateJsonLd([{ "@context": "https://schema.org", "@type": "FAQPage", mainEntity: [] }])).toContain("$[0].mainEntity: empty value");
  });
});
