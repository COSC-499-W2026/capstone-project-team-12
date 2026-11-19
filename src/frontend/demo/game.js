import { api } from "./api.js";
import { DIFFICULTIES, LANGUAGES, badgesCard, challengesCard, go } from "./app.js";
import * as audio from "./audio.js";
import { takePhoto } from "./photo.js";
import { bilingualBlock, busy, confirmBilingual, h, mount, showError, toast } from "./ui.js";

// Pings at most every 5 seconds, and not while the page is in the background (spec, Stage 1)
const PING_INTERVAL_MS = 5000;

let session = null;

function stopSession() {
  if (!session) return;
  if (session.watchId !== null) navigator.geolocation.clearWatch(session.watchId);
  clearInterval(session.pingTimer);
  clearInterval(session.tick);
  audio.clear();
  session = null;
}

window.addEventListener("hashchange", () => {
  if (!location.hash.startsWith(`#/play/${session?.id}`) || location.hash.endsWith("/summary")) stopSession();
});

export async function renderGame(id) {
  stopSession();
  let loc;
  let status;
  try {
    [loc, status] = await Promise.all([api.get(`/games/${id}/current-location`), api.get(`/games/${id}`)]);
  } catch (error) {
    if (error.code === "SESSION_NOT_ACTIVE" || error.code === "NOT_FOUND") return go(`/play/${id}/summary`);
    throw error;
  }
  // A location skipped on read can end the game: the route then answers with the summary
  if ("engagement" in loc) return go(`/play/${id}/summary`);

  session = {
    id,
    loc,
    status,
    clueStarted: false,
    canConfirm: false,
    gpsFailed: !("geolocation" in navigator),
    position: null,
    lastSent: 0,
    overrideAt: loc.gps_override_available_at,
    watchId: null,
    pingTimer: null,
    tick: null,
  };
  draw();
  if (loc.state === "paused") showPaused();
}

// ---- drawing

const els = {};

function draw() {
  const { loc, status } = session;
  const verifying = loc.stage === "verification";
  els.dots = h("div", { class: "dots", "aria-hidden": "true" },
    status.locations.map((l) => h("i", { class: l.position === loc.position && l.status !== "completed" ? "now" : l.status })),
  );
  els.attempts = h("span", { class: "pill" }, `Attempts left: ${loc.attempts_left}`);
  els.pulse = h("div", { class: `pulse ${session.clueStarted ? "on" : ""}` });
  els.audioState = h("span", { class: "label" }, "");
  els.notice = h("div", {});
  els.photo = h("div", { id: "photo-area" });

  els.start = h("button", { class: "btn big wide", onclick: startClue }, loc.position === 1 ? "Start" : "Next location");
  els.replay = h("button", { class: "btn ghost", onclick: replayClue }, "Replay clue");
  els.here = h("button", { class: "btn wide", disabled: true, onclick: () => confirmArrival(null) }, "I'm here");
  els.noGps = h("button", { class: "btn ghost wide", hidden: true, onclick: () => confirmArrival("gps_unavailable") }, "Continue without GPS");
  els.takePhoto = h("button", { class: "btn big wide", onclick: openCamera }, "Take photo");
  els.report = h("button", { class: "btn ghost small", onclick: openReport }, "Report audio problem");

  const stageControls = verifying
    ? [els.takePhoto, els.replay, els.report]
    : session.clueStarted
      ? [els.here, els.noGps, els.replay, els.report]
      : [els.start];

  audio.onStatus((state) => {
    els.pulse.classList.toggle("on", state === "playing" || (session?.clueStarted ?? false));
    els.audioState.textContent = state === "unavailable" ? "Audio unavailable" : "";
  });

  mount(
    h("div", { class: "game-head" },
      els.dots,
      h("span", { class: "muted" }, `${LANGUAGES[status.language]} · ${DIFFICULTIES[status.difficulty]}`),
    ),
    h("div", { class: "stage" },
      h("span", { class: "label" }, verifying ? "Stage 2 · Find the sign" : "Stage 1 · Listen and walk"),
      els.pulse,
      verifying ? els.attempts : h("span", { class: "label" }, `Location ${loc.position} of ${status.locations.length}`),
      els.audioState,
    ),
    els.notice,
    els.photo,
    h("div", { class: "controls" }, ...stageControls),
    h("div", { class: "controls three", style: "margin-top: 18px" },
      h("button", { class: "btn ghost", onclick: pauseGame }, "Pause"),
      h("button", { class: "btn ghost", onclick: resetGame }, "Reset"),
      h("button", { class: "btn ghost", onclick: quitGame }, "Quit"),
    ),
  );

  if (!verifying && session.clueStarted) startTracking();
}

