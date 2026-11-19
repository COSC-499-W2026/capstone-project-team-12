import { api, signedIn, whenSignedOut } from "./api.js";
import { renderGame, renderSummary } from "./game.js";
import { renderClass, renderClasses } from "./teacher.js";
import { bilingualBlock, busy, formatDate, h, mount, showError, toast } from "./ui.js";

// Routes follow the spec's route table, behind a hash so one page serves them all
const ROUTES = [
  [/^\/login$/, renderLogin, "public"],
  [/^\/register$/, renderRegister, "public"],
  [/^\/home$/, renderHome, "student"],
  [/^\/join$/, renderJoin, "student"],
  [/^\/play\/new$/, renderNewGame, "student"],
  [/^\/play\/([\w-]+)\/summary$/, (id) => renderSummary(id), "student"],
  [/^\/play\/([\w-]+)$/, (id) => renderGame(id), "student"],
  [/^\/leaderboard$/, renderLeaderboard, "student"],
  [/^\/history$/, renderHistory, "student"],
  [/^\/teach\/classes$/, renderClasses, "staff"],
  [/^\/teach\/classes\/([\w-]+)$/, (id) => renderClass(id), "staff"],
];

export const DIFFICULTIES = { 1: "Basic", 2: "Advancing", 3: "Challenging" };
// The demo content has clues for difficulty 1 only
const DEMO_DIFFICULTIES = [1];
export const LANGUAGES = { ja: "Japanese", zh: "Chinese", fr: "French", de: "German", ko: "Korean", es: "Spanish" };
const CLASS_LANG_KEY = "hunt-demo-class-languages";

let profile = null;

export function go(path) {
  location.hash = `#${path}`;
}

export async function me(refresh = false) {
  if (!profile || refresh) profile = await api.get("/me/profile");
  return profile;
}

function classLanguages() {
  try {
    return JSON.parse(localStorage.getItem(CLASS_LANG_KEY) || "{}");
  } catch {
    return {};
  }
}

function rememberClassLanguage(classId, language) {
  const known = classLanguages();
  known[classId] = language;
  try {
    localStorage.setItem(CLASS_LANG_KEY, JSON.stringify(known));
  } catch {
    // not kept across reloads then
  }
}

// The language the student plays in: the current class's, else the Practice language
export function playLanguage(p) {
  return (p.current_class_id && classLanguages()[p.current_class_id]) || p.preferred_language || "ja";
}

function homeFor(p) {
  return p.role === "staff" ? "/teach/classes" : "/home";
}

function renderNav(p) {
  const nav = document.getElementById("nav");
  document.body.classList.toggle("teach", p?.role === "staff");
  if (!p) {
    nav.replaceChildren();
    return;
  }
  const links =
    p.role === "staff"
      ? [h("a", { href: "#/teach/classes" }, "Classes")]
      : [h("a", { href: "#/home" }, "Home"), h("a", { href: "#/leaderboard" }, "Leaderboard"), h("a", { href: "#/history" }, "History")];
  nav.replaceChildren(
    ...links,
    h("button", {
      onclick: async () => {
        await api.logout();
        profile = null;
        go("/login");
      },
    }, "Log out"),
  );
}

async function route() {
  const path = location.hash.slice(1) || "/";
  if (!signedIn()) {
    renderNav(null);
    if (path === "/register") return renderRegister();
    return path === "/login" ? renderLogin() : go("/login");
  }
  let p;
  try {
    p = await me();
  } catch (error) {
    showError(error);
    return;
  }
  renderNav(p);
  for (const [pattern, render, area] of ROUTES) {
    const match = path.match(pattern);
    if (!match) continue;
    if (area === "public") return go(homeFor(p));
    if (area !== (p.role === "staff" ? "staff" : "student")) return go(homeFor(p));
    return render(...match.slice(1));
  }
  go(homeFor(p));
}

whenSignedOut(() => {
  profile = null;
  go("/login");
});
window.addEventListener("hashchange", route);
route();

// ---- public pages (English: the language isn't known yet, spec Localisation)

function renderLogin() {
  const email = h("input", { type: "email", required: true, autocomplete: "username" });
  const password = h("input", { type: "password", required: true, autocomplete: "current-password" });
  const submit = h("button", { class: "btn big", type: "submit" }, "Log in");
  mount(
    h("h1", {}, "Log in"),
    h("form", {
      class: "card stack",
      onsubmit: (event) => {
        event.preventDefault();
        busy(submit, async () => {
          await api.login(email.value, password.value);
          const p = await me(true);
          go(homeFor(p));
        });
      },
    }, h("label", {}, "Email", email), h("label", {}, "Password", password), submit),
    h("p", { class: "muted" }, "No account yet? ", h("a", { href: "#/register" }, "Register")),
  );
}

