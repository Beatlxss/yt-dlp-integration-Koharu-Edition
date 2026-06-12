(() => {
  "use strict";

  /** Checks whether it is youtube host. */
  function isYoutubeHost() {
    return /(^|\.)youtube\.com$/i.test(window.location.hostname);
  }

  /** Gets canonical youtube url. */
  function getCanonicalYoutubeUrl() {
    try {
      const current = new URL(window.location.href);

      const watchId = current.searchParams.get("v");
      if (current.pathname === "/watch" && watchId) {
        return `https://www.youtube.com/watch?v=${encodeURIComponent(watchId)}`;
      }

      const shortsMatch = current.pathname.match(/^\/shorts\/([^/?#]+)/);
      if (shortsMatch && shortsMatch[1]) {
        return `https://www.youtube.com/shorts/${encodeURIComponent(shortsMatch[1])}`;
      }

      return current.toString();
    } catch {
      return String(window.location.href || "");
    }
  }

  /** Gets youtube download target url. */
  function getYoutubeDownloadTargetUrl(selection) {
    const isPlaylist = Boolean(selection?.playlist);
    if (!isPlaylist) return getCanonicalYoutubeUrl();

    try {
      const current = new URL(window.location.href);
      const out = new URL(current.origin + current.pathname);

      const v = current.searchParams.get("v");
      const list = current.searchParams.get("list");
      const index = current.searchParams.get("index");

      if (v) out.searchParams.set("v", v);
      if (list) out.searchParams.set("list", list);
      if (index) out.searchParams.set("index", index);

      if (!list) {
        return getCanonicalYoutubeUrl();
      }

      return out.toString();
    } catch {
      return String(window.location.href || getCanonicalYoutubeUrl());
    }
  }

  /** Gets youtube download button. */
  function getYoutubeDownloadButton(event) {
    const path =
      typeof event.composedPath === "function" ? event.composedPath() : [];
    const elements = path.filter((node) => node instanceof Element);
    if (!elements.length) return null;

    const inWatchActions = elements.some((el) =>
      el.matches(
        "#top-level-buttons-computed, ytd-watch-metadata, ytd-download-button-renderer, ytd-reel-player-overlay-renderer",
      ),
    );

    if (!inWatchActions) return null;

    const downloadHost =
      elements.find((el) =>
        el.matches(
          "ytd-download-button-renderer, yt-download-button-view-model",
        ),
      ) || null;
    if (downloadHost) return downloadHost;

    const candidate =
      elements.find((el) =>
        el.matches("button, a[role='button'], ytd-button-renderer"),
      ) || null;
    if (!candidate) return null;

    const ownText = String(
      candidate.getAttribute("aria-label") ||
        candidate.getAttribute("title") ||
        candidate.textContent ||
        "",
    )
      .replace(/\s+/g, " ")
      .trim()
      .toLowerCase();

    const looksLikeDownload =
      ownText === "download" ||
      ownText.startsWith("download ") ||
      ownText.endsWith(" download");

    return looksLikeDownload ? candidate : null;
  }

  const VIDEO_HEIGHT_OPTIONS = [720, 1080, 1440, 2160];
  const AUDIO_BITRATE_OPTIONS = [128, 192, 256, 320];
  const YT_PANEL_STYLE_ID = "ytdlp-yt-quality-panel-style";
  const YTM_BUTTON_ATTR = "data-ytdlp-ytm-download-btn";
  const YT_POST_BUTTON_ATTR = "data-ytdlp-yt-post-download-btn";
  const YT_LAST_SELECTION_KEY = "ytPanelLastSelection";

  let activeYoutubeQualityPanel = null;
  let activeYoutubeQualityPanelCleanup = null;
  let activeYoutubePanelAnchor = null;
  let ytmObserver = null;
  let ytPostObserver = null;

  /** Gets storage local. */
  async function getStorageLocal(keys) {
    try {
      return await new Promise((resolve, reject) => {
        chrome.storage?.local?.get(keys, (result) => {
          const err = chrome.runtime?.lastError;
          if (err) {
            reject(err);
            return;
          }
          resolve(result || {});
        });
      });
    } catch {
      return {};
    }
  }

  /** Sets storage local. */
  async function setStorageLocal(value) {
    try {
      await new Promise((resolve, reject) => {
        chrome.storage?.local?.set(value, () => {
          const err = chrome.runtime?.lastError;
          if (err) {
            reject(err);
            return;
          }
          resolve();
        });
      });
    } catch {
      // ignore
    }
  }

  /** Loads last panel selection. */
  async function loadLastPanelSelection() {
    const data = await getStorageLocal([YT_LAST_SELECTION_KEY]);
    const raw = data?.[YT_LAST_SELECTION_KEY];
    if (!raw || typeof raw !== "object") return null;

    const mode = typeof raw.mode === "string" ? raw.mode : "";
    const qualityKey = typeof raw.qualityKey === "string" ? raw.qualityKey : "";
    if (!mode || !qualityKey) return null;

    return { mode, qualityKey };
  }

  /** Saves last panel selection. */
  async function saveLastPanelSelection(mode, qualityKey) {
    if (!mode || !qualityKey) return;
    await setStorageLocal({
      [YT_LAST_SELECTION_KEY]: {
        mode,
        qualityKey,
        savedAt: Date.now(),
      },
    });
  }

  /** Injects youtube quality panel styles. */
  function injectYoutubeQualityPanelStyles() {
    if (document.getElementById(YT_PANEL_STYLE_ID)) return;

    const style = document.createElement("style");
    style.id = YT_PANEL_STYLE_ID;
    style.textContent = `
      .ytdlp-yt-quality-panel {
        position: fixed;
        z-index: 2147483647;
        min-width: 220px;
        max-width: 280px;
        padding: 12px;
        border-radius: 10px;
        border: 1px solid rgba(255, 255, 255, 0.18);
        background: rgba(22, 22, 22, 0.96);
        box-shadow: 0 16px 30px rgba(0, 0, 0, 0.45);
        color: #f4f4f4;
        font: 13px/1.35 -apple-system, BlinkMacSystemFont, "Segoe UI", sans-serif;
        will-change: top, left;
      }

      .ytdlp-yt-quality-title {
        margin: 0 0 10px;
        font-size: 13px;
        font-weight: 700;
      }

      .ytdlp-yt-quality-row {
        display: flex;
        flex-direction: column;
        gap: 6px;
        margin-bottom: 10px;
      }

      .ytdlp-yt-quality-row label {
        font-size: 12px;
        opacity: 0.85;
      }

      .ytdlp-yt-choice-list {
        display: grid;
        grid-template-columns: repeat(2, minmax(0, 1fr));
        gap: 6px;
      }

      .ytdlp-yt-choice-item {
        height: 32px;
        border-radius: 8px;
        border: 1px solid rgba(255, 255, 255, 0.22);
        background: #202020;
        color: #fff;
        cursor: pointer;
        font: 600 12px/1 -apple-system, BlinkMacSystemFont, "Segoe UI", sans-serif;
      }

      .ytdlp-yt-choice-item:hover {
        background: #2a2a2a;
      }

      .ytdlp-yt-choice-item[data-active="1"] {
        border-color: rgba(46, 204, 113, 0.95);
        background: rgba(46, 204, 113, 0.18);
        color: #cbffe3;
      }

      .ytdlp-yt-quality-actions {
        display: grid;
        grid-template-columns: 1fr 1fr;
        gap: 8px;
      }

      .ytdlp-yt-quality-actions button {
        height: 32px;
        border-radius: 8px;
        border: 0;
        cursor: pointer;
        font: 600 12px/1 -apple-system, BlinkMacSystemFont, "Segoe UI", sans-serif;
      }

      .ytdlp-yt-quality-confirm {
        background: #2ecc71;
        color: #0e2718;
      }

      .ytdlp-yt-quality-cancel {
        background: #3a3a3a;
        color: #f4f4f4;
      }

      .ytdlp-ytm-download-btn {
        height: 36px;
        min-width: 96px;
        height: 36px;
        border: 0;
        border-radius: 18px;
        margin-left: 8px;
        padding: 0 14px;
        display: inline-flex;
        align-items: center;
        justify-content: center;
        gap: 8px;
        cursor: pointer;
        color: rgb(241, 241, 241);
        background: rgba(255, 255, 255, 0.1);
        font: 500 14px/1 -apple-system, BlinkMacSystemFont, "Segoe UI", sans-serif;
      }

      .ytdlp-ytm-download-btn:hover {
        background: rgba(255, 255, 255, 0.16);
      }

      .ytdlp-ytm-download-btn.ytdlp-ytm-download-btn-open {
        background: rgba(46, 204, 113, 0.22);
        color: rgb(216, 255, 234);
      }

      .ytdlp-ytm-download-btn svg {
        width: 18px;
        height: 18px;
        display: block;
      }

      .ytdlp-ytm-download-label {
        display: inline-block;
      }

      .ytdlp-ytpost-download-btn {
        width: 36px;
        height: 36px;
        min-width: 36px;
        border: 0;
        border-radius: 999px;
        padding: 0;
        display: inline-flex;
        align-items: center;
        justify-content: center;
        cursor: pointer;
        color: rgb(241, 241, 241);
        background: transparent;
      }

      .ytdlp-ytpost-download-btn:hover {
        background: rgba(255, 255, 255, 0.1);
      }

      .ytdlp-ytpost-download-btn svg {
        width: 20px;
        height: 20px;
        display: block;
      }

      .ytdlp-ytpost-download-btn[data-state="done"] {
        color: rgb(64, 214, 119);
      }

      .ytdlp-ytpost-download-btn[data-state="error"] {
        color: rgb(244, 74, 74);
      }
    `;

    const host = document.head || document.documentElement;
    if (host) host.appendChild(style);
  }

  /** Builds quality options. */
  function buildQualityOptions(mode) {
    if (mode === "playlist") {
      return [
        {
          key: "p-v1080",
          label: "1080p",
          mode: "video",
          videoHeight: 1080,
          playlist: true,
        },
        {
          key: "p-m320",
          label: "320 kbps",
          mode: "music",
          audioBitrate: 320,
          playlist: true,
        },
      ];
    }

    if (mode === "music") {
      return AUDIO_BITRATE_OPTIONS.map((value) => ({
        key: `m${value}`,
        label: `${value} kbps`,
        mode: "music",
        audioBitrate: value,
        playlist: false,
      }));
    }

    return VIDEO_HEIGHT_OPTIONS.map((value) => ({
      key: `v${value}`,
      label: `${value}p`,
      mode: "video",
      videoHeight: value,
      playlist: false,
    }));
  }

  /** Gets default quality key. */
  function getDefaultQualityKey(mode) {
    if (mode === "music") return "m320";
    if (mode === "playlist") return "p-v1080";
    return "v1080";
  }

  /** Ask youtube quality selection. */
  function askYoutubeQualitySelection(anchorElement, preferredMode = "video") {
    injectYoutubeQualityPanelStyles();

    const isToggleClose = Boolean(
      activeYoutubeQualityPanel &&
      activeYoutubeQualityPanelCleanup &&
      activeYoutubePanelAnchor &&
      anchorElement &&
      activeYoutubePanelAnchor === anchorElement,
    );

    if (isToggleClose) {
      activeYoutubeQualityPanelCleanup();
      activeYoutubeQualityPanelCleanup = null;
      activeYoutubePanelAnchor = null;
      return Promise.resolve(null);
    }

    if (activeYoutubeQualityPanelCleanup) {
      activeYoutubeQualityPanelCleanup();
      activeYoutubeQualityPanelCleanup = null;
      activeYoutubePanelAnchor = null;
    }

    return new Promise((resolve) => {
      const panel = document.createElement("div");
      panel.className = "ytdlp-yt-quality-panel";
      panel.setAttribute("role", "dialog");
      panel.setAttribute("aria-label", "Download quality");

      const title = document.createElement("p");
      title.className = "ytdlp-yt-quality-title";
      title.textContent = "Download quality";

      const modeRow = document.createElement("div");
      modeRow.className = "ytdlp-yt-quality-row";

      const modeLabel = document.createElement("label");
      modeLabel.textContent = "Mode";

      const modeList = document.createElement("div");
      modeList.className = "ytdlp-yt-choice-list";
      modeRow.append(modeLabel, modeList);

      const qualityRow = document.createElement("div");
      qualityRow.className = "ytdlp-yt-quality-row";

      const qualityLabel = document.createElement("label");
      qualityLabel.textContent = "Quality";

      const qualityList = document.createElement("div");
      qualityList.className = "ytdlp-yt-choice-list";
      qualityRow.append(qualityLabel, qualityList);

      const actions = document.createElement("div");
      actions.className = "ytdlp-yt-quality-actions";

      const confirmBtn = document.createElement("button");
      confirmBtn.className = "ytdlp-yt-quality-confirm";
      confirmBtn.type = "button";
      confirmBtn.textContent = "Download";

      const cancelBtn = document.createElement("button");
      cancelBtn.className = "ytdlp-yt-quality-cancel";
      cancelBtn.type = "button";
      cancelBtn.textContent = "Cancel";

      actions.append(confirmBtn, cancelBtn);
      panel.append(title, modeRow, qualityRow, actions);
      document.body.appendChild(panel);
      activeYoutubeQualityPanel = panel;
      activeYoutubePanelAnchor = anchorElement || null;

      const modeChoices = [
        { value: "video", label: "Video" },
        { value: "music", label: "Music" },
        { value: "playlist", label: "Playlist" },
      ];

      let selectedMode = preferredMode === "music" ? "music" : "video";
      let selectedQualityKey = getDefaultQualityKey(selectedMode);

      const persistCurrentSelection = () => {
        saveLastPanelSelection(selectedMode, selectedQualityKey);
      };

      const setActiveChoice = (container, value) => {
        const buttons = container.querySelectorAll(".ytdlp-yt-choice-item");
        for (const button of buttons) {
          button.setAttribute(
            "data-active",
            button.getAttribute("data-value") === String(value) ? "1" : "0",
          );
        }
      };

      const renderModeChoices = () => {
        modeList.innerHTML = "";
        for (const choice of modeChoices) {
          const button = document.createElement("button");
          button.type = "button";
          button.className = "ytdlp-yt-choice-item";
          button.textContent = choice.label;
          button.setAttribute("data-value", choice.value);
          button.addEventListener("click", () => {
            selectedMode = choice.value;
            setActiveChoice(modeList, selectedMode);
            renderQualityChoices();
            persistCurrentSelection();
          });
          modeList.appendChild(button);
        }
        setActiveChoice(modeList, selectedMode);
      };

      const renderQualityChoices = () => {
        qualityList.innerHTML = "";
        const options = buildQualityOptions(selectedMode);

        const availableKeys = options.map((option) => option.key);
        if (!availableKeys.includes(selectedQualityKey)) {
          selectedQualityKey = getDefaultQualityKey(selectedMode);
        }

        for (const option of options) {
          const button = document.createElement("button");
          button.type = "button";
          button.className = "ytdlp-yt-choice-item";
          button.textContent = option.label;
          button.setAttribute("data-value", option.key);
          button.addEventListener("click", () => {
            selectedQualityKey = option.key;
            setActiveChoice(qualityList, selectedQualityKey);
            persistCurrentSelection();
          });
          qualityList.appendChild(button);
        }

        setActiveChoice(qualityList, selectedQualityKey);
      };

      const resolveAnchor = () => {
        if (anchorElement && anchorElement.isConnected) return anchorElement;
        return document.querySelector(
          "ytd-download-button-renderer, yt-download-button-view-model",
        );
      };

      let settled = false;

      const finish = (value) => {
        if (settled) return;
        settled = true;
        cleanup();
        resolve(value);
      };

      const placePanel = () => {
        const anchor = resolveAnchor();
        if (!(anchor instanceof Element)) {
          finish(null);
          return;
        }

        const rect = anchor.getBoundingClientRect();
        const panelRect = panel.getBoundingClientRect();
        const panelWidth = Math.max(220, Math.ceil(panelRect.width || 250));
        const panelHeight = Math.max(120, Math.ceil(panelRect.height || 190));
        const gap = 8;
        const edge = 10;

        const spaceBelow = window.innerHeight - rect.bottom - edge;
        const spaceAbove = rect.top - edge;

        const left = Math.max(
          edge,
          Math.min(rect.left, window.innerWidth - panelWidth - edge),
        );

        let top = 0;
        if (spaceBelow >= panelHeight + gap) {
          top = rect.bottom + gap;
        } else if (spaceAbove >= panelHeight + gap) {
          top = rect.top - panelHeight - gap;
        } else if (spaceBelow >= spaceAbove) {
          top = window.innerHeight - panelHeight - edge;
        } else {
          top = edge;
        }

        panel.style.left = `${Math.round(left)}px`;
        panel.style.top = `${Math.round(top)}px`;
      };

      let placePanelRafId = 0;
      const schedulePlacePanel = () => {
        if (placePanelRafId) return;
        placePanelRafId = window.requestAnimationFrame(() => {
          placePanelRafId = 0;
          placePanel();
        });
      };

      const onConfirmClick = () => {
        const options = buildQualityOptions(selectedMode);
        const selected =
          options.find((option) => option.key === selectedQualityKey) ||
          options[0] ||
          null;
        if (!selected) {
          finish(null);
          return;
        }

        saveLastPanelSelection(selectedMode, selectedQualityKey);

        finish({
          mode: selected.mode,
          videoHeight: selected.videoHeight,
          audioBitrate: selected.audioBitrate,
          playlist: Boolean(selected.playlist),
        });
      };

      const onCancelClick = () => {
        persistCurrentSelection();
        finish(null);
      };

      const onOutsidePointerDown = (evt) => {
        const target = evt.target;
        if (!(target instanceof Node)) return;
        if (panel.contains(target)) return;

        if (anchorElement instanceof Node) {
          if (target === anchorElement) return;
          if (
            anchorElement instanceof Element &&
            anchorElement.contains(target)
          )
            return;
        }

        finish(null);
      };

      const onViewportChange = () => schedulePlacePanel();

      const onKeydown = (evt) => {
        if (evt.key === "Escape") {
          evt.preventDefault();
          finish(null);
          return;
        }
        if (evt.key === "Enter") {
          evt.preventDefault();
          onConfirmClick();
        }
      };

      const cleanup = () => {
        document.removeEventListener("pointerdown", onOutsidePointerDown, true);
        document.removeEventListener("keydown", onKeydown, true);
        window.removeEventListener("scroll", onViewportChange, true);
        window.removeEventListener("resize", onViewportChange, true);
        if (placePanelRafId) {
          window.cancelAnimationFrame(placePanelRafId);
          placePanelRafId = 0;
        }
        confirmBtn.removeEventListener("click", onConfirmClick);
        cancelBtn.removeEventListener("click", onCancelClick);
        if (panel.isConnected) panel.remove();
        if (activeYoutubeQualityPanel === panel)
          activeYoutubeQualityPanel = null;
        activeYoutubePanelAnchor = null;
        if (activeYoutubeQualityPanelCleanup === cleanup) {
          activeYoutubeQualityPanelCleanup = null;
        }
      };

      renderModeChoices();
      renderQualityChoices();

      loadLastPanelSelection().then((saved) => {
        if (!saved) return;

        const validMode = modeChoices.some(
          (choice) => choice.value === saved.mode,
        );
        if (!validMode) return;

        selectedMode = saved.mode;
        const options = buildQualityOptions(selectedMode);
        const validQuality = options.some(
          (option) => option.key === saved.qualityKey,
        );
        selectedQualityKey = validQuality
          ? saved.qualityKey
          : getDefaultQualityKey(selectedMode);

        renderModeChoices();
        renderQualityChoices();
      });

      confirmBtn.addEventListener("click", onConfirmClick);
      cancelBtn.addEventListener("click", onCancelClick);
      document.addEventListener("pointerdown", onOutsidePointerDown, true);
      document.addEventListener("keydown", onKeydown, true);
      window.addEventListener("scroll", onViewportChange, true);
      window.addEventListener("resize", onViewportChange, true);
      schedulePlacePanel();

      activeYoutubeQualityPanelCleanup = cleanup;
      const firstModeButton = modeList.querySelector(".ytdlp-yt-choice-item");
      if (firstModeButton instanceof HTMLElement) {
        firstModeButton.focus();
      }
    });
  }

  /** On youtube download click. */
  async function onYoutubeDownloadClick(event) {
    if (!isYoutubeHost()) return;
    if (event.defaultPrevented) return;
    if (event.button !== 0) return;

    const downloadButton = getYoutubeDownloadButton(event);
    if (!downloadButton) return;

    event.preventDefault();
    event.stopPropagation();
    event.stopImmediatePropagation();

    const selection = await askYoutubeQualitySelection(downloadButton, "video");
    if (!selection) return;
    const videoUrl = getYoutubeDownloadTargetUrl(selection);

    try {
      chrome.runtime.sendMessage(
        {
          type: "localhostDownload",
          url: videoUrl,
          mode: selection.mode,
          videoHeight: selection.videoHeight,
          audioBitrate: selection.audioBitrate,
          playlist: Boolean(selection.playlist),
        },
        (response) => {
          const runtimeError = chrome.runtime.lastError;
          if (runtimeError) {
            console.warn(
              "YouTube download redirect failed",
              runtimeError.message || runtimeError,
            );
            return;
          }

          if (!response?.ok) {
            console.warn(
              "YouTube download redirect failed",
              response?.error || "unknown error",
            );
          }
        },
      );
    } catch (err) {
      console.warn("YouTube download redirect failed", err);
    }
  }

  /** Checks whether it is youtube music host. */
  function isYoutubeMusicHost() {
    return /^music\.youtube\.com$/i.test(window.location.hostname);
  }

  /** Yt post download icon svg. */
  function ytPostDownloadIconSvg() {
    return (
      '<svg viewBox="0 0 24 24" fill="none" xmlns="http://www.w3.org/2000/svg" aria-hidden="true">' +
      '<path d="M6 21H18M12 3V16M12 16L17 11M12 16L7 11" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"/>' +
      "</svg>"
    );
  }

  /** Checks whether it is youtube post page. */
  function isYoutubePostPage() {
    return /^\/post\//i.test(String(window.location.pathname || ""));
  }

  /** Normalizes youtube post image url. */
  function normalizeYoutubePostImageUrl(rawUrl) {
    const value = String(rawUrl || "").trim();
    if (!value) return "";

    try {
      const url = new URL(value);
      if (!/googleusercontent\.com$|ggpht\.com$/i.test(url.hostname)) {
        return url.toString();
      }

      url.pathname = url.pathname.replace(/=s\d+[^/]*$/i, "");
      return `${url.origin}${url.pathname}=s0`;
    } catch {
      return value;
    }
  }

  /** Gets youtube post id. */
  function getYoutubePostId() {
    const match = String(window.location.pathname || "").match(
      /^\/post\/([^/?#]+)/i,
    );
    return match && match[1] ? match[1] : "";
  }

  /** Hash string32. */
  function hashString32(value) {
    const input = String(value || "");
    let hash = 0;
    for (let i = 0; i < input.length; i += 1) {
      hash = (hash << 5) - hash + input.charCodeAt(i);
      hash |= 0;
    }
    return Math.abs(hash).toString(36);
  }

  /** Infer image extension. */
  function inferImageExtension(urlString) {
    try {
      const url = new URL(String(urlString || ""));
      const path = String(url.pathname || "");
      const extMatch = path.match(/\.([a-z0-9]{2,5})$/i);
      if (extMatch && extMatch[1]) {
        const ext = extMatch[1].toLowerCase();
        if (
          ["jpg", "jpeg", "png", "webp", "gif", "bmp", "avif"].includes(ext)
        ) {
          return ext === "jpeg" ? "jpg" : ext;
        }
      }
    } catch {
      // ignore
    }
    return "jpg";
  }

  /** Builds youtube post image filename. */
  function buildYoutubePostImageFilename(imageUrl) {
    const postIdRaw = getYoutubePostId() || "post";
    const postId =
      postIdRaw.replace(/[^a-z0-9_-]/gi, "").slice(0, 64) || "post";
    const ext = inferImageExtension(imageUrl);
    const digest =
      hashString32(imageUrl).slice(0, 8) || Date.now().toString(36);
    return `youtube-post-${postId}-${digest}.${ext}`;
  }

  /** Gets best youtube post image url. */
  function getBestYoutubePostImageUrl() {
    const root =
      document.querySelector("ytd-backstage-post-thread-renderer") ||
      document.querySelector("ytd-backstage-post-renderer") ||
      document;

    const images = [
      ...root.querySelectorAll(
        "ytd-backstage-image-renderer img, #content ytd-post-multi-image-renderer img, #content img",
      ),
    ];
    let best = null;

    for (const img of images) {
      if (!(img instanceof HTMLImageElement)) continue;
      const src = String(img.currentSrc || img.src || "").trim();
      if (!src || src.startsWith("data:")) continue;

      const w = img.naturalWidth || img.clientWidth || 0;
      const h = img.naturalHeight || img.clientHeight || 0;
      const area = w * h;
      if (area < 50000) continue;

      if (!best || area > best.area) {
        best = { src, area };
      }
    }

    return normalizeYoutubePostImageUrl(best?.src || "");
  }

  /** Gets youtube post action host. */
  function getYoutubePostActionHost() {
    return (
      document.querySelector(
        "ytd-backstage-post-renderer ytd-comment-action-buttons-renderer #toolbar",
      ) ||
      document.querySelector(
        "ytd-backstage-post-thread-renderer ytd-comment-action-buttons-renderer #toolbar",
      ) ||
      document.querySelector(
        "ytd-backstage-post-thread-renderer ytd-comment-action-buttons-renderer #action-buttons",
      ) ||
      document.querySelector(
        "ytd-backstage-post-renderer ytd-comment-action-buttons-renderer #action-buttons",
      ) ||
      document.querySelector("ytd-comment-action-buttons-renderer #toolbar") ||
      document.querySelector(
        "ytd-comment-action-buttons-renderer #action-buttons",
      ) ||
      null
    );
  }

  /** Sets youtube post button state. */
  function setYoutubePostButtonState(button, state, title) {
    if (!(button instanceof HTMLButtonElement)) return;
    button.setAttribute("data-state", state || "idle");
    if (title) {
      button.title = title;
      button.setAttribute("aria-label", title);
    }
  }

  /** On youtube post download button click. */
  async function onYoutubePostDownloadButtonClick(event) {
    event.preventDefault();
    event.stopPropagation();

    const button =
      event.currentTarget instanceof HTMLButtonElement
        ? event.currentTarget
        : null;
    if (!button) return;

    const imageUrl = getBestYoutubePostImageUrl();
    if (!imageUrl) {
      setYoutubePostButtonState(button, "error", "Image not found");
      window.setTimeout(
        () => setYoutubePostButtonState(button, "idle", "Download image"),
        1500,
      );
      return;
    }

    setYoutubePostButtonState(button, "busy", "Downloading image...");

    try {
      const filename = buildYoutubePostImageFilename(imageUrl);
      const response = await chrome.runtime.sendMessage({
        type: "chromeDownload",
        url: imageUrl,
        filename,
      });

      if (!response?.ok) {
        throw new Error(response?.error || "download failed");
      }

      setYoutubePostButtonState(button, "done", "Image downloaded");
      window.setTimeout(
        () => setYoutubePostButtonState(button, "idle", "Download image"),
        1600,
      );
    } catch {
      setYoutubePostButtonState(button, "error", "Download failed");
      window.setTimeout(
        () => setYoutubePostButtonState(button, "idle", "Download image"),
        1800,
      );
    }
  }

  /** Ensures youtube post download button. */
  function ensureYoutubePostDownloadButton() {
    if (!isYoutubeHost() || !isYoutubePostPage()) return;

    injectYoutubeQualityPanelStyles();

    const host = getYoutubePostActionHost();
    if (!(host instanceof Element)) return;

    const existing = host.querySelector(`[${YT_POST_BUTTON_ATTR}="1"]`);
    if (existing) return;

    const button = document.createElement("button");
    button.type = "button";
    button.className = "ytdlp-ytpost-download-btn";
    button.setAttribute(YT_POST_BUTTON_ATTR, "1");
    button.innerHTML = ytPostDownloadIconSvg();
    setYoutubePostButtonState(button, "idle", "Download image");
    button.addEventListener("click", onYoutubePostDownloadButtonClick);

    const shareButton =
      host.querySelector(
        "#share-button, ytd-button-renderer#share-button, button[aria-label*='Share' i], tp-yt-paper-icon-button[aria-label*='Share' i]",
      ) || null;

    if (shareButton && shareButton.parentElement === host) {
      host.insertBefore(button, shareButton.nextSibling);
    } else {
      host.appendChild(button);
    }
  }

  /** Starts youtube post button mounting. */
  function startYoutubePostButtonMounting() {
    if (!isYoutubeHost()) return;

    ensureYoutubePostDownloadButton();

    if (ytPostObserver) return;

    ytPostObserver = new MutationObserver(() => {
      ensureYoutubePostDownloadButton();
    });

    ytPostObserver.observe(document.documentElement, {
      subtree: true,
      childList: true,
    });
  }

  /** Finds ytm button host. */
  function findYtmButtonHost() {
    return (
      document.querySelector("ytmusic-player-bar .middle-controls-buttons") ||
      document.querySelector(
        "ytmusic-player-bar #right-controls .right-controls-buttons",
      ) ||
      document.querySelector("ytmusic-player-bar #right-controls") ||
      null
    );
  }

  /** Ytm download icon svg. */
  function ytmDownloadIconSvg() {
    return (
      '<svg viewBox="0 0 24 24" fill="none" xmlns="http://www.w3.org/2000/svg" aria-hidden="true">' +
      '<path d="M6 21H18M12 3V16M12 16L17 11M12 16L7 11" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"/>' +
      "</svg>"
    );
  }

  /** Applies ytm button visual. */
  function applyYtmButtonVisual(button, isOpen) {
    if (!(button instanceof HTMLElement)) return;

    button.style.display = "inline-flex";
    button.style.alignItems = "center";
    button.style.justifyContent = "center";
    button.style.gap = "8px";
    button.style.minWidth = "96px";
    button.style.height = "36px";
    button.style.padding = "0 14px";
    button.style.marginLeft = "8px";
    button.style.border = "0";
    button.style.borderRadius = "18px";
    button.style.cursor = "pointer";
    button.style.font =
      '500 14px/1 -apple-system, BlinkMacSystemFont, "Segoe UI", sans-serif';
    button.style.color = isOpen ? "rgb(216, 255, 234)" : "rgb(241, 241, 241)";
    button.style.background = isOpen
      ? "rgba(46, 204, 113, 0.22)"
      : "rgba(255, 255, 255, 0.1)";
    button.style.boxSizing = "border-box";
    button.style.userSelect = "none";
    button.style.whiteSpace = "nowrap";
  }

  /** On ytm download button click. */
  async function onYtmDownloadButtonClick(event) {
    event.preventDefault();
    event.stopPropagation();
    event.stopImmediatePropagation();

    const trigger =
      event.currentTarget instanceof Element ? event.currentTarget : null;
    if (trigger) trigger.classList.add("ytdlp-ytm-download-btn-open");
    applyYtmButtonVisual(trigger, true);
    const selection = await askYoutubeQualitySelection(trigger, "music");
    if (trigger) trigger.classList.remove("ytdlp-ytm-download-btn-open");
    applyYtmButtonVisual(trigger, false);
    if (!selection) return;

    const videoUrl = getYoutubeDownloadTargetUrl(selection);

    try {
      chrome.runtime.sendMessage(
        {
          type: "localhostDownload",
          url: videoUrl,
          mode: selection.mode,
          videoHeight: selection.videoHeight,
          audioBitrate: selection.audioBitrate,
          playlist: Boolean(selection.playlist),
        },
        (response) => {
          const runtimeError = chrome.runtime.lastError;
          if (runtimeError) {
            console.warn(
              "YouTube Music download redirect failed",
              runtimeError.message || runtimeError,
            );
            return;
          }

          if (!response?.ok) {
            console.warn(
              "YouTube Music download redirect failed",
              response?.error || "unknown error",
            );
          }
        },
      );
    } catch (err) {
      console.warn("YouTube Music download redirect failed", err);
    }
  }

  /** Ensures ytm download button. */
  function ensureYtmDownloadButton() {
    if (!isYoutubeMusicHost()) return;

    // Inject styles before creating/mounting the button so the icon is visible immediately.
    injectYoutubeQualityPanelStyles();

    const host = findYtmButtonHost();
    if (!(host instanceof Element)) return;

    const existing = host.querySelector(`[${YTM_BUTTON_ATTR}="1"]`);
    if (existing) return;

    const button = document.createElement("button");
    button.type = "button";
    button.className = "ytdlp-ytm-download-btn";
    button.setAttribute(YTM_BUTTON_ATTR, "1");
    button.setAttribute("aria-label", "Download with yt-dlp local");
    button.title = "Download";
    button.innerHTML = `${ytmDownloadIconSvg()}<span class="ytdlp-ytm-download-label">Download</span>`;
    applyYtmButtonVisual(button, false);
    button.addEventListener("mouseenter", () => {
      if (!button.classList.contains("ytdlp-ytm-download-btn-open")) {
        button.style.background = "rgba(255, 255, 255, 0.16)";
      }
    });
    button.addEventListener("mouseleave", () => {
      if (!button.classList.contains("ytdlp-ytm-download-btn-open")) {
        button.style.background = "rgba(255, 255, 255, 0.1)";
      }
    });
    button.addEventListener("click", onYtmDownloadButtonClick);

    host.appendChild(button);
  }

  /** Starts ytm button mounting. */
  function startYtmButtonMounting() {
    if (!isYoutubeMusicHost()) return;

    injectYoutubeQualityPanelStyles();
    ensureYtmDownloadButton();

    if (ytmObserver) return;

    ytmObserver = new MutationObserver(() => {
      ensureYtmDownloadButton();
    });

    ytmObserver.observe(document.documentElement, {
      subtree: true,
      childList: true,
    });
  }

  document.addEventListener("click", onYoutubeDownloadClick, true);
  startYoutubePostButtonMounting();
  startYtmButtonMounting();
})();
