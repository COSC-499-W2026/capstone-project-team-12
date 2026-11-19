import { api } from "./api.js";
import { DIFFICULTIES, LANGUAGES, go, leaderboardTable } from "./app.js";
import { busy, formatDate, h, mount, percent, showError, toast } from "./ui.js";

// ---- /teach/classes: the teacher's classes and a form to create one

export async function renderClasses() {
  const classes = await api.get("/teacher/classes");
  const name = h("input", { required: true, placeholder: "JAPN 101 - Fall 2026" });
  const semester = h("input", { required: true, placeholder: "2026W1", pattern: "\\d{4}(W|S)[12]" });
  const language = h("select", {}, Object.entries(LANGUAGES).map(([value, label]) => h("option", { value }, label)));
  const create = h("button", { class: "btn", type: "submit" }, "Create class");
  const created = h("div", {});
  mount(
    h("h1", {}, "My classes"),
    h("div", { class: "grid2" },
      h("div", {},
        classes.length
          ? classes.map((c) =>
              h("a", { class: "card", href: `#/teach/classes/${c.class_id}`, style: "display: block; text-decoration: none; color: inherit" },
                h("div", { class: "row", style: "justify-content: space-between" },
                  h("b", {}, c.name),
                  h("span", { class: c.archived_at ? "pill warn" : "pill" }, c.archived_at ? "Archived" : c.staff_role),
                ),
                h("p", { class: "muted" }, `${LANGUAGES[c.target_language]} · ${c.semester}`),
              ),
            )
          : h("p", { class: "card muted" }, "No classes yet"),
      ),
      h("div", {},
        h("form", {
          class: "card stack",
          onsubmit: (event) => {
            event.preventDefault();
            busy(create, async () => {
              const result = await api.post("/teacher/classes", { name: name.value.trim(), semester: semester.value.trim(), language: language.value });
              created.replaceChildren(
                h("div", { class: "card stack" },
                  h("h2", {}, `${result.name} created`),
                  h("p", { class: "muted" }, "Students join with this code"),
                  h("div", { class: "code" }, formatCode(result.access_code)),
                  h("button", { class: "btn ghost", onclick: () => go(`/teach/classes/${result.class_id}`) }, "Open class"),
                ),
              );
            });
          },
        },
          h("h2", {}, "New class"),
          h("label", {}, "Name", name),
          h("label", {}, "Semester (UBC term code)", semester),
          h("label", {}, "Target language (fixed after creation)", language),
          create,
        ),
        created,
      ),
    ),
  );
}

// Codes are stored as 8 characters and shown as K7PM-3QXD
function formatCode(code) {
  const plain = code.replace("-", "");
  return plain.length === 8 ? `${plain.slice(0, 4)}-${plain.slice(4)}` : code;
}

// ---- /teach/classes/:id: access codes, settings, leaderboard, analytics and the export

export async function renderClass(id) {
  const classes = await api.get("/teacher/classes");
  const info = classes.find((c) => c.class_id === id);
  if (!info) {
    showError({ message: "Class not found" });
    return go("/teach/classes");
  }
  const owner = info.staff_role === "owner";

  const codeArea = h("div", {});
  const newCode = h("button", { class: "btn" }, "Generate access code");
  newCode.addEventListener("click", () => busy(newCode, async () => {
    const code = await api.post(`/teacher/classes/${id}/access-codes`, {});
    const deactivate = h("button", { class: "btn ghost" }, "Deactivate code");
    deactivate.addEventListener("click", () => busy(deactivate, async () => {
      await api.patch(`/teacher/access-codes/${code.id}`);
      toast("Code deactivated");
      codeArea.replaceChildren(h("p", { class: "muted" }, `${formatCode(code.code)} no longer works for new students.`));
    }));
    codeArea.replaceChildren(
      h("div", { class: "code" }, formatCode(code.code)),
      h("p", { class: "muted" }, `Expires ${formatDate(code.expires_at)}`),
      deactivate,
    );
  }));

  const toggle = h("input", { type: "checkbox", checked: info.leaderboard_enabled, disabled: !owner });
  toggle.addEventListener("change", () => busy(null, async () => {
    await api.patch(`/teacher/classes/${id}`, { leaderboard_enabled: toggle.checked });
    toast(toggle.checked ? "Leaderboard on" : "Leaderboard off");
  }));

  const today = new Date();
  const weekAgo = new Date(today.getTime() - 7 * 24 * 60 * 60 * 1000);
  // en-CA formats a local date as YYYY-MM-DD
  const start = h("input", { type: "date", value: weekAgo.toLocaleDateString("en-CA") });
  const end = h("input", { type: "date", value: today.toLocaleDateString("en-CA") });
  const target = h("input", { type: "number", min: 1, value: 10, style: "width: 90px" });
  const exportButton = h("button", { class: "btn" }, "Export participation CSV");
  exportButton.addEventListener("click", () => busy(exportButton, () => downloadCsv(id, start.value, end.value, target.value)));

  const analyticsArea = h("div", {}, h("p", { class: "muted" }, "Loading…"));
  const studentsArea = h("div", {}, h("p", { class: "muted" }, "Loading…"));
  const boardArea = h("div", {});

  mount(
    h("p", {}, h("a", { href: "#/teach/classes" }, "← My classes")),
    h("h1", {}, info.name),
    h("p", { class: "muted" }, `${LANGUAGES[info.target_language]} · ${info.semester} · ${info.staff_role}`),
    h("div", { class: "grid2" },
      h("div", { class: "card stack" }, h("h2", {}, "Access codes"), newCode, codeArea),
      h("div", { class: "card stack" },
        h("h2", {}, "Settings"),
        h("label", { class: "row" }, toggle, "Class leaderboard"),
        h("h2", { style: "margin-top: 8px" }, "Participation export"),
        h("div", { class: "row" }, h("label", {}, "From", start), h("label", {}, "To", end), h("label", {}, "Target locations", target)),
        exportButton,
      ),
    ),
    h("div", { class: "card" }, h("h2", {}, "Class analytics"), analyticsArea),
    h("div", { class: "card" }, h("h2", {}, "Students"), studentsArea),
    h("div", {}, h("h2", {}, "Leaderboard"), boardArea),
  );

  api.get(`/teacher/classes/${id}/analytics`).then((a) => analyticsArea.replaceChildren(analyticsView(a)), (error) => analyticsArea.replaceChildren(errorLine(error)));
  api.get(`/teacher/classes/${id}/students`).then((s) => studentsArea.replaceChildren(studentsView(s)), (error) => studentsArea.replaceChildren(errorLine(error)));
  api.get(`/classes/${id}/leaderboard?page_size=50`).then((b) => boardArea.replaceChildren(leaderboardTable(b.items)), (error) => boardArea.replaceChildren(errorLine(error)));
}