// ---- Stage 1

function clueUrl() {
  return session.loc.clue_audio_url;
}

async function freshClueUrl() {
  const loc = await api.get(`/games/${session.id}/current-location`);
  session.loc = loc;
  return loc.clue_audio_url;
}

// The clue starts from a tap, so mobile browsers allow the audio
function startClue() {
  session.clueStarted = true;
  audio.play(clueUrl(), freshClueUrl, "clue");
  draw();
}

function replayClue() {
  audio.play(clueUrl(), freshClueUrl, "clue");
}

// Report audio problem (spec, Audio): the dialog as specified, always in English. The backend
// has no report endpoint yet, so the demo says so instead of sending anything.
const REPORT_REASONS = ["Can't hear it clearly", "Wrong content", "Doesn't play", "Other"];

function openReport() {
  const clip = audio.lastPlayed() === "feedback" ? "the last feedback phrase" : "the current clue";
  const note = h("textarea", { rows: 3, maxlength: 500, placeholder: "Optional note" });
  const dialog = h("dialog", { class: "report" },
    h("form", { method: "dialog", class: "stack" },
      h("h2", {}, "Report audio problem"),
      h("p", { class: "muted" }, `About ${clip}.`),
      h("fieldset", { class: "stack" },
        REPORT_REASONS.map((reason, i) => h("label", { class: "row" }, h("input", { type: "radio", name: "reason", value: reason, required: true, checked: i === 0 }), reason)),
      ),
      note,
      h("div", { class: "row" },
        h("button", { class: "btn ghost", value: "cancel", formnovalidate: true }, "Cancel"),
        h("button", { class: "btn", value: "send" }, "Send"),
      ),
    ),
  );
  dialog.addEventListener("close", () => {
    if (dialog.returnValue === "send") toast("Thanks. Reports aren't sent in this demo yet.");
    dialog.remove();
  });
  document.body.append(dialog);
  dialog.showModal();
}

function startTracking() {
  if (session.watchId !== null || session.loc.stage !== "navigation") return;
  if (!session.gpsFailed) {
    session.watchId = navigator.geolocation.watchPosition(
      (pos) => {
        session.position = pos.coords;
        session.gpsFailed = false;
      },
      () => {
        session.gpsFailed = true;
        updateOverride();
      },
      { enableHighAccuracy: true, maximumAge: 2000 },
    );
  }
  session.pingTimer = setInterval(sendPing, PING_INTERVAL_MS);
  session.tick = setInterval(updateOverride, 1000);
  updateOverride();
}

async function sendPing() {
  const s = session;
  if (!s || !s.position || document.hidden) return;
  const { latitude, longitude, accuracy } = s.position;
  try {
    const result = await api.post(`/games/${s.id}/navigation/ping`, { lat: latitude, lng: longitude, accuracy_m: accuracy });
    if (session !== s) return;
    // Guidance is voice only: the trend is never drawn on screen
    if (result.speak) audio.play(result.feedback.audio_url);
    s.canConfirm = result.can_confirm;
    s.overrideAt = result.gps_override_available_at;
    els.here.disabled = !s.canConfirm;
    updateOverride();
  } catch (error) {
    if (error.code === "RATE_LIMITED") return;
    showError(error);
    if (["SESSION_NOT_ACTIVE", "INVALID_STAGE", "STATE_CHANGED"].includes(error.code)) renderGame(s.id);
  }
}

// "Continue without GPS" appears once the server allows it; the time can move, so compare with now
function updateOverride() {
  if (!session || session.loc.stage !== "navigation") return;
  const at = session.overrideAt ? Date.parse(session.overrideAt) : null;
  const ready = at !== null && Date.now() >= at;
  els.noGps.hidden = !ready || session.canConfirm;
  if (session.gpsFailed && !ready) {
    const seconds = at === null ? null : Math.max(0, Math.ceil((at - Date.now()) / 1000));
    els.notice.replaceChildren(
      bilingualBlock("gpsUnavailable"),
      seconds !== null ? h("p", { class: "muted" }, `Available in ${Math.floor(seconds / 60)}:${String(seconds % 60).padStart(2, "0")}`) : "",
    );
  } else {
    els.notice.replaceChildren();
  }
}

async function confirmArrival(overrideReason) {
  const button = overrideReason ? els.noGps : els.here;
  await busy(button, async () => {
    const body = { position: session.loc.position };
    if (overrideReason) body.override_reason = overrideReason;
    const arrived = await api.post(`/games/${session.id}/navigation/confirm-arrival`, body);
    audio.playAll(arrived.feedback);
    stopTracking();
    session.loc = arrived;
    draw();
  });
}