function renderRegister() {
  const email = h("input", { type: "email", required: true });
  const username = h("input", { required: true, minlength: 3, maxlength: 30 });
  const password = h("input", { type: "password", required: true, autocomplete: "new-password" });
  const code = h("input", { placeholder: "Optional, e.g. K7PM-3QXD" });
  const submit = h("button", { class: "btn big", type: "submit" }, "Create account");
  mount(
    h("h1", {}, "Register"),
    h("form", {
      class: "card stack",
      onsubmit: (event) => {
        event.preventDefault();
        busy(submit, async () => {
          await api.post("/auth/register", {
            email: email.value,
            username: username.value,
            password: password.value,
            access_code: code.value.trim() || null,
          });
          await api.login(email.value, password.value);
          const p = await me(true);
          toast("Account created. A verification email was sent.");
          go(homeFor(p));
        });
      },
    },
      h("label", {}, "Email", email),
      h("label", {}, "Username", username),
      h("label", {}, "Password", password),
      h("label", {}, "Class access code", code),
      submit,
    ),
    h("p", { class: "muted" }, "Have an account? ", h("a", { href: "#/login" }, "Log in")),
  );
}

// ---- student pages

async function renderHome() {
  const p = await me(true);
  const language = playLanguage(p);
  let stats = null;
  let openGame = null;
  try {
    stats = await api.get(`/me/engagement?language=${language}`);
  } catch (error) {
    showError(error);
  }
  try {
    openGame = await api.get("/games/current");
  } catch (error) {
    if (error.status !== 404) showError(error);
  }
  const levelSpan = stats && stats.points_to_next_level !== null ? stats.total_points + stats.points_to_next_level : null;
  mount(
    h("h1", {}, `Hi, ${p.username}`),
    h("div", { class: "card" },
      h("div", { class: "row", style: "justify-content: space-between" },
        h("span", { class: "pill" }, p.current_class_id ? "Class" : "Practice"),
        h("span", { class: "muted" }, LANGUAGES[language]),
      ),
      stats &&
        h("div", { class: "stats3", style: "margin-top: 12px" },
          h("div", { class: "stat" }, h("span", { class: "muted" }, "Level"), h("b", {}, stats.level)),
          h("div", { class: "stat" }, h("span", { class: "muted" }, "Points"), h("b", {}, stats.total_points)),
          h("div", { class: "stat" }, h("span", { class: "muted" }, "Streak"), h("b", {}, `${stats.current_streak_days} days`)),
        ),
      levelSpan &&
        h("div", { style: "margin-top: 12px" },
          h("div", { class: "progress" }, h("i", { style: `width: ${Math.round((stats.total_points / levelSpan) * 100)}%` })),
          h("p", { class: "muted" }, `${stats.points_to_next_level} points to the next level`),
        ),
    ),
    openGame
      ? h("button", { class: "btn big", onclick: () => go(`/play/${openGame.session_id}`) }, "Resume game")
      : h("button", { class: "btn big", onclick: () => go("/play/new") }, "Start a game"),
    stats && challengesCard(stats.daily_challenges),
    stats && badgesCard(stats.recent_badges, "Recent badges"),
    h("div", { class: "row", style: "margin-top: 14px" },
      h("button", { class: "btn ghost", onclick: () => go("/join") }, "Join a class"),
    ),
  );
}

export function challengesCard(daily) {
  if (!daily || !daily.challenges.length) return null;
  return h("div", { class: "card" },
    h("h2", {}, "Today's challenges"),
    h("div", { class: "stack" },
      daily.challenges.map((c) =>
        h("div", {},
          h("div", { class: "row", style: "justify-content: space-between" },
            h("span", {}, challengeLabel(c.description_key), h("span", { class: "muted" }, ` +${c.reward_points}`)),
            h("span", { class: c.completed ? "pill ok" : "pill" }, c.completed ? "Done" : `${c.progress_current}/${c.progress_required}`),
          ),
          h("div", { class: "progress" }, h("i", { style: `width: ${Math.min(100, (c.progress_current / c.progress_required) * 100)}%` })),
        ),
      ),
    ),
  );
}

// complete_3_basic_locations -> "Complete 3 Basic locations"
function challengeLabel(key) {
  const match = /^complete_(\d+)_(?:(basic|advancing|challenging)_)?locations?$/.exec(key);
  if (!match) return key.replaceAll("_", " ");
  const [, count, level] = match;
  const kind = level ? `${level[0].toUpperCase()}${level.slice(1)} ` : "";
  return `Complete ${count} ${kind}location${count === "1" ? "" : "s"}`;
}

export function badgesCard(badges, title) {
  return h("div", { class: "card" },
    h("h2", {}, title),
    badges.length
      ? h("div", { class: "row" }, badges.map((b) => h("span", { class: "pill ok", title: formatDate(b.unlocked_at) }, b.achievement_key)))
      : h("p", { class: "muted" }, "No badges yet"),
  );
}

function renderJoin() {
  const code = h("input", { required: true, placeholder: "K7PM-3QXD", autocapitalize: "characters" });
  const submit = h("button", { class: "btn big", type: "submit" }, "Join");
  mount(
    h("h1", {}, "Join a class"),
    h("form", {
      class: "card stack",
      onsubmit: (event) => {
        event.preventDefault();
        busy(submit, async () => {
          const joined = await api.post("/me/classes/join", { access_code: code.value.trim() });
          rememberClassLanguage(joined.class_id, joined.language);
          await me(true);
          toast(`Joined a ${LANGUAGES[joined.language]} class`);
          go("/home");
        });
      },
    }, h("label", {}, "Access code from your teacher", code), submit),
  );
}

