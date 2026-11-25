/* Each section is a complete, independently addressable HTML document. */
const sectionPaths = new Set(["/", "/docs", "/pricing"]);
const pageCache = new Map();
const metadataSelector = 'meta[name="description"], meta[property^="og:"], meta[name^="twitter:"], link[rel="canonical"], script[type="application/ld+json"]';
let pendingNavigation;

async function loadPage(url, signal) {
  const key = url.pathname + url.search;
  if (pageCache.has(key)) return pageCache.get(key);
  const response = await fetch(url, { signal });
  if (!response.ok) throw new Error("Section unavailable");
  const page = new DOMParser().parseFromString(await response.text(), "text/html");
  if (!page.querySelector("main") || !page.querySelector(".site-tabs")) throw new Error("Unexpected page");
  if (!url.search) pageCache.set(key, page);
  return page;
}

async function visit(url) {
  pendingNavigation?.abort();
  const request = new AbortController();
  pendingNavigation = request;
  try {
    const next = await loadPage(url, request.signal);
    if (request.signal.aborted) return;
    const main = next.querySelector("main").cloneNode(true);
    document.querySelector("main").replaceWith(main);
    document.title = next.title;
    for (const tag of document.head.querySelectorAll(metadataSelector)) tag.remove();
    for (const tag of next.head.querySelectorAll(metadataSelector)) document.head.append(tag.cloneNode(true));
    for (const link of document.querySelectorAll(".site-tabs a")) {
      if (link.pathname === url.pathname) link.setAttribute("aria-current", "page");
      else link.removeAttribute("aria-current");
    }
    history.pushState(null, "", url);
    main.focus({ preventScroll: true });
    const target = document.getElementById(url.hash.slice(1));
    if (target) target.scrollIntoView();
    else window.scrollTo(0, 0);
    document.dispatchEvent(new CustomEvent("page:navigated", { detail: { url } }));
  } catch (error) {
    if (!request.signal.aborted) location.assign(url);
  } finally {
    if (pendingNavigation === request) pendingNavigation = undefined;
  }
}

document.addEventListener("click", event => {
  const link = event.target.closest("a[href]");
  if (!link || event.defaultPrevented || event.button !== 0 || event.metaKey || event.ctrlKey || event.shiftKey || event.altKey || link.target || link.hasAttribute("download")) return;
  const url = new URL(link.href);
  if (url.origin !== location.origin || !sectionPaths.has(url.pathname)) return;
  if (url.pathname === location.pathname) {
    pendingNavigation?.abort();
    return;
  }
  event.preventDefault();
  visit(url);
});

window.addEventListener("popstate", () => {
  pendingNavigation?.abort();
  if (document.querySelector(".site-tabs [aria-current]").pathname !== location.pathname) location.reload();
});