function errorLine(error) {
  return h("p", { class: "muted" }, error.message || "Couldn't load this");
}

function seconds(value) {
  return value === null || value === undefined ? "—" : `${Math.round(value / 60)} min`;
}

function analyticsView(a) {
  const t = a.class_stats;
  return h("div", { class: "stack" },
    h("div", { class: "grid2" },
      h("div", { class: "stat" }, h("span", { class: "muted" }, "Locations finished"), h("b", {}, t.finished)),
      h("div", { class: "stat" }, h("span", { class: "muted" }, "Success rate"), h("b", {}, percent(t.success_rate))),
      h("div", { class: "stat" }, h("span", { class: "muted" }, "Photo attempts"), h("b", {}, t.photo_attempts)),
      h("div", { class: "stat" }, h("span", { class: "muted" }, "Average time"), h("b", {}, seconds(t.average_time_seconds))),
    ),
    h("div", { class: "table-wrap" },
      h("table", {},
        h("thead", {}, h("tr", {}, ["Location", "Finished", "Completed", "Failed", "Success", "Photos", "Avg time", "GPS overrides", "Flagged"].map((c) => h("th", {}, c)))),
        h("tbody", {},
          a.locations.map((l) =>
            h("tr", {}, [l.label, l.finished, l.completed, l.failed, percent(l.success_rate), l.photo_attempts, seconds(l.average_time_seconds), l.gps_overrides, l.flagged_results].map((v) => h("td", {}, v))),
          ),
        ),
      ),
      !a.locations.length && h("p", { class: "muted" }, "No games played yet"),
    ),
  );
}

const SUGGESTION = { suggest_easier: "Try an easier level", suggest_harder: "Try a harder level" };

function studentsView(students) {
  if (!students.length) return h("p", { class: "muted" }, "No students yet");
  return h("div", { class: "table-wrap" },
    h("table", {},
      h("thead", {}, h("tr", {}, ["Student", "Points", "Sessions", "Completed", "Failed", "Success", "Streak", "Last played", "Difficulty hints", "Flags"].map((c) => h("th", {}, c)))),
      h("tbody", {},
        students.map((s) =>
          h("tr", {},
            h("td", {}, s.username, s.is_active_enrollment ? "" : h("span", { class: "pill warn", style: "margin-left: 6px" }, "left")),
            h("td", {}, s.classroom_points),
            h("td", {}, s.sessions),
            h("td", {}, s.completed_locations),
            h("td", {}, s.failed_locations),
            h("td", {}, percent(s.success_rate)),
            h("td", {}, `${s.streak_days} d`),
            h("td", {}, formatDate(s.last_activity_at)),
            h("td", {}, s.difficulty_hints.filter((d) => d.suggestion).map((d) => h("div", {}, `${DIFFICULTIES[d.difficulty]}: ${SUGGESTION[d.suggestion] || d.suggestion}`))),
            h("td", {}, s.flags.map((f) => h("span", { class: "pill bad" }, f))),
          ),
        ),
      ),
    ),
  );
}

async function downloadCsv(id, startDate, endDate, targetLocations) {
  const query = new URLSearchParams({ start_date: startDate, end_date: endDate, target_locations: targetLocations });
  const response = await api.download(`/teacher/classes/${id}/participation.csv?${query}`);
  const blob = await response.blob();
  const name = /filename="?([^";]+)"?/.exec(response.headers.get("Content-Disposition") || "")?.[1] || "participation.csv";
  const link = h("a", { href: URL.createObjectURL(blob), download: name });
  document.body.append(link);
  link.click();
  link.remove();
  URL.revokeObjectURL(link.href);
}