const PERMISSIONS_KEY = "hunt-demo-permissions-asked";

async function askPermissions() {
  // Location: a single read is enough to trigger the prompt
  await new Promise((resolve) => navigator.geolocation.getCurrentPosition(resolve, resolve, { timeout: 10000 }));
  try {
    const stream = await navigator.mediaDevices.getUserMedia({ video: { facingMode: "environment" } });
    stream.getTracks().forEach((track) => track.stop());
  } catch {
    toast("Camera access is needed for Stage 2. Allow it in your browser settings.");
  }
  try {
    localStorage.setItem(PERMISSIONS_KEY, "1");
  } catch {
    // asked again next time
  }
}

async function renderNewGame() {
  const p = await me(true);
  let asked = false;
  try {
    asked = Boolean(localStorage.getItem(PERMISSIONS_KEY));
  } catch {
    asked = false;
  }
  if (!asked) {
    const allow = h("button", { class: "btn big" }, "Allow location and camera");
    allow.addEventListener("click", () => busy(allow, async () => {
      await askPermissions();
      renderNewGame();
    }));
    mount(h("h1", {}, "Before you play"), bilingualBlock("permissions"), allow);
    return;
  }
  const difficulty = h("select", {}, DEMO_DIFFICULTIES.map((value) => h("option", { value }, DIFFICULTIES[value])));
  const mode = h("select", {},
    p.current_class_id && h("option", { value: "class" }, "My class"),
    h("option", { value: "practice" }, "Practice"),
  );
  const language = h("select", {}, Object.entries(LANGUAGES).map(([value, name]) => h("option", { value, selected: value === playLanguage(p) }, name)));
  const languageField = h("label", { hidden: Boolean(p.current_class_id) }, "Practice language", language);
  mode.addEventListener("change", () => (languageField.hidden = mode.value === "class"));
  const start = h("button", { class: "btn big", type: "submit" }, "Start");
  mount(
    h("h1", {}, "New game"),
    h("form", {
      class: "card stack",
      onsubmit: (event) => {
        event.preventDefault();
        busy(start, async () => {
          const body = { difficulty: Number(difficulty.value) };
          if (mode.value === "class") body.class_id = p.current_class_id;
          else body.language = language.value;
          try {
            const game = await api.post("/games", body);
            go(`/play/${game.session_id}`);
          } catch (error) {
            if (error.code === "SESSION_ALREADY_OPEN" && error.details[0]) {
              toast("You already have a game open. Resuming it.");
              go(`/play/${error.details[0].session_id}`);
              return;
            }
            throw error;
          }
        });
      },
    }, h("label", {}, "Mode", mode), languageField, h("label", {}, "Difficulty", difficulty), start),
  );
}

async function renderLeaderboard() {
  const p = await me(true);
  if (!p.current_class_id) {
    mount(h("h1", {}, "Leaderboard"), h("p", { class: "card muted" }, "Join a class to see its leaderboard."));
    return;
  }
  try {
    const board = await api.get(`/classes/${p.current_class_id}/leaderboard?page_size=50`);
    mount(h("h1", {}, "Leaderboard"), leaderboardTable(board.items));
  } catch (error) {
    mount(h("h1", {}, "Leaderboard"));
    showError(error);
  }
}

export function leaderboardTable(items) {
  return h("div", { class: "card table-wrap" },
    h("table", {},
      h("thead", {}, h("tr", {}, h("th", {}, "#"), h("th", {}, "Student"), h("th", {}, "Points"))),
      h("tbody", {},
        items.map((row) =>
          h("tr", { style: row.is_me ? "font-weight: 700" : null }, h("td", {}, row.rank), h("td", {}, row.username + (row.is_me ? " (you)" : "")), h("td", {}, row.total_points)),
        ),
      ),
    ),
    !items.length && h("p", { class: "muted" }, "No points yet"),
  );
}

async function renderHistory() {
  const history = await api.get("/me/games?page_size=20");
  mount(
    h("h1", {}, "Game history"),
    history.items.length
      ? history.items.map((game) =>
          h("div", { class: "card" },
            h("div", { class: "row", style: "justify-content: space-between" },
              h("b", {}, `${game.total_score} points`),
              h("span", { class: game.state === "completed" ? "pill ok" : "pill warn" }, game.end_reason || game.state),
            ),
            h("p", { class: "muted" }, `${LANGUAGES[game.language]} · ${DIFFICULTIES[game.difficulty]} · ${game.locations_completed} locations completed`),
            h("p", { class: "muted" }, formatDate(game.ended_at)),
            h("a", { href: `#/play/${game.session_id}/summary` }, "Summary"),
          ),
        )
      : h("p", { class: "card muted" }, "No finished games yet"),
  );
}

