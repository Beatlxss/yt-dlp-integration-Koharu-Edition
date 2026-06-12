const MENU_ROOT_ID = "ytdlp-download-root";
const MENU_VIDEO_ID = "ytdlp-download-video";
const MENU_MUSIC_ID = "ytdlp-download-music";

const ACTIONS = {
  "ytdlp-video-720": { mode: "video", videoHeight: 720 },
  "ytdlp-video-1080": { mode: "video", videoHeight: 1080 },
  "ytdlp-video-1440": { mode: "video", videoHeight: 1440 },
  "ytdlp-video-2160": { mode: "video", videoHeight: 2160 },

  "ytdlp-music-128": { mode: "music", audioBitrate: 128 },
  "ytdlp-music-192": { mode: "music", audioBitrate: 192 },
  "ytdlp-music-256": { mode: "music", audioBitrate: 256 },
  "ytdlp-music-320": { mode: "music", audioBitrate: 320 },
};

/** Gets youtube video id from url. */
function getYoutubeVideoIdFromUrl(urlString) {
  try {
    const url = new URL(urlString);

    const queryId = url.searchParams.get("v");
    if (queryId) return queryId;

    const shortsMatch = url.pathname.match(/^\/shorts\/([^/?]+)/);
    if (shortsMatch && shortsMatch[1]) return shortsMatch[1];

    return null;
  } catch {
    return null;
  }
}

/** Try get last video info. */
async function tryGetLastVideoInfo(tabId) {
  try {
    return await chrome.tabs.sendMessage(tabId, { type: "getLastVideo" });
  } catch {
    return null;
  }
}

/** Starts download. */
async function startDownload({
  pageUrl,
  srcUrl,
  mode,
  videoHeight,
  audioBitrate,
  playlist,
}) {
  const youtubeId = getYoutubeVideoIdFromUrl(pageUrl);

  if (youtubeId) {
    const params = new URLSearchParams();
    params.set("v", youtubeId);
    if (mode) params.set("mode", String(mode));
    if (videoHeight) params.set("h", String(videoHeight));
    if (audioBitrate) params.set("abr", String(audioBitrate));
    if (playlist) params.set("playlist", "1");

    const url = `http://localhost:8791/download?${params.toString()}`;
    const response = await fetch(url, { method: "GET" });
    if (!response.ok) throw new Error(`localhost error: ${response.status}`);
    return;
  }

  const response = await fetch("http://localhost:8791/download", {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({
      url: srcUrl || pageUrl,
      pageUrl,
      srcUrl,
      mode,
      videoHeight,
      audioBitrate,
      playlist: Boolean(playlist),
    }),
  });

  if (!response.ok) throw new Error(`localhost error: ${response.status}`);
}

/** Starts download from url. */
async function startDownloadFromUrl({
  url,
  mode,
  videoHeight,
  audioBitrate,
  playlist,
}) {
  const response = await fetch("http://localhost:8791/download", {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({
      url,
      mode,
      videoHeight,
      audioBitrate,
      playlist: Boolean(playlist),
    }),
  });

  if (!response.ok) throw new Error(`localhost error: ${response.status}`);
}

chrome.runtime.onInstalled.addListener(() => {
  chrome.contextMenus.removeAll(() => {
    const contexts = ["page", "video", "link"];

    chrome.contextMenus.create({
      id: MENU_ROOT_ID,
      title: "Download",
      contexts,
    });

    chrome.contextMenus.create({
      id: MENU_VIDEO_ID,
      parentId: MENU_ROOT_ID,
      title: "Video",
      contexts,
    });

    chrome.contextMenus.create({
      id: MENU_MUSIC_ID,
      parentId: MENU_ROOT_ID,
      title: "Music",
      contexts,
    });

    chrome.contextMenus.create({
      id: "ytdlp-video-720",
      parentId: MENU_VIDEO_ID,
      title: "720p",
      contexts,
    });
    chrome.contextMenus.create({
      id: "ytdlp-video-1080",
      parentId: MENU_VIDEO_ID,
      title: "1080p",
      contexts,
    });
    chrome.contextMenus.create({
      id: "ytdlp-video-1440",
      parentId: MENU_VIDEO_ID,
      title: "1440p",
      contexts,
    });
    chrome.contextMenus.create({
      id: "ytdlp-video-2160",
      parentId: MENU_VIDEO_ID,
      title: "4K (2160p)",
      contexts,
    });

    chrome.contextMenus.create({
      id: "ytdlp-music-128",
      parentId: MENU_MUSIC_ID,
      title: "128 kbps",
      contexts,
    });
    chrome.contextMenus.create({
      id: "ytdlp-music-192",
      parentId: MENU_MUSIC_ID,
      title: "192 kbps",
      contexts,
    });
    chrome.contextMenus.create({
      id: "ytdlp-music-256",
      parentId: MENU_MUSIC_ID,
      title: "256 kbps",
      contexts,
    });
    chrome.contextMenus.create({
      id: "ytdlp-music-320",
      parentId: MENU_MUSIC_ID,
      title: "320 kbps",
      contexts,
    });
  });
});

chrome.contextMenus.onClicked.addListener(async (info, tab) => {
  if (!tab?.id) return;

  const action = ACTIONS[String(info.menuItemId)] || null;
  if (!action) return;

  const pageUrl = info.pageUrl || tab.url || "";

  const last = await tryGetLastVideoInfo(tab.id);
  const srcUrl = (last && last.srcUrl) || info.srcUrl || info.linkUrl || "";

  try {
    await startDownload({ pageUrl, srcUrl, ...action });
  } catch (error) {
    console.error("Download failed", error);
  }
});

chrome.runtime.onMessage.addListener((message, sender, sendResponse) => {
  try {
    if (!message || typeof message !== "object") return;

    if (message.type === "chromeDownload") {
      const url = String(message.url || "").trim();
      const requestedFilename = String(message.filename || "").trim();
      if (!url) {
        sendResponse({ ok: false, error: "missing url" });
        return;
      }

      const safeFilename = requestedFilename
        .replace(/[\\/:*?"<>|]/g, "-")
        .replace(/\s+/g, " ")
        .trim()
        .slice(0, 180);

      try {
        chrome.downloads.download(
          {
            url,
            ...(safeFilename ? { filename: safeFilename } : {}),
            saveAs: false,
            conflictAction: "uniquify",
          },
          (downloadId) => {
            const err = chrome.runtime.lastError;
            if (err) {
              sendResponse({ ok: false, error: String(err.message || err) });
              return;
            }
            sendResponse({ ok: true, downloadId });
          },
        );
      } catch (err) {
        sendResponse({ ok: false, error: String(err?.message || err) });
      }

      return true;
    }

    if (message.type !== "localhostDownload") return;

    const url = String(message.url || "").trim();
    if (!url) {
      sendResponse({ ok: false, error: "missing url" });
      return;
    }

    startDownloadFromUrl({
      url,
      mode: message.mode,
      videoHeight: message.videoHeight,
      audioBitrate: message.audioBitrate,
      playlist: message.playlist,
    })
      .then(() => sendResponse({ ok: true }))
      .catch((err) =>
        sendResponse({ ok: false, error: String(err?.message || err) }),
      );

    return true; // keep the message channel open for async response
  } catch (err) {
    try {
      sendResponse({ ok: false, error: String(err?.message || err) });
    } catch {
      // ignore
    }
  }
});

