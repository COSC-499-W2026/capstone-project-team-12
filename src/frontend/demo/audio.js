// One queue for clue and feedback clips, so they never overlap (spec, Gameplay Screen Rules: Audio).
// The first clip of a location must start from a tap: mobile browsers block audio otherwise.
const player = new Audio();
player.preload = "auto";
const queue = [];
let busy = false;
let status = () => {};

// `refresh` returns a fresh signed URL when a link has expired (403), e.g. from current-location.
export function play(url, refresh) {
  if (!url) return;
  queue.push({ url, refresh, retried: false });
  if (!busy) next();
}

export function playAll(feedback) {
  for (const item of feedback || []) play(item.audio_url);
}

export function clear() {
  queue.length = 0;
  player.pause();
  busy = false;
}

export function onStatus(callback) {
  status = callback;
}

function next() {
  const item = queue.shift();
  if (!item) {
    busy = false;
    status("idle");
    return;
  }
  busy = true;
  status("playing");
  player.src = item.url;
  player.onended = next;
  player.onerror = () => failed(item);
  player.play().catch(() => failed(item));
}

async function failed(item) {
  if (item.refresh && !item.retried) {
    try {
      const url = await item.refresh();
      queue.unshift({ url, refresh: item.refresh, retried: true });
    } catch {
      status("unavailable");
    }
  } else {
    // e.g. the recording isn't uploaded yet; the game carries on without it
    status("unavailable");
  }
  next();
}
