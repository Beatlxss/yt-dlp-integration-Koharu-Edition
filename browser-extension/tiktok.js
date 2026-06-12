(() => {
  "use strict";

  const STYLE_ID = "ytdlp-tiktok-style";
  const BUTTON_CLASS = "ytdlp-tiktok-download-btn";
  const BUTTON_BUSY_CLASS = "ytdlp-tiktok-download-btn-busy";
  const BUTTON_DONE_CLASS = "ytdlp-tiktok-download-btn-done";
  const BUTTON_ERROR_CLASS = "ytdlp-tiktok-download-btn-error";
  const BUTTON_INLINE_CLASS = "ytdlp-tiktok-download-btn-inline";
  const BUTTON_ATTR = "data-ytdlp-tiktok-btn";
  const FLOATING_HOST_ID = "ytdlp-tiktok-floating-host";
  const ACTION_BAR_SELECTOR = '[class*="SectionActionBarContainer"]';
  const INLINE_ROW_SELECTOR = '[class*="DivFlexCenterRow"]';
  const SIDEBAR_SELECTOR = `${ACTION_BAR_SELECTOR}, [data-e2e="video-sidebar"]`;
  const BOOTSTRAP_INTERVAL_MS = 250;
  const BOOTSTRAP_MAX_TICKS = 120;

  let processScheduled = false;
  let bootstrapTicks = 0;
  let bootstrapTimer = null;

  /** Injects once-only CSS used by sidebar and floating download buttons. */
  function injectStyles() {
    if (document.getElementById(STYLE_ID)) return;

    const style = document.createElement("style");
    style.id = STYLE_ID;
    style.textContent = `
			.${BUTTON_CLASS} {
				appearance: none;
				width: 48px;
				height: 48px;
				border: 0;
				border-radius: 9999px;
				margin: 0 0 10px;
				padding: 0;
				display: flex;
				align-items: center;
				justify-content: center;
				cursor: pointer;
				color: rgb(32, 201, 151);
				background: rgba(255, 255, 255, 0.12);
				box-shadow: 0 8px 18px rgba(0, 0, 0, 0.18);
				transition: transform 120ms ease, opacity 120ms ease, filter 120ms ease;
			}

			.${BUTTON_CLASS}:hover {
				background: rgba(37, 37, 37, 1);
				filter: none;
				transform: none;
			}

			.${BUTTON_CLASS}:active {
				transform: translateY(0);
				opacity: 0.9;
			}

			.${BUTTON_CLASS}.${BUTTON_BUSY_CLASS} {
				opacity: 0.75;
				cursor: wait;
			}

			.${BUTTON_CLASS}.${BUTTON_DONE_CLASS} {
				background: rgba(32, 201, 151, 0.18);
			}

			.${BUTTON_CLASS}.${BUTTON_ERROR_CLASS} {
				background: rgba(216, 68, 31, 0.5);
			}

			.${BUTTON_CLASS} svg {
				width: 22px;
				height: 22px;
				display: block;
			}

      .${BUTTON_CLASS}.${BUTTON_INLINE_CLASS} {
        width: 32px;
        height: 32px;
        min-width: 32px;
        margin: 0;
        padding: 0;
		gap: 10px;
        border-radius: 9999px;
        background: rgba(255, 255, 255, 0.12);
        box-shadow: none;
        color: rgb(32, 201, 151);
        filter: none;
        transform: none;
      }

      .${BUTTON_CLASS}.${BUTTON_INLINE_CLASS}:hover {
        background: rgba(37, 37, 37, 1);
        filter: none;
        transform: none;
      }

      .${BUTTON_CLASS}.${BUTTON_INLINE_CLASS}.${BUTTON_DONE_CLASS} {
        background: rgba(32, 201, 151, 0.18);
      }

      .${BUTTON_CLASS}.${BUTTON_INLINE_CLASS}.${BUTTON_ERROR_CLASS} {
        background: rgba(216, 68, 31, 0.5);
        color: rgb(216, 68, 31);
      }

			#${FLOATING_HOST_ID} {
				position: fixed;
				right: 14px;
				top: 42%;
				z-index: 2147483647;
				display: flex;
				align-items: center;
				justify-content: center;
				pointer-events: none;
			}

			#${FLOATING_HOST_ID} .${BUTTON_CLASS} {
				margin: 0;
				pointer-events: auto;
			}
		`;

    const host = document.head || document.documentElement;
    if (host) host.appendChild(style);
  }

  /** Returns the download icon SVG markup as an inline HTML string. */
  function downloadSvgHtml() {
    return (
      '<svg viewBox="0 0 24 24" fill="none" xmlns="http://www.w3.org/2000/svg" aria-hidden="true">' +
      '<path d="M6 21H18M12 3V17M12 17L17 12M12 17L7 12" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"/>' +
      "</svg>"
    );
  }

  /** Normalizes a TikTok video URL to canonical @user/video/id format. */
  function normalizeTikTokUrl(rawUrl) {
    try {
      const url = new URL(String(rawUrl || ""), window.location.origin);
      if (!url.hostname.includes("tiktok.com")) return null;
      if (!url.pathname.includes("/video/")) return null;
      const canonicalMatch = url.pathname.match(/^\/@[^/]+\/video\/\d+/);
      if (!canonicalMatch) return null;
      url.pathname = canonicalMatch[0];
      url.hash = "";
      url.search = "";
      return url.toString();
    } catch {
      return null;
    }
  }

  /** Attempts to extract a canonical video URL from meta/canonical tags and page HTML. */
  function extractCanonicalVideoUrl() {
    const directCandidates = [
      document.querySelector('link[rel="canonical"]')?.getAttribute("href") ||
        "",
      document
        .querySelector('meta[property="og:url"]')
        ?.getAttribute("content") || "",
      window.location.href,
    ];

    for (const candidate of directCandidates) {
      const normalized = normalizeTikTokUrl(candidate);
      if (normalized) return normalized;
    }

    try {
      const html = document.documentElement?.innerHTML || "";
      const match = html.match(
        /https:\/\/www\.tiktok\.com\/@[^/\"']+\/video\/\d+/i,
      );
      if (match && match[0]) {
        const normalized = normalizeTikTokUrl(match[0]);
        if (normalized) return normalized;
      }
    } catch {
      // ignore
    }

    return null;
  }

  /** Reads the best video URL signal directly from document-level metadata. */
  function videoUrlFromDocument() {
    return extractCanonicalVideoUrl();
  }

  /** Returns whether an element is at least partially visible in the viewport. */
  function isElementInViewport(element) {
    if (!(element instanceof Element)) return false;
    const rect = element.getBoundingClientRect?.();
    if (!rect) return false;

    return (
      rect.bottom > 0 &&
      rect.right > 0 &&
      rect.top < window.innerHeight &&
      rect.left < window.innerWidth
    );
  }

  /** Computes DOM-tree distance between two elements by nearest shared ancestor depth. */
  function elementTreeDistance(left, right) {
    if (!(left instanceof Element) || !(right instanceof Element)) {
      return Number.POSITIVE_INFINITY;
    }

    if (left === right) return 0;

    const leftDepthMap = new Map();
    let leftNode = left;
    let leftDepth = 0;
    while (leftNode instanceof Element) {
      leftDepthMap.set(leftNode, leftDepth);
      leftNode = leftNode.parentElement;
      leftDepth += 1;
    }

    let rightNode = right;
    let rightDepth = 0;
    while (rightNode instanceof Element) {
      const sharedDepth = leftDepthMap.get(rightNode);
      if (typeof sharedDepth === "number") {
        return sharedDepth + rightDepth;
      }
      rightNode = rightNode.parentElement;
      rightDepth += 1;
    }

    return Number.POSITIVE_INFINITY;
  }

  /** Selects the best canonical video URL from link candidates near a reference element. */
  function pickBestVideoUrlFromLinks(links, referenceElement) {
    let bestUrl = null;
    let bestScore = Number.POSITIVE_INFINITY;

    for (const link of links) {
      if (!(link instanceof HTMLAnchorElement)) continue;
      const href = link.getAttribute("href") || link.href || "";
      const normalized = normalizeTikTokUrl(href);
      if (!normalized) continue;

      const visibleBonus = isElementInViewport(link) ? 0 : 10;
      let score = visibleBonus;

      if (referenceElement instanceof Element) {
        score += elementTreeDistance(referenceElement, link);
      } else {
        const rect = link.getBoundingClientRect?.();
        if (rect) {
          const centerX = rect.left + rect.width / 2;
          const centerY = rect.top + rect.height / 2;
          const deltaX = Math.abs(centerX - window.innerWidth / 2);
          const deltaY = Math.abs(centerY - window.innerHeight / 2);
          score += (deltaX + deltaY) / 180;
        }
      }

      if (score < bestScore) {
        bestScore = score;
        bestUrl = normalized;
      }
    }

    return bestUrl;
  }

  /** Returns visible area in pixels for an element clipped by current viewport. */
  function visibleViewportArea(element) {
    if (!(element instanceof Element)) return 0;
    const rect = element.getBoundingClientRect?.();
    if (!rect) return 0;

    const left = Math.max(0, rect.left);
    const top = Math.max(0, rect.top);
    const right = Math.min(window.innerWidth, rect.right);
    const bottom = Math.min(window.innerHeight, rect.bottom);
    const width = Math.max(0, right - left);
    const height = Math.max(0, bottom - top);
    return width * height;
  }

  /** Selects the active/visible feed video, preferring proximity to the provided reference. */
  function pickActiveVideoElement(referenceElement) {
    const videos = [...document.querySelectorAll("video")].filter(
      (video) =>
        video instanceof HTMLVideoElement &&
        video.getBoundingClientRect?.().width >= 120 &&
        video.getBoundingClientRect?.().height >= 120,
    );

    if (!videos.length) return null;

    let bestVideo = null;
    let bestScore = Number.POSITIVE_INFINITY;

    for (const video of videos) {
      const area = visibleViewportArea(video);
      if (area <= 0) continue;

      const isPlaying = !video.paused && !video.ended;
      const playBonus = isPlaying ? -40 : 0;
      const areaBonus = -Math.min(area / 12000, 60);
      let score = playBonus + areaBonus;

      if (referenceElement instanceof Element) {
        score += elementTreeDistance(referenceElement, video);
      } else {
        const rect = video.getBoundingClientRect?.();
        if (rect) {
          const centerX = rect.left + rect.width / 2;
          const centerY = rect.top + rect.height / 2;
          const deltaX = Math.abs(centerX - window.innerWidth / 2);
          const deltaY = Math.abs(centerY - window.innerHeight / 2);
          score += (deltaX + deltaY) / 150;
        }
      }

      if (score < bestScore) {
        bestScore = score;
        bestVideo = video;
      }
    }

    return bestVideo;
  }

  /** Resolves canonical TikTok URL by walking up from the active video element. */
  function videoUrlFromActiveVideo(referenceElement) {
    const video = pickActiveVideoElement(referenceElement);
    if (!(video instanceof HTMLVideoElement)) return null;

    let node = video;
    for (let depth = 0; node && depth < 10; depth += 1) {
      if (node instanceof Element) {
        const links = [...node.querySelectorAll('a[href*="/video/"]')];
        const fromNode = pickBestVideoUrlFromLinks(links, video);
        if (fromNode) return fromNode;
      }
      node = node.parentElement;
    }

    return null;
  }

  /** Resolves direct stream/media URL from the active video element when canonical URL is unavailable. */
  function activeVideoSourceUrl(referenceElement) {
    const video = pickActiveVideoElement(referenceElement);
    if (!(video instanceof HTMLVideoElement)) return null;

    const raw = String(video.currentSrc || video.src || "").trim();
    if (!raw || /^blob:/i.test(raw)) return null;

    try {
      const parsed = new URL(raw, window.location.origin);
      parsed.hash = "";
      return parsed.toString();
    } catch {
      return null;
    }
  }

  /** Gets the latest likely TikTok media request URL from Performance API entries. */
  function latestMediaRequestUrl() {
    try {
      if (!performance?.getEntriesByType) return null;
      const entries = performance.getEntriesByType("resource");
      for (let index = entries.length - 1; index >= 0; index -= 1) {
        const name = String(entries[index]?.name || "");
        if (!/^https?:\/\//i.test(name)) continue;

        const looksLikeMedia =
          /video\/tos\//i.test(name) ||
          /mime_type=video/i.test(name) ||
          /\.mp4(\?|$)/i.test(name) ||
          /\.m3u8(\?|$)/i.test(name);
        if (!looksLikeMedia) continue;

        if (/cover|avatar|thumb|image|sprite/i.test(name)) continue;

        try {
          const parsed = new URL(name, window.location.origin);
          parsed.hash = "";
          return parsed.toString();
        } catch {
          // ignore malformed entry and continue scanning
        }
      }
    } catch {
      // ignore
    }

    return null;
  }

  /** Resolves the current video URL from visible links first, then metadata fallback. */
  function currentVideoUrlFromPage() {
    const fromActiveVideo = videoUrlFromActiveVideo(null);
    if (fromActiveVideo) return fromActiveVideo;

    const links = [...document.querySelectorAll('a[href*="/video/"]')];

    const fromLinks = pickBestVideoUrlFromLinks(links, null);
    if (fromLinks) return fromLinks;

    const current = videoUrlFromDocument();
    if (current) return current;

    const activeSource = activeVideoSourceUrl(null);
    if (activeSource) return activeSource;

    const perfMedia = latestMediaRequestUrl();
    if (perfMedia) return perfMedia;

    return null;
  }

  /** Finds the nearest ancestor scope that contains a video link near the action sidebar. */
  function findVideoScope(sidebar) {
    let node = sidebar;
    for (let depth = 0; node && depth < 8; depth += 1) {
      if (node.querySelector?.('a[href*="/video/"]')) return node;
      node = node.parentElement;
    }
    return document;
  }

  /** Resolves a download URL from a sidebar context, falling back to document metadata. */
  function resolveVideoUrl(sidebar) {
    try {
      const fromActiveVideo = videoUrlFromActiveVideo(sidebar);
      if (fromActiveVideo) return fromActiveVideo;

      const scope = findVideoScope(sidebar);
      const links = [...scope.querySelectorAll('a[href*="/video/"]')];
      const fromScope = pickBestVideoUrlFromLinks(links, sidebar);
      if (fromScope) return fromScope;

      const pageLinks = [...document.querySelectorAll('a[href*="/video/"]')];
      const fromPage = pickBestVideoUrlFromLinks(pageLinks, sidebar);
      if (fromPage) return fromPage;
    } catch {
      // ignore
    }

    const activeSource = activeVideoSourceUrl(sidebar);
    if (activeSource) return activeSource;

    const perfMedia = latestMediaRequestUrl();
    if (perfMedia) return perfMedia;

    return videoUrlFromDocument();
  }

  /** Applies visual/interaction state to a button and updates accessibility labels. */
  function setButtonState(button, state, title) {
    button.classList.toggle(BUTTON_BUSY_CLASS, state === "busy");
    button.classList.toggle(BUTTON_DONE_CLASS, state === "done");
    button.classList.toggle(BUTTON_ERROR_CLASS, state === "error");
    button.disabled = state === "busy";
    if (title) button.title = title;
    button.setAttribute("aria-label", title || "Download video");
  }

  /** Finds a native TikTok action control to mirror styling on injected buttons. */
  function findNativeActionTemplate(sidebar) {
    if (!(sidebar instanceof Element)) return null;

    const candidates = [
      ...sidebar.querySelectorAll(
        ':scope > button, :scope > a, :scope > [role="button"]',
      ),
    ];

    return (
      candidates.find(
        (node) =>
          node instanceof HTMLElement && !node.hasAttribute(BUTTON_ATTR),
      ) || null
    );
  }

  /** Adapts injected button classes to match inline vs stacked native action rows. */
  function syncButtonPresentation(button, sidebar) {
    if (
      !(button instanceof HTMLButtonElement) ||
      !(sidebar instanceof Element)
    ) {
      return;
    }

    const isInlineRow = sidebar.matches(INLINE_ROW_SELECTOR);
    button.classList.toggle(BUTTON_INLINE_CLASS, isInlineRow);

    if (!isInlineRow) return;

    const template = findNativeActionTemplate(sidebar);
    if (!(template instanceof HTMLElement)) return;

    const extraClasses = [...template.classList].filter(
      (name) =>
        ![
          BUTTON_CLASS,
          BUTTON_INLINE_CLASS,
          BUTTON_BUSY_CLASS,
          BUTTON_DONE_CLASS,
          BUTTON_ERROR_CLASS,
        ].includes(name),
    );

    button.className = [
      BUTTON_CLASS,
      BUTTON_INLINE_CLASS,
      ...extraClasses,
      button.classList.contains(BUTTON_BUSY_CLASS) ? BUTTON_BUSY_CLASS : "",
      button.classList.contains(BUTTON_DONE_CLASS) ? BUTTON_DONE_CLASS : "",
      button.classList.contains(BUTTON_ERROR_CLASS) ? BUTTON_ERROR_CLASS : "",
    ]
      .filter(Boolean)
      .join(" ");
  }

  /** Returns the sidebar container that owns a given injected button. */
  function getSidebarForButton(button) {
    if (!(button instanceof Element)) return null;
    return button.closest(SIDEBAR_SELECTOR);
  }

  /** Refreshes and stores the best available video URL for a button instance. */
  function refreshButtonUrl(button, sidebar, options = {}) {
    if (!(button instanceof HTMLButtonElement)) return null;

    const allowCached = options.allowCached !== false;

    const sourceSidebar =
      sidebar instanceof Element ? sidebar : getSidebarForButton(button);
    const url = sourceSidebar ? resolveVideoUrl(sourceSidebar) : null;
    if (url) {
      button.dataset.ytdlpUrl = url;
      return url;
    }

    const current = currentVideoUrlFromPage();
    if (current) {
      button.dataset.ytdlpUrl = current;
      return current;
    }

    return allowCached ? button.dataset.ytdlpUrl || null : null;
  }

  /** Sends the resolved URL to background download flow and reflects result state in UI. */
  async function requestDownload(url, button) {
    setButtonState(button, "busy", "Sending to app...");

    try {
      const response = await chrome.runtime.sendMessage({
        type: "localhostDownload",
        url,
        mode: "video",
      });

      if (!response || response.ok !== true) {
        throw new Error((response && response.error) || "download failed");
      }

      setButtonState(button, "done", "Sent to app");
      window.setTimeout(() => {
        if (button.isConnected) {
          setButtonState(button, "idle", "Download with app");
        }
      }, 1800);
    } catch (error) {
      console.error("ytdlp tiktok download failed", error);
      setButtonState(button, "error", "App not reachable");
      window.setTimeout(() => {
        if (button.isConnected) {
          setButtonState(button, "idle", "Download with app");
        }
      }, 2400);
    }
  }

  /** Creates a download button wired to URL refresh and localhost download requests. */
  function createButton(url) {
    const button = document.createElement("button");
    button.type = "button";
    button.className = BUTTON_CLASS;
    button.setAttribute(BUTTON_ATTR, "1");
    button.dataset.ytdlpUrl = url || "";
    button.innerHTML = downloadSvgHtml();
    setButtonState(button, "idle", "Download with app");

    button.addEventListener("click", (event) => {
      event.preventDefault();
      event.stopPropagation();
      const nextUrl = refreshButtonUrl(button, null, { allowCached: false });
      if (!nextUrl) {
        setButtonState(button, "error", "Video URL not found");
        window.setTimeout(() => {
          if (button.isConnected) {
            setButtonState(button, "idle", "Download with app");
          }
        }, 1600);
        return;
      }
      button.dataset.ytdlpUrl = nextUrl;
      void requestDownload(nextUrl, button);
    });

    return button;
  }

  /** Injects or reuses a download button in a sidebar and keeps it visually aligned. */
  function injectButtonIntoSidebar(sidebar) {
    if (!(sidebar instanceof Element)) return;

    const existingButton = sidebar.querySelector(`[${BUTTON_ATTR}]`);
    if (existingButton instanceof HTMLButtonElement) {
      refreshButtonUrl(existingButton, sidebar);
      syncButtonPresentation(existingButton, sidebar);
      if (sidebar.firstElementChild !== existingButton) {
        sidebar.insertBefore(existingButton, sidebar.firstElementChild || null);
      }
      return;
    }

    const url = resolveVideoUrl(sidebar) || "";

    const button = createButton(url);
    refreshButtonUrl(button, sidebar);
    syncButtonPresentation(button, sidebar);
    sidebar.insertBefore(button, sidebar.firstElementChild || null);
  }

  /** Returns selector sets for key engagement controls used to detect action stacks. */
  function engagementSelector(kind) {
    const selectors = {
      like: [
        '[data-e2e="like-icon"]',
        '[data-e2e="browse-like-icon"]',
        'button[aria-label*="Like"]',
        '[role="button"][aria-label*="Like"]',
      ],
      comment: [
        '[data-e2e="comment-icon"]',
        '[data-e2e="browse-comment-icon"]',
        'button[aria-label*="Comment"]',
        '[role="button"][aria-label*="Comment"]',
      ],
      share: [
        '[data-e2e="share-icon"]',
        '[data-e2e="browse-share-icon"]',
        'button[aria-label*="Share"]',
        '[role="button"][aria-label*="Share"]',
      ],
    };

    return selectors[kind].join(", ");
  }

  /** Checks whether a node looks like a TikTok action stack for a video card. */
  function isActionStackCandidate(node) {
    if (!(node instanceof Element)) return false;

    const controls = node.querySelectorAll("button, [role='button'], a");
    if (controls.length < 3 || controls.length > 16) return false;

    const hasLike = Boolean(node.querySelector(engagementSelector("like")));
    const hasComment = Boolean(
      node.querySelector(engagementSelector("comment")),
    );
    const hasShare = Boolean(node.querySelector(engagementSelector("share")));

    return hasLike && (hasComment || hasShare);
  }

  /** Locates likely action stack containers by walking up from like controls. */
  function findActionStackCandidates(root) {
    const candidates = [];
    const likeNodes = root.querySelectorAll(engagementSelector("like"));

    for (const likeNode of likeNodes) {
      let node = likeNode instanceof Element ? likeNode.closest("div") : null;
      for (let depth = 0; node && depth < 8; depth += 1) {
        if (isActionStackCandidate(node)) {
          candidates.push(node);
          break;
        }
        node = node.parentElement;
      }
    }

    return candidates;
  }

  /** Collects unique candidate sidebars/action bars where a button can be mounted. */
  function collectSidebars(root) {
    const sidebars = [];
    const seen = new Set();

    /** Push. */
    function push(node) {
      if (!(node instanceof Element)) return;
      if (seen.has(node)) return;
      seen.add(node);
      sidebars.push(node);
    }

    if (root instanceof Element && root.matches('[data-e2e="video-sidebar"]')) {
      push(root);
    }

    if (root instanceof Element && root.matches(ACTION_BAR_SELECTOR)) {
      push(root);
    }

    if (root instanceof Element || root instanceof Document) {
      for (const node of root.querySelectorAll('[data-e2e="video-sidebar"]')) {
        push(node);
      }

      for (const node of root.querySelectorAll(ACTION_BAR_SELECTOR)) {
        push(node);
      }

      for (const node of findActionStackCandidates(root)) {
        push(node);
      }
    }

    return sidebars;
  }

  /** Scores sidebar candidates so native video sidebars win over heuristic matches. */
  function sidebarPriority(sidebar) {
    if (!(sidebar instanceof Element)) return 0;
    if (sidebar.matches('[data-e2e="video-sidebar"]')) return 3;
    if (sidebar.matches(ACTION_BAR_SELECTOR)) return 2;
    return 1;
  }

  /** Deduplicates nested sidebars and keeps the highest-priority containers. */
  function selectPreferredSidebars(sidebars) {
    const ordered = [...sidebars].sort((left, right) => {
      const priorityDiff = sidebarPriority(right) - sidebarPriority(left);
      if (priorityDiff !== 0) return priorityDiff;

      if (left.contains(right)) return -1;
      if (right.contains(left)) return 1;
      return 0;
    });

    const selected = [];
    for (const sidebar of ordered) {
      const covered = selected.some(
        (existing) =>
          existing === sidebar ||
          existing.contains(sidebar) ||
          sidebar.contains(existing),
      );
      if (!covered) selected.push(sidebar);
    }

    return selected;
  }

  /** Removes injected buttons from sidebars that are no longer selected. */
  function removeButtonsFromSidebars(sidebars) {
    for (const sidebar of sidebars) {
      if (!(sidebar instanceof Element)) continue;
      for (const button of sidebar.querySelectorAll(`[${BUTTON_ATTR}]`)) {
        button.remove();
      }
    }
  }

  /** Reconciles button placement for all discovered sidebars and returns count. */
  function processSidebars() {
    const sidebars = collectSidebars(document);
    const selectedSidebars = selectPreferredSidebars(sidebars);
    const skippedSidebars = sidebars.filter(
      (sidebar) => !selectedSidebars.includes(sidebar),
    );

    removeButtonsFromSidebars(skippedSidebars);

    for (const sidebar of selectedSidebars) {
      injectButtonIntoSidebar(sidebar);
    }

    return selectedSidebars.length;
  }

  /** Returns the floating button host element when present. */
  function getFloatingHost() {
    return document.getElementById(FLOATING_HOST_ID);
  }

  /** Determines whether the current route/context represents an active video view. */
  function isVideoOpenContext() {
    const pathname = String(window.location.pathname || "");
    if (/\/video\//i.test(pathname)) return true;

    // Fallback: require an actual playable video element in view, not just profile thumbnails.
    const videos = [...document.querySelectorAll("video")];
    return videos.some((video) => {
      const rect = video.getBoundingClientRect?.();
      if (!rect) return false;
      return rect.width >= 120 && rect.height >= 120;
    });
  }

  /** Removes the floating fallback button host from the page. */
  function removeFloatingButton() {
    getFloatingHost()?.remove();
  }

  /** Ensures a floating fallback button exists when no sidebar injection target is available. */
  function ensureFloatingButton() {
    if (!isVideoOpenContext()) {
      removeFloatingButton();
      return;
    }

    const url = currentVideoUrlFromPage();
    if (!url) {
      removeFloatingButton();
      return;
    }

    let host = getFloatingHost();
    if (!host) {
      host = document.createElement("div");
      host.id = FLOATING_HOST_ID;
      document.documentElement.appendChild(host);

      const button = createButton(url);
      host.appendChild(button);
    }

    const button = host.querySelector(`[${BUTTON_ATTR}]`);
    if (button instanceof HTMLButtonElement) {
      button.dataset.ytdlpUrl = url;
    }
  }

  /** Batches DOM reconciliation work into a single animation frame. */
  function scheduleProcess() {
    if (processScheduled) return;
    processScheduled = true;

    window.requestAnimationFrame(() => {
      processScheduled = false;
      const sidebarCount = processSidebars();
      if (sidebarCount > 0) {
        removeFloatingButton();
      } else {
        ensureFloatingButton();
      }

      if (document.querySelector(`[${BUTTON_ATTR}]`)) {
        stopBootstrapPolling();
      }
    });
  }

  /** Stops short bootstrap polling once stable button injection is achieved. */
  function stopBootstrapPolling() {
    if (bootstrapTimer == null) return;
    window.clearInterval(bootstrapTimer);
    bootstrapTimer = null;
  }

  /** Starts short-lived polling to cover delayed initial DOM hydration. */
  function startBootstrapPolling() {
    if (bootstrapTimer != null) return;
    bootstrapTicks = 0;

    bootstrapTimer = window.setInterval(() => {
      bootstrapTicks += 1;
      scheduleProcess();

      if (bootstrapTicks >= BOOTSTRAP_MAX_TICKS) {
        stopBootstrapPolling();
      }
    }, BOOTSTRAP_INTERVAL_MS);
  }

  /** Observes DOM mutations and schedules re-processing when new nodes appear. */
  function observePage() {
    const observer = new MutationObserver((mutations) => {
      for (const mutation of mutations) {
        for (const node of mutation.addedNodes) {
          if (node.nodeType !== Node.ELEMENT_NODE) continue;
          scheduleProcess();
        }
      }
    });

    observer.observe(document.documentElement, {
      childList: true,
      subtree: true,
    });
  }

  /** Hooks SPA navigation APIs so button placement updates after route changes. */
  function monitorLocationChanges() {
    let lastHref = window.location.href;

    const onLocationMaybeChanged = () => {
      const nextHref = window.location.href;
      if (nextHref === lastHref) return;
      lastHref = nextHref;
      window.setTimeout(() => scheduleProcess(), 50);
    };

    const originalPushState = history.pushState;
    history.pushState = function pushStatePatched(...args) {
      const result = originalPushState.apply(this, args);
      onLocationMaybeChanged();
      return result;
    };

    const originalReplaceState = history.replaceState;
    history.replaceState = function replaceStatePatched(...args) {
      const result = originalReplaceState.apply(this, args);
      onLocationMaybeChanged();
      return result;
    };

    window.addEventListener("popstate", onLocationMaybeChanged, true);
  }

  injectStyles();
  scheduleProcess();
  startBootstrapPolling();
  observePage();
  monitorLocationChanges();

  window.addEventListener("load", scheduleProcess, true);
  window.addEventListener("pageshow", scheduleProcess, true);
  document.addEventListener(
    "visibilitychange",
    () => {
      if (!document.hidden) scheduleProcess();
    },
    true,
  );
})();
