(() => {
  "use strict";

  const STYLE_ID = "ytdlp_tw_style";
  const ACTION_BTN_CLASS = "ytdlp-tw-action-btn";
  const ACTION_BTN_ON_CLASS = "ytdlp-tw-action-on";
  const ACTION_BTN_CLICKED_CLASS = "ytdlp-tw-action-clicked";
  const ACTION_BTN_DETAIL_CLASS = "ytdlp-tw-action-btn-detail";

  const LIKE_DL_FLAG = "ytdlpTwLikedDownloaded";
  const STORAGE_KEY = "twAutoLikeDownload";

  let autoLikeDownload = false;

  /** Injects styles. */
  function injectStyles() {
    if (document.getElementById(STYLE_ID)) return;
    const style = document.createElement("style");
    style.id = STYLE_ID;
    style.textContent = `
      /* Injected action-bar button (matches icon-button sizing) */
      .${ACTION_BTN_CLASS} {
        appearance: none;
        background: transparent;
        border: 0;
        padding: 0;
        margin: 0;
        width: 34px;
        height: 34px;
        border-radius: 9999px;
        display: inline-flex;
        align-items: center;
        justify-content: center;
        cursor: pointer;
        color: #2ecc71;
        opacity: 0.92;
        transition: background-color 160ms ease-in-out, opacity 160ms ease-in-out;
        align-self: center;
        line-height: 1;
      }
      .${ACTION_BTN_CLASS}:hover {
        opacity: 1;
        background-color: rgba(46, 204, 113, 0.2);
      }
      .${ACTION_BTN_CLASS}:active {
        opacity: 0.75;
      }
      .${ACTION_BTN_ON_CLASS} {
        /* Indicates that auto-download-on-like is ON (toggled by right-click) */
        color: #28a745;
      }
      .${ACTION_BTN_CLICKED_CLASS} {
        opacity: 0.75;
      }
      .${ACTION_BTN_CLASS} svg {
        width: 20px;
        height: 20px;
        display: block;
      }

      /* Full-page tweet detail view (/status/...) is slightly tighter */
      .${ACTION_BTN_DETAIL_CLASS} {
        width: 32px;
        height: 32px;
      }
      .${ACTION_BTN_DETAIL_CLASS} svg {
        width: 24px;
        height: 20px;
      }
    `;

    // `document_start` may run before <head> exists.
    const host = document.head || document.documentElement;
    if (host) host.appendChild(style);
  }

  /** Updates all toggle buttons. */
  function updateAllToggleButtons() {
    try {
      document.querySelectorAll(`.${ACTION_BTN_CLASS}`).forEach((btn) => {
        btn.classList.toggle(ACTION_BTN_ON_CLASS, autoLikeDownload);
      });
    } catch {
      // ignore
    }
  }

  /** Sets toggle state. */
  function setToggleState(enabled) {
    autoLikeDownload = Boolean(enabled);
    try {
      chrome.storage.local.set({ [STORAGE_KEY]: autoLikeDownload });
    } catch {
      // ignore
    }
    updateAllToggleButtons();
  }

  /** Loads initial toggle state. */
  async function loadInitialToggleState() {
    try {
      const got = await chrome.storage.local.get([STORAGE_KEY]);
      autoLikeDownload = Boolean(got && got[STORAGE_KEY]);
    } catch {
      autoLikeDownload = false;
    }
  }

  /** Gets tweet url from article. */
  function getTweetUrlFromArticle(article) {
    try {
      if (!article) return null;
      const links = [...article.querySelectorAll('a[href*="/status/"]')];
      const timeLink = links.find(
        (a) => a && a.querySelector && a.querySelector("time"),
      );
      const a = timeLink || links[0] || null;
      const href = a && a.getAttribute ? a.getAttribute("href") : "";
      if (!href) return null;
      return new URL(href, window.location.origin).toString();
    } catch {
      return null;
    }
  }

  /** Checks whether it has video in article. */
  function hasVideoInArticle(article) {
    try {
      if (!article) return false;
      if (article.querySelector("video")) return true;
      if (article.querySelector('[data-testid="videoPlayer"]')) return true;
      if (article.querySelector('[data-testid="videoComponent"]')) return true;

      // Many video tweets only show a thumbnail image in the timeline.
      // Examples: /ext_tw_video_thumb/..., /amplify_video_thumb/...
      const imgs = [...article.querySelectorAll("img")];
      return imgs.some((img) => {
        const src = String(img.currentSrc || img.src || "");
        const srcset = String(img.getAttribute("srcset") || "");
        const s = `${src} ${srcset}`;
        return (
          s.includes("/ext_tw_video_thumb/") ||
          s.includes("/amplify_video_thumb/") ||
          s.includes("_video_thumb")
        );
      });
    } catch {
      return false;
    }
  }

  /** Checks whether it has video in container. */
  function hasVideoInContainer(container) {
    try {
      if (!container) return false;
      if (container.querySelector("video")) return true;
      if (container.querySelector('[data-testid="videoPlayer"]')) return true;
      if (container.querySelector('[data-testid="videoComponent"]'))
        return true;

      const imgs = [...container.querySelectorAll("img")];
      return imgs.some((img) => {
        const src = String(img.currentSrc || img.src || "");
        const srcset = String(img.getAttribute("srcset") || "");
        const s = `${src} ${srcset}`;
        return (
          s.includes("/ext_tw_video_thumb/") ||
          s.includes("/amplify_video_thumb/") ||
          s.includes("_video_thumb")
        );
      });
    } catch {
      return false;
    }
  }

  /** Gets direct video url from container. */
  function getDirectVideoUrlFromContainer(container) {
    try {
      if (!container) return null;
      const v = container.querySelector("video");
      if (!v) return null;

      const direct = String(v.currentSrc || v.src || "").trim();
      if (direct && !direct.startsWith("blob:")) return direct;

      const srcEl = v.querySelector ? v.querySelector("source") : null;
      const src = srcEl
        ? String(srcEl.src || srcEl.getAttribute("src") || "").trim()
        : "";
      if (src && !src.startsWith("blob:")) return src;

      return null;
    } catch {
      return null;
    }
  }

  /** Checks whether it is gif post from container. */
  function isGifPostFromContainer(container) {
    try {
      const url = getDirectVideoUrlFromContainer(container);
      if (url && url.includes("/tweet_video/")) return true;

      // Heuristic fallback: some layouts show a GIF label overlay.
      const gifLabel =
        container && container.querySelector
          ? container.querySelector(
              '[aria-label="GIF"], span[aria-label="GIF"], div[aria-label="GIF"]',
            )
          : null;
      if (gifLabel) return true;

      return false;
    } catch {
      return false;
    }
  }

  /** Checks whether it has any image in article. */
  function hasAnyImageInArticle(article) {
    try {
      if (!article) return false;
      const imgs = [...article.querySelectorAll("img")];
      return imgs.some((img) => {
        const src = String(img.currentSrc || img.src || "");
        const srcset = String(img.getAttribute("srcset") || "");
        return (
          src.includes("twimg.com/media") || srcset.includes("twimg.com/media")
        );
      });
    } catch {
      return false;
    }
  }

  /** To orig image url. */
  function _toOrigImageUrl(raw) {
    try {
      const u = new URL(String(raw || ""));
      if (!u.hostname.endsWith("twimg.com")) return null;
      if (!u.pathname.includes("/media/")) return null;
      u.searchParams.set("name", "orig");
      return u.toString();
    } catch {
      return null;
    }
  }

  /** Media image urls. */
  function _mediaImageUrls(container) {
    try {
      const imgs = [...container.querySelectorAll("img")];
      const urls = [];
      for (const img of imgs) {
        const src = String(img.currentSrc || img.src || "");
        const orig = _toOrigImageUrl(src);
        if (orig) urls.push(orig);
      }
      // Deduplicate while preserving order.
      return [...new Set(urls)];
    } catch {
      return [];
    }
  }

  /** Gets best visible image orig url. */
  function getBestVisibleImageOrigUrl(container) {
    try {
      if (!container) return null;

      const candidates = [...container.querySelectorAll("img")]
        .map((img) => {
          const raw = String(img.currentSrc || img.src || "");
          const orig = _toOrigImageUrl(raw);
          if (!orig) return null;
          const r = img.getBoundingClientRect
            ? img.getBoundingClientRect()
            : null;
          const area = r ? Math.max(0, r.width) * Math.max(0, r.height) : 0;
          return { orig, area };
        })
        .filter(Boolean);

      if (!candidates.length) return null;

      // Prefer the largest visible image (usually the currently opened one).
      candidates.sort((a, b) => b.area - a.area);
      const best = candidates[0];
      if (best && best.area >= 80 * 80) return best.orig;

      // Fallback: first media URL.
      const urls = _mediaImageUrls(container);
      return urls.length ? urls[0] : null;
    } catch {
      return null;
    }
  }

  /** Gets photo index from location. */
  function getPhotoIndexFromLocation() {
    try {
      const m = String(location.pathname || "").match(/\/photo\/(\d+)/);
      if (!m) return null;
      const n = Number(m[1]);
      if (!Number.isFinite(n) || n <= 0) return null;
      return n - 1;
    } catch {
      return null;
    }
  }

  /** Gets first image orig url from container. */
  function getFirstImageOrigUrlFromContainer(container) {
    try {
      const urls = _mediaImageUrls(container);
      return urls.length >= 1 ? urls[0] : null;
    } catch {
      return null;
    }
  }

  /** Gets single image orig url from container. */
  function getSingleImageOrigUrlFromContainer(container) {
    try {
      const urls = _mediaImageUrls(container);
      return urls.length === 1 ? urls[0] : null;
    } catch {
      return null;
    }
  }

  /** Requests localhost download. */
  async function requestLocalhostDownload(url) {
    try {
      const resp = await chrome.runtime.sendMessage({
        type: "localhostDownload",
        url,
        mode: "video",
      });
      if (!resp || resp.ok !== true) {
        throw new Error((resp && resp.error) || "download failed");
      }
    } catch (err) {
      // Keep console noise minimal; user can inspect if needed.
      console.error("ytdlp twitter download failed", err);
    }
  }

  /** Requests chrome download. */
  async function requestChromeDownload(url) {
    try {
      const resp = await chrome.runtime.sendMessage({
        type: "chromeDownload",
        url,
      });
      if (!resp || resp.ok !== true) {
        throw new Error((resp && resp.error) || "download failed");
      }
    } catch (err) {
      console.error("ytdlp twitter image download failed", err);
    }
  }

  /** Setup like auto download. */
  function setupLikeAutoDownload() {
    document.addEventListener(
      "click",
      (e) => {
        try {
          const likeBtn =
            e.target && e.target.closest
              ? e.target.closest('[data-testid="like"]')
              : null;
          if (!likeBtn) return;
          if (!autoLikeDownload) return;

          const article = likeBtn.closest ? likeBtn.closest("article") : null;
          if (article) {
            if (article.dataset && article.dataset[LIKE_DL_FLAG] === "1")
              return;
            const imgUrl = getSingleImageOrigUrlFromContainer(article);
            if (!imgUrl) return;
            article.dataset[LIKE_DL_FLAG] = "1";
            requestChromeDownload(imgUrl);
            return;
          }

          const dialog = likeBtn.closest
            ? likeBtn.closest('div[role="dialog"]')
            : null;
          if (dialog) {
            if (dialog.dataset && dialog.dataset[LIKE_DL_FLAG] === "1") return;
            const imgUrl = getSingleImageOrigUrlFromContainer(dialog);
            if (!imgUrl) return;
            dialog.dataset[LIKE_DL_FLAG] = "1";
            requestChromeDownload(imgUrl);
          }
        } catch {
          // ignore
        }
      },
      true,
    );
  }

  /** Download svg html. */
  function _downloadSvgHtml() {
    // Based on browser-extension/download-svgrepo-com.svg
    // Use currentColor so it matches X theme.
    return (
      '<svg viewBox="0 0 24 24" fill="none" xmlns="http://www.w3.org/2000/svg" aria-hidden="true">' +
      '<path d="M6 21H18M12 3V17M12 17L17 12M12 17L7 12" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"/>' +
      "</svg>"
    );
  }

  /** Finds action group from article. */
  function findActionGroupFromArticle(article) {
    try {
      // First try to anchor off the real action buttons (most reliable across
      // timeline/profile/status pages). This avoids depending on `data-testid`
      // hydration timing for the group itself.
      const anchor = article.querySelector(
        '[data-testid="reply"], [data-testid="retweet"], [data-testid="like"], [data-testid="unlike"], [data-testid="bookmark"], [data-testid="removeBookmark"], [data-testid="share"]',
      );
      if (anchor) {
        const g = anchor.closest ? anchor.closest('div[role="group"]') : null;
        if (g) return g;

        const tb = anchor.closest
          ? anchor.closest('[data-testid="toolBar"]')
          : null;
        if (tb) {
          return tb.querySelector('div[role="group"]') || tb;
        }
      }

      const toolBar = article.querySelector('[data-testid="toolBar"]');
      if (toolBar) {
        return toolBar.querySelector('div[role="group"]') || toolBar;
      }

      const groups = [...article.querySelectorAll('div[role="group"]')];

      // X sometimes hydrates `data-testid` later; prefer a scoring approach so
      // we can pick the correct action-bar group immediately.
      const testIds = ["reply", "retweet", "like", "bookmark", "share"];
      let best = null;
      let bestScore = -1;

      for (const g of groups) {
        let score = 0;

        for (const id of testIds) {
          if (g.querySelector(`[data-testid="${id}"]`)) score += 3;
        }

        const interactiveCount = g.querySelectorAll(
          'button, a[role="link"], div[role="button"], div[tabindex]',
        ).length;
        if (interactiveCount >= 4) score += 2;
        if (interactiveCount >= 6) score += 1;

        const directChildren = g.children ? g.children.length : 0;
        if (directChildren >= 4) score += 1;

        if (score > bestScore) {
          bestScore = score;
          best = g;
        }
      }

      return best || groups[0] || null;
    } catch {
      return null;
    }
  }

  /** Tweet has any media. */
  function tweetHasAnyMedia(article) {
    return hasVideoInArticle(article) || hasAnyImageInArticle(article);
  }

  /** Context has any media. */
  function contextHasAnyMedia(contextEl, actionContainer) {
    try {
      const closestOf = (el, selector) => {
        try {
          return el && el.closest ? el.closest(selector) : null;
        } catch {
          return null;
        }
      };

      const article =
        (contextEl &&
        contextEl.tagName &&
        String(contextEl.tagName).toLowerCase() === "article"
          ? contextEl
          : null) ||
        closestOf(contextEl, "article") ||
        closestOf(actionContainer, "article") ||
        null;

      if (article) {
        return tweetHasAnyMedia(article);
      }

      const mediaRoot =
        closestOf(actionContainer, '[data-testid="tweetDetail"]') ||
        closestOf(contextEl, '[data-testid="tweetDetail"]') ||
        closestOf(actionContainer, '[data-testid="tweet"]') ||
        closestOf(contextEl, '[data-testid="tweet"]') ||
        closestOf(actionContainer, 'div[role="dialog"]') ||
        closestOf(contextEl, 'div[role="dialog"]') ||
        contextEl ||
        actionContainer;

      if (!mediaRoot) return false;

      const hasVideo = hasVideoInContainer(mediaRoot);
      if (hasVideo) return true;

      return _mediaImageUrls(mediaRoot).length > 0;
    } catch {
      return false;
    }
  }

  /** Ensures buttons. */
  function ensureButtons() {
    try {
      const isStatusPage = /\/status\/\d+/.test(String(location.pathname));

      const ACTION_TESTIDS = [
        "reply",
        "retweet",
        "like",
        "unlike",
        "bookmark",
        "removeBookmark",
        "share",
      ];

      const hasAnyTweetActions = (root) => {
        try {
          if (!root || !root.querySelector) return false;
          for (const id of ACTION_TESTIDS) {
            if (root.querySelector(`[data-testid="${id}"]`)) return true;
          }
          // Sometimes share has no data-testid on some layouts.
          if (root.querySelector('button[aria-label="Share post"]'))
            return true;
          return false;
        } catch {
          return false;
        }
      };

      const getScanRoot = () => {
        try {
          const dialogs = [...document.querySelectorAll('div[role="dialog"]')];
          // Prefer the last dialog in DOM (typically top-most overlay).
          for (let i = dialogs.length - 1; i >= 0; i -= 1) {
            const d = dialogs[i];
            if (hasAnyTweetActions(d)) return d;
          }
          return document;
        } catch {
          return document;
        }
      };

      const scanRoot = getScanRoot();

      const toolbarHasTweetActions = (toolBarEl) => {
        try {
          if (!toolBarEl) return false;
          return Boolean(
            toolBarEl.querySelector('[data-testid="reply"]') ||
            toolBarEl.querySelector('[data-testid="retweet"]') ||
            toolBarEl.querySelector('[data-testid="like"]') ||
            toolBarEl.querySelector('[data-testid="unlike"]') ||
            toolBarEl.querySelector('[data-testid="bookmark"]') ||
            toolBarEl.querySelector('[data-testid="removeBookmark"]') ||
            toolBarEl.querySelector('[data-testid="share"]'),
          );
        } catch {
          return false;
        }
      };

      const findTweetContextFromNode = (node) => {
        try {
          if (!node) return null;
          const n = node.closest
            ? node.closest(
                'article, [data-testid="tweet"], [data-testid="tweetDetail"], div[role="dialog"]',
              )
            : null;
          return n || null;
        } catch {
          return null;
        }
      };

      const _countDistinctActions = (root) => {
        try {
          if (!root || !root.querySelector) return 0;
          let count = 0;
          for (const id of ACTION_TESTIDS) {
            if (root.querySelector(`[data-testid="${id}"]`)) count += 1;
          }
          return count;
        } catch {
          return 0;
        }
      };

      const findActionContainerFromAnchor = (anchorEl) => {
        try {
          if (!anchorEl) return null;

          // Climb up to find the smallest ancestor that contains multiple action
          // buttons. This works on tweet detail pages where role/group/toolBar
          // can differ.
          let cur = anchorEl;
          let best = null;
          for (let i = 0; i < 16 && cur; i += 1) {
            const parent = cur.parentElement;
            if (!parent) break;

            const actionCount = _countDistinctActions(parent);
            const buttonCount = parent.querySelectorAll("button").length;

            // Typical action bar has at least 3 of these.
            if (actionCount >= 3 && buttonCount >= 3) {
              best = parent;
              // Keep climbing a bit to find the smallest tight container.
            }

            cur = parent;
          }
          return best;
        } catch {
          return null;
        }
      };

      const findArticleFromContext = (ctx) => {
        try {
          if (!ctx) return null;
          if (ctx.tagName && String(ctx.tagName).toLowerCase() === "article") {
            return ctx;
          }
          return ctx.querySelector ? ctx.querySelector("article") : null;
        } catch {
          return null;
        }
      };

      const isInDialog = (node) => {
        try {
          return Boolean(
            node && node.closest && node.closest('div[role="dialog"]'),
          );
        } catch {
          return false;
        }
      };

      const findDirectChildOf = (container, node) => {
        try {
          if (!container || !node) return null;
          let cur = node;
          for (let i = 0; i < 24 && cur; i += 1) {
            const p = cur.parentElement;
            if (!p) return null;
            if (p === container) return cur;
            cur = p;
          }
          return null;
        } catch {
          return null;
        }
      };

      const isTweetContextEl = (el) => {
        try {
          if (!el || !el.matches) return false;
          return el.matches(
            'article, [data-testid="tweet"], [data-testid="tweetDetail"], div[role="dialog"]',
          );
        } catch {
          return false;
        }
      };

      const getTweetContextEl = (actionContainer, contextEl) => {
        try {
          if (isTweetContextEl(contextEl)) return contextEl;
          const fromContainer =
            actionContainer && actionContainer.closest
              ? actionContainer.closest(
                  'article, [data-testid="tweet"], [data-testid="tweetDetail"], div[role="dialog"]',
                )
              : null;
          return fromContainer || null;
        } catch {
          return null;
        }
      };

      const getDedupeRootEl = (actionContainer, contextEl) => {
        try {
          const closestOf = (el, selector) => {
            try {
              return el && el.closest ? el.closest(selector) : null;
            } catch {
              return null;
            }
          };

          const dialog =
            closestOf(actionContainer, 'div[role="dialog"]') ||
            closestOf(contextEl, 'div[role="dialog"]') ||
            null;

          // IMPORTANT: de-dupe should be per tweet, not per dialog.
          // Otherwise only one button would appear for the whole modal,
          // preventing buttons on replies in the side panel.
          const tweetDetail =
            closestOf(actionContainer, '[data-testid="tweetDetail"]') ||
            closestOf(contextEl, '[data-testid="tweetDetail"]') ||
            null;
          if (tweetDetail) return tweetDetail;

          const tweet =
            closestOf(actionContainer, 'article, [data-testid="tweet"]') ||
            closestOf(contextEl, 'article, [data-testid="tweet"]') ||
            null;
          if (tweet) return tweet;

          // Fallback: if we truly can't find a tweet wrapper, fall back to the
          // dialog (or null) to avoid uncontrolled duplication.
          return dialog;
        } catch {
          return null;
        }
      };

      const insertButtonIntoActionContainer = (actionContainer, contextEl) => {
        try {
          if (!actionContainer) return;

          // Only show the button when this post/reply has actual media.
          // This check is re-run frequently, so the button appears once media
          // hydrates in dynamic X views.
          if (!contextHasAnyMedia(contextEl, actionContainer)) return;

          // De-dupe per tweet context, not just per container. X often contains
          // multiple candidate containers for the same tweet detail/modal view.
          const dedupeRoot = getDedupeRootEl(actionContainer, contextEl);
          if (dedupeRoot && dedupeRoot.querySelector(`.${ACTION_BTN_CLASS}`)) {
            return;
          }

          if (actionContainer.querySelector(`.${ACTION_BTN_CLASS}`)) return;

          const article = findArticleFromContext(contextEl);

          const closestOf = (el, selector) => {
            try {
              return el && el.closest ? el.closest(selector) : null;
            } catch {
              return null;
            }
          };

          // Media (especially multi-image carousels) often lives outside the
          // action bar container. Use the nearest tweet wrapper, not the
          // actionContainer itself.
          const mediaRoot =
            article ||
            closestOf(actionContainer, '[data-testid="tweetDetail"]') ||
            closestOf(contextEl, '[data-testid="tweetDetail"]') ||
            closestOf(actionContainer, 'article, [data-testid="tweet"]') ||
            closestOf(contextEl, 'article, [data-testid="tweet"]') ||
            contextEl ||
            actionContainer;

          const detailClassWanted =
            isStatusPage && !isInDialog(contextEl || actionContainer);

          const wireButton = (btn) => {
            btn.type = "button";
            btn.classList.add(ACTION_BTN_CLASS);
            if (detailClassWanted) btn.classList.add(ACTION_BTN_DETAIL_CLASS);
            btn.setAttribute("aria-label", "Download");
            btn.removeAttribute("aria-haspopup");
            btn.removeAttribute("aria-expanded");
            btn.removeAttribute("data-testid");
            btn.innerHTML = _downloadSvgHtml();

            btn.addEventListener(
              "click",
              (e) => {
                try {
                  e.preventDefault();
                  e.stopPropagation();
                } catch {
                  // ignore
                }

                btn.classList.add(ACTION_BTN_CLICKED_CLASS);
                setTimeout(() => {
                  try {
                    btn.classList.remove(ACTION_BTN_CLICKED_CLASS);
                  } catch {
                    // ignore
                  }
                }, 800);

                const resolvedTweetUrl =
                  (article && getTweetUrlFromArticle(article)) ||
                  (isStatusPage ? window.location.href : null) ||
                  window.location.href;

                // Scope media to the tweet that owns THIS button so we don't
                // accidentally grab images from replies/side-panel.
                const tweetScopedEl =
                  (btn.closest &&
                    btn.closest('article, [data-testid="tweet"]')) ||
                  article ||
                  mediaRoot;

                // In /photo/N route, force image-first behavior. Some layouts
                // include unrelated video elements in the same subtree, which
                // can incorrectly trigger localhost download.
                const photoIdx = getPhotoIndexFromLocation();
                if (photoIdx != null) {
                  const scopedUrls = _mediaImageUrls(tweetScopedEl);
                  const chosenScoped =
                    photoIdx >= 0 && photoIdx < scopedUrls.length
                      ? scopedUrls[photoIdx]
                      : null;
                  const visibleScoped = getBestVisibleImageOrigUrl(tweetScopedEl);

                  if (chosenScoped || visibleScoped || scopedUrls[0]) {
                    requestChromeDownload(
                      chosenScoped || visibleScoped || scopedUrls[0],
                    );
                    return;
                  }

                  // Extra fallback for media viewers where the action bar and
                  // visible image live in different containers.
                  const dialogs = [...document.querySelectorAll('div[role="dialog"]')];
                  const topDialog = dialogs.length
                    ? dialogs[dialogs.length - 1]
                    : null;
                  const viewerRoot =
                    topDialog || document.querySelector("main") || document.body;
                  const viewerUrls = viewerRoot ? _mediaImageUrls(viewerRoot) : [];
                  const chosenViewer =
                    photoIdx >= 0 && photoIdx < viewerUrls.length
                      ? viewerUrls[photoIdx]
                      : null;
                  const visibleViewer = viewerRoot
                    ? getBestVisibleImageOrigUrl(viewerRoot)
                    : null;

                  if (chosenViewer || visibleViewer || viewerUrls[0]) {
                    requestChromeDownload(
                      chosenViewer || visibleViewer || viewerUrls[0],
                    );
                    return;
                  }
                }

                const videoPresent = article
                  ? hasVideoInArticle(article)
                  : hasVideoInContainer(tweetScopedEl);

                if (videoPresent) {
                  // X "GIF" posts are typically served as looping MP4s under
                  // video.twimg.com/tweet_video/... . Download via Chrome so we
                  // don't involve the app/yt-dlp pipeline.
                  if (isGifPostFromContainer(tweetScopedEl)) {
                    const vidUrl =
                      getDirectVideoUrlFromContainer(tweetScopedEl);
                    if (vidUrl) {
                      requestChromeDownload(vidUrl);
                      return;
                    }
                  }

                  requestLocalhostDownload(resolvedTweetUrl);
                  return;
                }

                const urls = _mediaImageUrls(tweetScopedEl);
                if (urls && urls.length) {
                  const idx = photoIdx;
                  const chosen =
                    idx != null && idx >= 0 && idx < urls.length
                      ? urls[idx]
                      : null;
                  requestChromeDownload(
                    chosen ||
                      getBestVisibleImageOrigUrl(tweetScopedEl) ||
                      urls[0],
                  );
                  return;
                }

                const img = getBestVisibleImageOrigUrl(tweetScopedEl);
                if (img) {
                  requestChromeDownload(img);
                  return;
                }

                // Fallback to yt-dlp using the tweet/page URL.
                requestLocalhostDownload(resolvedTweetUrl);
              },
              true,
            );

            btn.addEventListener(
              "contextmenu",
              (e) => {
                try {
                  e.preventDefault();
                  e.stopPropagation();
                } catch {
                  // ignore
                }
                setToggleState(!autoLikeDownload);
              },
              true,
            );
          };

          // If we can locate the share action item inside this container, clone
          // its wrapper structure so our button matches full-page layout.
          const shareBtn =
            actionContainer.querySelector('[data-testid="share"]') ||
            actionContainer.querySelector('button[aria-label="Share post"]') ||
            actionContainer.querySelector('button[aria-label^="Share"]') ||
            actionContainer.querySelector('button[aria-label*="Share"]');

          if (shareBtn) {
            const shareItem = findDirectChildOf(actionContainer, shareBtn);
            if (shareItem) {
              const clone = shareItem.cloneNode(true);
              const inner = clone.querySelector("button") || clone;
              try {
                // Keep X's sizing/layout classes and replace behavior+icon.
                wireButton(inner);
              } catch {
                // ignore
              }

              actionContainer.insertBefore(clone, shareItem.nextSibling);
              return;
            }
          }

          // Fallback: simple button append.
          const btn = document.createElement("button");
          wireButton(btn);
          actionContainer.appendChild(btn);
          return;
        } catch {
          // ignore
        }
      };

      // Most robust: anchor off the real action buttons anywhere in the page
      // (works for modal overlays and full-page tweet detail with media).
      scanRoot
        .querySelectorAll(
          ACTION_TESTIDS.map((id) => `[data-testid="${id}"]`).join(","),
        )
        .forEach((anchor) => {
          try {
            const ctx = findTweetContextFromNode(anchor);
            if (!ctx && !isStatusPage) return;

            const container = findActionContainerFromAnchor(anchor);
            if (!container) return;

            // Avoid injecting into random header toolbars by requiring the
            // container to be within a tweet-like context when possible.
            if (
              ctx &&
              container.closest &&
              !container.closest(
                'article, [data-testid="tweet"], [data-testid="tweetDetail"], div[role="dialog"]',
              )
            ) {
              // If the container isn't inside the tweet context, skip.
              // (In practice this prevents injecting into the app chrome.)
              return;
            }

            insertButtonIntoActionContainer(container, ctx || container);
          } catch {
            // ignore
          }
        });

      // Status pages often render inside an overlay / different tree.
      // Inject primarily by scanning the toolbars (more stable than articles).
      scanRoot.querySelectorAll('[data-testid="toolBar"]').forEach((tb) => {
        try {
          if (!toolbarHasTweetActions(tb)) return;
          const actionContainer = tb.querySelector('div[role="group"]') || tb;
          const ctx =
            findTweetContextFromNode(tb) ||
            (tb.closest ? tb.closest("main") : null);
          insertButtonIntoActionContainer(actionContainer, ctx);
        } catch {
          // ignore
        }
      });

      // On opened tweet views (especially with image/video), the action bar can
      // exist without a `toolBar` testid. Scan the main column for action groups
      // that contain the tweet action buttons.
      const main = scanRoot.querySelector("main") || scanRoot;
      if (main) {
        main.querySelectorAll('div[role="group"]').forEach((g) => {
          try {
            if (g.querySelector(`.${ACTION_BTN_CLASS}`)) return;
            // Must look like the tweet action bar.
            const hasCoreActions = Boolean(
              g.querySelector('[data-testid="reply"]') ||
              g.querySelector('[data-testid="retweet"]') ||
              g.querySelector('[data-testid="like"]') ||
              g.querySelector('[data-testid="unlike"]') ||
              g.querySelector('[data-testid="share"]'),
            );
            if (!hasCoreActions) return;

            const ctx =
              findTweetContextFromNode(g) ||
              (g.closest ? g.closest("main") : null);
            if (!ctx) return;

            insertButtonIntoActionContainer(g, ctx);
          } catch {
            // ignore
          }
        });
      }

      scanRoot.querySelectorAll("article").forEach((article) => {
        // Do NOT gate injection on media detection: X lazily hydrates media
        // elements, which can make the button appear only after scrolling.
        // Instead, inject for any tweet-like article with a status URL.
        const tweetUrl = getTweetUrlFromArticle(article);

        const group = findActionGroupFromArticle(article);
        if (!group) return;

        // On some views (notably /status/ pages), X can render the action bar
        // before the status link becomes discoverable inside the article.
        // Allow injection there; we'll resolve the URL at click time.
        if (!tweetUrl && !isStatusPage) return;
        if (group.querySelector(`.${ACTION_BTN_CLASS}`)) return;

        insertButtonIntoActionContainer(group, article);
      });
    } catch {
      // ignore
    }
  }

  let _ensureScheduled = false;
  /** Schedules ensure buttons. */
  function scheduleEnsureButtons() {
    if (_ensureScheduled) return;
    _ensureScheduled = true;
    requestAnimationFrame(() => {
      _ensureScheduled = false;
      ensureButtons();
      updateAllToggleButtons();
    });
  }

  /** Start. */
  async function start() {
    injectStyles();
    await loadInitialToggleState();
    setupLikeAutoDownload();
    scheduleEnsureButtons();

    const observer = new MutationObserver(() => {
      scheduleEnsureButtons();
    });

    // X is a dynamic SPA; relevant elements may appear via attribute changes
    // (not just childList). Observe a small set of attributes to catch that.
    observer.observe(document.documentElement || document.body, {
      childList: true,
      subtree: true,
      attributes: true,
      attributeFilter: ["data-testid", "role", "href", "src", "srcset"],
    });

    // X is a highly dynamic SPA; some UI pieces appear without clean mutations.
    // A periodic/scroll/pointer refresh makes the button appear consistently.
    window.addEventListener(
      "scroll",
      () => {
        scheduleEnsureButtons();
      },
      { passive: true },
    );

    document.addEventListener(
      "pointerover",
      (e) => {
        try {
          const t = e && e.target;
          const article = t && t.closest ? t.closest("article") : null;
          if (!article) return;
          scheduleEnsureButtons();
        } catch {
          // ignore
        }
      },
      { passive: true, capture: true },
    );

    // Fast polling for the first few seconds after load helps cover cases
    // where the initial timeline is already rendered before observers attach.
    const startTs = Date.now();
    const fastTimer = setInterval(() => {
      scheduleEnsureButtons();
      if (Date.now() - startTs > 9000) {
        clearInterval(fastTimer);
      }
    }, 250);

    // Keep a bounded retry window instead of polling forever.
    let retryTicks = 0;
    const retryTimer = setInterval(() => {
      scheduleEnsureButtons();
      retryTicks += 1;
      if (retryTicks >= 40) {
        clearInterval(retryTimer);
      }
    }, 1500);

    // Extra retries shortly after load.
    setTimeout(() => scheduleEnsureButtons(), 100);
    setTimeout(() => scheduleEnsureButtons(), 400);
    setTimeout(() => scheduleEnsureButtons(), 1200);
    setTimeout(() => scheduleEnsureButtons(), 3000);
  }

  // Delay slightly so X's initial DOM is present.
  if (document.readyState === "loading") {
    document.addEventListener("DOMContentLoaded", () => void start(), {
      once: true,
    });
  } else {
    void start();
  }
})();