function stopTracking() {
  if (session.watchId !== null) navigator.geolocation.clearWatch(session.watchId);
  clearInterval(session.pingTimer);
  clearInterval(session.tick);
  session.watchId = null;
}

// ---- Stage 2: the photo module (Eddy) owns the camera, the upload and the result display

async function openCamera() {
  const s = session;
  await busy(els.takePhoto, async () => {
    const result = await takePhoto({ gameId: s.id, api, audio, container: els.photo, attemptsLeft: s.loc.attempts_left });
    if (session !== s) return;
    els.photo.replaceChildren();
    toast(result.location_result.status === "completed" ? `Location complete: +${result.location_result.points_awarded} points` : "Location failed");
    if (result.summary) return go(`/play/${s.id}/summary`);
    // the location is done: the next one starts from a tap on "Next location"
    [s.status, s.loc] = await Promise.all([api.get(`/games/${s.id}`), api.get(`/games/${s.id}/current-location`)]);
    s.clueStarted = false;
    s.canConfirm = false;
    s.overrideAt = s.loc.gps_override_available_at;
    draw();
  });
}

// ---- game controls

async function pauseGame(event) {
  await busy(event.currentTarget, async () => {
    await api.post(`/games/${session.id}/pause`);
    stopTracking();
    audio.clear();
    showPaused();
  });
}

function showPaused() {
  const resume = h("button", { class: "btn big" }, "Resume");
  const overlay = h("div", { class: "overlay" },
    h("div", { class: "stack" }, bilingualBlock("paused"), resume, h("button", { class: "btn ghost", onclick: () => go("/home") }, "Home")),
  );
  resume.addEventListener("click", () => busy(resume, async () => {
    await api.post(`/games/${session.id}/resume`);
    overlay.remove();
    renderGame(session.id);
  }));
  document.body.append(overlay);
  window.addEventListener("hashchange", () => overlay.remove(), { once: true });
}

async function resetGame(event) {
  if (!(await confirmBilingual("confirmReset"))) return;
  await busy(event.currentTarget, async () => {
    const game = await api.post(`/games/${session.id}/reset`);
    go(`/play/${game.session_id}`);
  });
}

async function quitGame(event) {
  if (!(await confirmBilingual("confirmQuit"))) return;
  await busy(event.currentTarget, async () => {
    const id = session.id;
    await api.post(`/games/${id}/quit`);
    go(`/play/${id}/summary`);
  });
}

// ---- reward screen (text allowed, spec Frontend Design)

const STATUS_PILL = { completed: "pill ok", failed: "pill bad", skipped: "pill warn" };

export async function renderSummary(id) {
  stopSession();
  let summary;
  try {
    summary = await api.get(`/games/${id}/summary`);
  } catch (error) {
    showError(error);
    return go("/home");
  }
  let stats = null;
  try {
    stats = await api.get(`/me/engagement?language=${summary.language}`);
  } catch {
    stats = null;
  }
  const e = summary.engagement;
  mount(
    h("h1", {}, summary.state === "completed" ? "Game complete" : "Game over"),
    h("div", { class: "card" },
      h("div", { class: "grid2" },
        h("div", { class: "stat" }, h("span", { class: "muted" }, "Points this game"), h("b", {}, summary.total_score)),
        h("div", { class: "stat" }, h("span", { class: "muted" }, "Level"), h("b", {}, e.level, e.level_up ? h("span", { class: "pill ok", style: "margin-left: 8px" }, "Level up!") : "")),
        stats && h("div", { class: "stat" }, h("span", { class: "muted" }, "Total points"), h("b", {}, stats.total_points)),
        stats && h("div", { class: "stat" }, h("span", { class: "muted" }, "Streak"), h("b", {}, `${stats.current_streak_days} days`)),
      ),
    ),
    h("div", { class: "card" },
      h("h2", {}, "Locations"),
      h("div", { class: "stack" },
        summary.locations.map((l) =>
          h("div", { class: "row", style: "justify-content: space-between" },
            h("span", {}, `Location ${l.position}`),
            h("span", {}, h("span", { class: STATUS_PILL[l.status] || "pill" }, l.status), ` ${l.points_awarded} pts`),
          ),
        ),
      ),
    ),
    badgesCard(e.new_badges, "New badges"),
    stats && challengesCard(stats.daily_challenges),
    h("div", { class: "row" },
      h("button", { class: "btn", onclick: () => go("/play/new") }, "Play again"),
      h("button", { class: "btn ghost", onclick: () => go("/home") }, "Home"),
      h("button", { class: "btn ghost", onclick: () => go("/leaderboard") }, "Leaderboard"),
    ),
  );
}
