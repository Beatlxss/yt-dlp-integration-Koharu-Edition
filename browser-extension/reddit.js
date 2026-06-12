(() => {
  "use strict";

  const BUTTON_ATTR = "data-ytdlp-reddit-download-btn";
  const BUTTON_STATE_CLASS = "ytdlp-reddit-btn-state";
  const BUTTON_BUSY_CLASS = "ytdlp-reddit-btn-busy";
  const BUTTON_DONE_CLASS = "ytdlp-reddit-btn-done";
  const BUTTON_ERROR_CLASS = "ytdlp-reddit-btn-error";
  const BUTTON_BASE_CLASS = "ytdlp-reddit-btn-base";
  const STYLE_ID = "ytdlp-reddit-style";

  const POST_SELECTORS = [
    "shreddit-post",
    "article",
    "div[data-testid='post-container']",
    "div[data-click-id='body']",
  ].join(",");

  let processScheduled = false;

  /** Injects styles. */
  function injectStyles() {
    if (document.getElementById(STYLE_ID)) return;

    const style = document.createElement("style");
    style.id = STYLE_ID;
    style.textContent = `
      .${BUTTON_BASE_CLASS} {
        margin-inline-start: 8px !important;
        display: inline-flex;
        align-items: center;
        gap: 6px;
        position: relative;
        z-index: 2;
      }

      .${BUTTON_BASE_CLASS} svg {
        width: 16px;
        height: 16px;
        flex: 0 0 auto;
      }

      .${BUTTON_STATE_CLASS}.${BUTTON_BUSY_CLASS} {
        opacity: 0.7;
        pointer-events: none;
      }

      .${BUTTON_STATE_CLASS}.${BUTTON_DONE_CLASS} {
        color: rgb(42, 169, 82) !important;
      }

      .${BUTTON_STATE_CLASS}.${BUTTON_ERROR_CLASS} {
        color: rgb(216, 68, 31) !important;
      }
    `;

    const host = document.head || document.documentElement;
    if (host) host.appendChild(style);
  }

  /** Gets search roots. */
  function getSearchRoots() {
    return [document];
  }

  /** Schedules process. */
  function scheduleProcess() {
    if (processScheduled) return;
    processScheduled = true;

    requestAnimationFrame(() => {
      processScheduled = false;
      processPage();
    });
  }

  /** Gets closest post node. */
  function getClosestPostNode(node) {
    if (!(node instanceof Element)) return null;

    const direct = node.closest(POST_SELECTORS);
    if (direct) return direct;

    if (node.getRootNode && node.getRootNode() instanceof ShadowRoot) {
      const host = node.getRootNode().host;
      if (host instanceof Element) return host.closest(POST_SELECTORS) || host;
    }

    return null;
  }

  /** Normalizes image url. */
  function normalizeImageUrl(rawUrl) {
    try {
      const url = new URL(String(rawUrl || ""), window.location.origin);
      if (!url.protocol.startsWith("http")) return null;
      if (url.pathname.endsWith(".svg")) return null;
      url.hash = "";
      return url.toString();
    } catch {
      return null;
    }
  }

  /** Normalizes video url. */
  function normalizeVideoUrl(rawUrl) {
    try {
      const url = new URL(String(rawUrl || ""), window.location.origin);
      if (!url.protocol.startsWith("http")) return null;
      if (url.protocol === "blob:") return null;
      url.hash = "";
      return url.toString();
    } catch {
      return null;
    }
  }

  /** Checks whether it has youtube media. */
  function hasYoutubeMedia(postNode) {
    if (!(postNode instanceof Element)) return false;

    return Boolean(
      postNode.querySelector(
        "iframe[src*='youtube.com'], iframe[src*='youtube-nocookie.com'], iframe[src*='youtu.be'], a[href*='youtube.com/watch'], a[href*='youtu.be/']",
      ),
    );
  }

  /** Gets post content href. */
  function getPostContentHref(postNode) {
    if (!(postNode instanceof Element)) return "";

    const raw =
      postNode.getAttribute("content-href") ||
      postNode.getAttribute("url") ||
      "";

    if (!raw) return "";

    try {
      return new URL(raw, window.location.origin).toString();
    } catch {
      return "";
    }
  }

  /** Checks whether it is youtube url. */
  function isYoutubeUrl(urlString) {
    try {
      const u = new URL(String(urlString || ""), window.location.origin);
      const host = u.hostname.toLowerCase();
      return (
        host.includes("youtube.com") ||
        host.includes("youtube-nocookie.com") ||
        host.includes("youtu.be")
      );
    } catch {
      return false;
    }
  }

  /** Checks whether it has youtube post link. */
  function hasYoutubePostLink(postNode) {
    if (!(postNode instanceof Element)) return false;

    const contentHref = getPostContentHref(postNode);
    if (contentHref && isYoutubeUrl(contentHref)) return true;

    const domain = String(postNode.getAttribute("domain") || "").toLowerCase();
    if (domain.includes("youtube.com") || domain.includes("youtu.be")) {
      return true;
    }

    const anchors = postNode.querySelectorAll(
      "a[href*='youtube.com'], a[href*='youtu.be']",
    );
    return anchors.length > 0;
  }

  /** Gets post media root. */
  function getPostMediaRoot(postNode) {
    if (!(postNode instanceof Element)) return null;

    const slotted = postNode.querySelector("[slot='post-media-container']");
    if (slotted instanceof Element) return slotted;

    if (postNode.tagName === "SHREDDIT-POST" && postNode.shadowRoot) {
      const slot = postNode.shadowRoot.querySelector(
        "slot[name='post-media-container']",
      );
      if (slot instanceof HTMLSlotElement) {
        const assigned = slot.assignedElements({ flatten: true });
        if (assigned[0] instanceof Element) return assigned[0];
      }
    }

    return null;
  }

  /** Checks whether it has downloadable media. */
  function hasDownloadableMedia(postNode) {
    if (!(postNode instanceof Element)) return false;

    const postType = String(
      postNode.getAttribute("post-type") || "",
    ).toLowerCase();
    if (
      postType.includes("image") ||
      postType.includes("gallery") ||
      postType.includes("video")
    ) {
      return true;
    }

    const mediaRoot = getPostMediaRoot(postNode);

    if (
      mediaRoot &&
      mediaRoot.querySelector(
        "video, shreddit-player, source[type*='mp4'], a[href*='v.redd.it']",
      )
    ) {
      return true;
    }

    if (
      hasYoutubePostLink(postNode) ||
      (mediaRoot && hasYoutubeMedia(mediaRoot)) ||
      hasYoutubeMedia(postNode)
    ) {
      return true;
    }

    if (!mediaRoot) return false;

    return Boolean(
      mediaRoot.querySelector(
        "img[src*='i.redd.it'], img[src*='preview.redd.it'], img[src*='external-preview.redd.it'], img[src*='redditmedia.com']",
      ),
    );
  }

  /** Pick largest candidate. */
  function pickLargestCandidate(nodes, readUrl) {
    let best = null;

    for (const node of nodes) {
      if (!(node instanceof Element)) continue;

      const raw = readUrl(node);
      const normalized = normalizeImageUrl(raw);
      if (!normalized) continue;

      const rect = node.getBoundingClientRect
        ? node.getBoundingClientRect()
        : null;
      const area = rect
        ? Math.max(0, rect.width) * Math.max(0, rect.height)
        : 0;

      if (!best || area > best.area) {
        best = { url: normalized, area };
      }
    }

    return best ? best.url : null;
  }

  /** Pick active media image url. */
  function pickActiveMediaImageUrl(postNode, nodes, readUrl) {
    if (!(postNode instanceof Element)) return null;

    const viewportCenterX = window.innerWidth / 2;
    const viewportCenterY = window.innerHeight / 2;
    let best = null;

    for (const node of nodes) {
      if (!(node instanceof Element)) continue;

      const raw = readUrl(node);
      const normalized = normalizeImageUrl(raw);
      if (!normalized) continue;

      const rect = node.getBoundingClientRect
        ? node.getBoundingClientRect()
        : null;
      if (!rect) continue;

      // Skip tiny/non-media images (avatars/icons).
      if (rect.width < 120 || rect.height < 120) continue;

      const cs = getComputedStyle(node);
      const opacity = Number(cs.opacity || "1");
      const isHidden =
        cs.display === "none" || cs.visibility === "hidden" || opacity < 0.1;
      if (isHidden) continue;

      // Reddit gallery thumbnails/overlay layers are often partially transparent.
      // Ignore those so we pick the currently shown media frame.
      if (opacity < 0.85) continue;

      const intersectsViewport =
        rect.right > 0 &&
        rect.bottom > 0 &&
        rect.left < window.innerWidth &&
        rect.top < window.innerHeight;
      if (!intersectsViewport) continue;

      const area = rect.width * rect.height;
      const centerX = rect.left + rect.width / 2;
      const centerY = rect.top + rect.height / 2;
      const distance =
        Math.abs(centerX - viewportCenterX) +
        Math.abs(centerY - viewportCenterY);

      let hostBonus = 0;
      try {
        const host = new URL(normalized).hostname;
        if (host.includes("i.redd.it")) hostBonus = 200000;
      } catch {
        // ignore URL parse errors
      }

      // Prefer large visible media closest to viewport center (active gallery slide).
      const score = area - distance * 100 + hostBonus;

      if (!best || score > best.score) {
        best = { url: normalized, score };
      }
    }

    return best ? best.url : null;
  }

  /** Extracts post permalink. */
  function extractPostPermalink(postNode) {
    if (!(postNode instanceof Element)) return window.location.href;

    const link =
      postNode.querySelector("a[data-click-id='comments']") ||
      postNode.querySelector("a[href*='/comments/']") ||
      postNode.querySelector("faceplate-tracker a[href*='/comments/']");

    const href =
      (link && (link.getAttribute("href") || link.href)) ||
      postNode.getAttribute("permalink") ||
      window.location.href;

    try {
      return new URL(String(href), window.location.origin).toString();
    } catch {
      return window.location.href;
    }
  }

  /** Resolves download target. */
  function resolveDownloadTarget(postNode) {
    const permalink = extractPostPermalink(postNode);
    const mediaRoot = getPostMediaRoot(postNode) || postNode;
    const contentHref = getPostContentHref(postNode);

    const postType = String(
      postNode.getAttribute("post-type") || "",
    ).toLowerCase();
    const hasVideoLikeMarkers =
      postType.includes("video") ||
      hasYoutubePostLink(postNode) ||
      hasYoutubeMedia(mediaRoot) ||
      hasYoutubeMedia(postNode) ||
      Boolean(
        mediaRoot.querySelector(
          "video, shreddit-player, source[type*='mp4'], a[href*='v.redd.it']",
        ),
      );

    // For Reddit video posts, sending the permalink to localhost is more reliable
    // than trying to pass in-page player URLs.
    if (hasVideoLikeMarkers) {
      return {
        method: "localhost",
        url:
          hasYoutubePostLink(postNode) && contentHref ? contentHref : permalink,
        permalink,
      };
    }

    const videoNodes = [
      ...mediaRoot.querySelectorAll("video"),
      ...mediaRoot.querySelectorAll("video source"),
      ...mediaRoot.querySelectorAll("shreddit-player source"),
      ...mediaRoot.querySelectorAll("source[type*='mp4']"),
    ];

    for (const node of videoNodes) {
      const raw =
        node.getAttribute("src") ||
        node.src ||
        (node.currentSrc ? node.currentSrc : "");
      const videoUrl = normalizeVideoUrl(raw);
      if (videoUrl) {
        return { method: "localhost", url: videoUrl, permalink };
      }
    }

    const videoLink = mediaRoot.querySelector("a[href*='v.redd.it']");
    if (videoLink) {
      const videoUrl = normalizeVideoUrl(
        videoLink.getAttribute("href") || videoLink.href || "",
      );
      if (videoUrl) {
        return { method: "localhost", url: videoUrl, permalink };
      }
    }

    const imageCandidates = [
      ...mediaRoot.querySelectorAll("img[src*='i.redd.it']"),
      ...mediaRoot.querySelectorAll("img[src*='preview.redd.it']"),
      ...mediaRoot.querySelectorAll("img[src*='external-preview.redd.it']"),
      ...mediaRoot.querySelectorAll("img[src*='redditmedia.com']"),
    ];

    const activeImageUrl = pickActiveMediaImageUrl(
      postNode,
      imageCandidates,
      (node) => {
        return node.currentSrc || node.getAttribute("src") || "";
      },
    );

    if (activeImageUrl) {
      return { method: "chrome", url: activeImageUrl, permalink };
    }

    const imageUrl = pickLargestCandidate(imageCandidates, (node) => {
      return node.currentSrc || node.getAttribute("src") || "";
    });

    if (imageUrl) {
      return { method: "chrome", url: imageUrl, permalink };
    }

    return { method: "localhost", url: permalink, permalink };
  }

  /** Requests localhost download. */
  async function requestLocalhostDownload(url) {
    const resp = await chrome.runtime.sendMessage({
      type: "localhostDownload",
      url,
    });

    if (!resp || !resp.ok) {
      throw new Error((resp && resp.error) || "localhost download failed");
    }
  }

  /** Requests chrome download. */
  async function requestChromeDownload(url) {
    const resp = await chrome.runtime.sendMessage({
      type: "chromeDownload",
      url,
    });

    if (!resp || !resp.ok) {
      throw new Error((resp && resp.error) || "chrome download failed");
    }
  }

  /** Sets button state. */
  function setButtonState(button, state) {
    button.classList.add(BUTTON_STATE_CLASS);
    button.classList.toggle(BUTTON_BUSY_CLASS, state === "busy");
    button.classList.toggle(BUTTON_DONE_CLASS, state === "done");
    button.classList.toggle(BUTTON_ERROR_CLASS, state === "error");

    if (state === "busy") {
      button.setAttribute("aria-disabled", "true");
      button.disabled = true;
      button.title = "Downloading...";
      return;
    }

    button.removeAttribute("aria-disabled");
    button.disabled = false;

    if (state === "done") {
      button.title = "Download started";
    } else if (state === "error") {
      button.title = "Download failed";
    } else {
      button.title = "Download";
    }
  }

  /** Builds button label. */
  function buildButtonLabel(button) {
    const labelCandidates = [
      ...button.querySelectorAll("span"),
      ...button.querySelectorAll("p"),
      ...button.querySelectorAll("div"),
    ];

    for (const candidate of labelCandidates) {
      const text = String(candidate.textContent || "")
        .trim()
        .toLowerCase();
      if (!text) continue;

      if (
        text.includes("share") ||
        text.includes("award") ||
        text.includes("comment")
      ) {
        candidate.textContent = "Download";
        return;
      }
    }

    const fallbackLabel = button.querySelector("span") || button;

    if (fallbackLabel === button) {
      button.textContent = "Download";
      return;
    }

    fallbackLabel.textContent = "Download";
  }

  /** Sets download icon. */
  function setDownloadIcon(button) {
    const icon =
      '<svg viewBox="0 0 24 24" fill="none" xmlns="http://www.w3.org/2000/svg" aria-hidden="true">' +
      '<path d="M6 21H18M12 3V17M12 17L17 12M12 17L7 12" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"/>' +
      "</svg>";

    const existingSvg = button.querySelector("svg");
    if (existingSvg) {
      existingSvg.outerHTML = icon;
      return;
    }

    const prependTarget =
      button.querySelector("span") || button.querySelector("div") || button;

    if (prependTarget instanceof Element) {
      prependTarget.insertAdjacentHTML("afterbegin", icon);
    }
  }

  /** Creates download button. */
  function createDownloadButton(shareButton) {
    const sourceClass =
      shareButton instanceof HTMLElement
        ? String(shareButton.className || "").trim()
        : "";

    const button = document.createElement("button");
    button.type = "button";
    button.className =
      sourceClass ||
      [
        "button",
        "border-md",
        "font-semibold",
        "text-caption-1",
        "button-secondary",
        "inline-flex",
        "items-center",
        "px-sm",
      ].join(" ");

    if (!(button instanceof HTMLElement)) return null;

    button.removeAttribute("id");
    button.classList.add(BUTTON_BASE_CLASS);
    button.setAttribute(BUTTON_ATTR, "1");
    button.setAttribute("aria-label", "Download");
    button.setAttribute("title", "Download");

    button.textContent = "Download";
    setDownloadIcon(button);
    setButtonState(button, "idle");

    button.addEventListener("click", async (event) => {
      event.preventDefault();
      event.stopPropagation();

      if (button.disabled) return;

      const postNode =
        getClosestPostNode(button) || getClosestPostNode(shareButton);
      if (!(postNode instanceof Element)) {
        setButtonState(button, "error");
        setTimeout(() => setButtonState(button, "idle"), 1400);
        return;
      }

      const target = resolveDownloadTarget(postNode);
      setButtonState(button, "busy");

      try {
        if (target.method === "chrome") {
          await requestChromeDownload(target.url);
        } else {
          await requestLocalhostDownload(target.url || target.permalink);
        }

        setButtonState(button, "done");
      } catch (err) {
        console.error("ytdlp reddit download failed", err);
        setButtonState(button, "error");
      }

      setTimeout(() => {
        if (button.isConnected) setButtonState(button, "idle");
      }, 1200);
    });

    return button;
  }

  /** Injects into shreddit post. */
  function injectIntoShredditPost(post) {
    if (!(post instanceof HTMLElement) || post.tagName !== "SHREDDIT-POST") {
      return false;
    }

    const host = post.shadowRoot;
    if (!host) return false;

    // Remove any legacy light-DOM injected buttons from older logic.
    for (const legacy of post.querySelectorAll(`[${BUTTON_ATTR}]`)) {
      legacy.remove();
    }

    const actionRow =
      host.querySelector("div.shreddit-post-container") ||
      host.querySelector("div[class*='shreddit-post-container']");
    if (!(actionRow instanceof HTMLElement)) return false;

    if (!hasDownloadableMedia(post)) {
      const existingInRow = actionRow.querySelector(`[${BUTTON_ATTR}]`);
      if (existingInRow) existingInRow.remove();
      return false;
    }

    const existing = actionRow.querySelector(`[${BUTTON_ATTR}]`);
    if (existing) return true;

    const commentsAction =
      actionRow.querySelector(
        "button[data-post-click-location='comments-button']",
      ) ||
      actionRow.querySelector(
        "a[data-post-click-location='comments-button']",
      ) ||
      [...actionRow.querySelectorAll("button, a")].find((el) =>
        String(el.textContent || "")
          .toLowerCase()
          .includes("comment"),
      ) ||
      null;

    const template =
      commentsAction ||
      actionRow.querySelector("button, a") ||
      document.createElement("button");
    const btn = createDownloadButton(template);
    if (!btn) return false;

    if (commentsAction instanceof HTMLElement) {
      commentsAction.insertAdjacentElement("afterend", btn);
    } else {
      actionRow.appendChild(btn);
    }

    return true;
  }

  /** Process page. */
  function processPage() {
    injectStyles();

    // Remove any stale buttons accidentally injected outside post action rows.
    for (const stray of document.querySelectorAll(`[${BUTTON_ATTR}]`)) {
      const row = stray.closest(
        "div.shreddit-post-container, div[class*='shreddit-post-container']",
      );
      if (!row) stray.remove();
    }

    const posts = document.querySelectorAll("shreddit-post");
    for (const post of posts) {
      injectIntoShredditPost(post);
    }
  }

  const observer = new MutationObserver(() => scheduleProcess());
  observer.observe(document.documentElement, {
    childList: true,
    subtree: true,
  });

  if (document.readyState === "loading") {
    document.addEventListener("DOMContentLoaded", () => scheduleProcess(), {
      once: true,
    });
  }

  scheduleProcess();
})();

