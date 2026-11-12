const app = document.querySelector("#app");
const accountArea = document.querySelector("#account-area");
const toast = document.querySelector("#toast");
const tokenKey = "learnhub_access_token";

const state = {
  user: null,
  authMode: "login",
  services: [],
  courses: [],
  courseQuery: "",
  courseCategory: "all",
};

const services = [
  { name: "User service", key: "user-service", detail: "Accounts and sign-in", path: "/account" },
  { name: "Course service", key: "course-service", detail: "Course catalog", path: "/courses" },
  { name: "Enrollment service", key: "enrollment-service", detail: "Your course enrolments", path: "/enrollments" },
  { name: "Progress service", key: "progress-service", detail: "Lesson completion", path: "/progress" },
  { name: "Notification service", key: "notification-service", detail: "Learning activity", path: "/notifications" },
];

function escapeHtml(value) {
  return String(value ?? "").replace(/[&<>"']/g, (character) => ({
    "&": "&amp;",
    "<": "&lt;",
    ">": "&gt;",
    '"': "&quot;",
    "'": "&#39;",
  })[character]);
}

function getToken() {
  return localStorage.getItem(tokenKey);
}

function setToast(message, kind = "success") {
  toast.textContent = message;
  toast.dataset.kind = kind;
  toast.classList.add("visible");
  clearTimeout(setToast.timeout);
  setToast.timeout = setTimeout(() => toast.classList.remove("visible"), 3500);
}

async function request(path, options = {}) {
  const headers = new Headers(options.headers || {});
  const token = getToken();
  if (token) headers.set("Authorization", `Bearer ${token}`);
  if (options.body && !headers.has("Content-Type")) headers.set("Content-Type", "application/json");

  const response = await fetch(path, { ...options, headers });
  const contentType = response.headers.get("content-type") || "";
  const body = contentType.includes("application/json")
    ? await response.json()
    : await response.text();

  if (!response.ok) {
    const message = typeof body === "object" && body !== null
      ? body.detail || body.message || `Request failed (${response.status})`
      : body || `Request failed (${response.status})`;
    throw new Error(message);
  }
  return body;
}

function navigate(path) {
  if (window.location.pathname !== path) history.pushState({}, "", path);
  renderPage();
}

function renderAccountArea() {
  if (state.user) {
    accountArea.innerHTML = `
      <a href="/account" data-link class="identity" title="${escapeHtml(state.user.email)}">${escapeHtml(state.user.email)}</a>
      <button class="button secondary small-button" type="button" data-action="logout">Sign out</button>
    `;
  } else {
    accountArea.innerHTML = `<a href="/account" data-link>Sign in</a>`;
  }
}

function updateNavigation() {
  const path = window.location.pathname.replace(/\/+$/, "") || "/";
  document.querySelectorAll("[data-nav]").forEach((link) => {
    link.classList.toggle("active", link.dataset.nav === path);
  });
  renderAccountArea();
}

function heading(eyebrow, title, description) {
  return `
    <div class="eyebrow">${escapeHtml(eyebrow)}</div>
    <h1>${escapeHtml(title)}</h1>
    <p class="lead">${escapeHtml(description)}</p>
  `;
}

function emptyState(title, message, actionPath, actionText) {
  return `
    <section class="empty-state">
      <h2>${escapeHtml(title)}</h2>
      <p>${escapeHtml(message)}</p>
      ${actionPath ? `<a class="button" href="${escapeHtml(actionPath)}" data-link>${escapeHtml(actionText)}</a>` : ""}
    </section>
  `;
}

function errorPanel(error) {
  return `<div class="notice error" role="alert">${escapeHtml(error.message || "Something went wrong.")}</div>`;
}

async function getServiceStatuses() {
  state.services = await Promise.all(services.map(async (service) => {
    try {
      await request(`/service-health/${service.key}/readyz`);
      return { ...service, online: true };
    } catch (error) {
      return { ...service, online: false, error: error.message };
    }
  }));
}

async function renderHome() {
  let courses = [];
  let catalogError = "";
  try {
    courses = await request("/api/courses");
  } catch (error) {
    catalogError = error.message || "Course catalog is unavailable.";
  }
  const featuredCourses = [
    ...courses.filter((course) => course.category && course.category !== "general"),
    ...courses.filter((course) => !course.category || course.category === "general"),
  ].slice(0, 3);
  app.innerHTML = `
    <section class="hero">
      <div class="eyebrow">LearnHub · online learning</div>
      <h1>Make room for what you want to learn.</h1>
      <p>Build practical skills with expert-led courses. Learn at your own pace and see every milestone add up.</p>
      <a class="button" href="/courses" data-link>Explore courses</a>
      <span class="hero-note">A little progress, every day.</span>
    </section>
    ${catalogError ? `<div class="notice error catalog-warning" role="alert">Course catalog is unavailable: ${escapeHtml(catalogError)}</div>` : ""}
    ${featuredCourses.length ? `
      <div class="section-heading">
        <div><h2>Picked for your next chapter</h2><p class="muted small">A few courses from the LearnHub catalog.</p></div>
        <a class="text-link" href="/courses" data-link>View all courses →</a>
      </div>
      <section class="grid">${featuredCourses.map(courseCard).join("")}</section>
    ` : ""}
    <div class="section-heading">
      <div><h2>Service status</h2><p class="muted small">Live readiness checks for the LearnHub services.</p></div>
      <button class="button secondary small-button" type="button" data-action="refresh-status">Refresh</button>
    </div>
    <section id="service-status" class="grid" aria-live="polite">
      ${state.services.map((service) => `
        <a class="card service-card" href="${service.path}" data-link>
          <span class="status-dot ${service.online ? "online" : "offline"}"></span>
          <span><h3>${escapeHtml(service.name)}</h3><p>${service.online ? "Online · " + escapeHtml(service.detail) : "Unavailable · " + escapeHtml(service.detail)}</p></span>
        </a>
      `).join("")}
    </section>
    <div class="section-heading"><div><h2>Start learning</h2><p class="muted small">Browse the catalog or continue where you left off.</p></div></div>
    <div class="actions">
      <a class="button" href="/courses" data-link>Browse courses</a>
      <a class="button secondary" href="/enrollments" data-link>My learning</a>
      ${state.user ? "" : '<a class="button secondary" href="/account" data-link>Create an account</a>'}
    </div>
  `;
}

function courseCard(course) {
  const id = escapeHtml(course._id);
  const moduleCount = Array.isArray(course.modules) ? course.modules.length : 0;
  const modules = Array.isArray(course.modules) ? course.modules : [];
  const lessonCount = Array.isArray(course.modules)
    ? modules.reduce((count, module) => count + (Array.isArray(module.lessons) ? module.lessons.length : 0), 0)
    : 0;
  return `
    <article class="card course-card">
      <div class="course-cover"><span>${escapeHtml((course.category || "learn").slice(0, 1).toUpperCase())}</span><span class="tag">${escapeHtml(course.category || "general")}</span></div>
      <h3>${escapeHtml(course.title)}</h3>
      <p>${escapeHtml(course.description || "No course description yet.")}</p>
      ${modules.length ? `
        <details class="course-outline">
          <summary>Course outline</summary>
          <ul>${modules.map((module) => `
            <li><strong>${escapeHtml(module.title || "Module")}</strong>
              ${Array.isArray(module.lessons) ? `<span>${module.lessons.map((lesson) => escapeHtml(lesson.title || "Lesson")).join(" · ")}</span>` : ""}
            </li>
          `).join("")}</ul>
        </details>
      ` : ""}
      <div class="card-bottom">
        <span class="muted small">${moduleCount} modules · ${lessonCount} lessons</span>
        <button class="button small-button" type="button" data-action="enroll" data-course="${id}" ${state.user ? "" : 'data-needs-auth="true"'}>Enrol</button>
      </div>
    </article>
  `;
}

function renderCourseResults() {
  const query = state.courseQuery.trim().toLowerCase();
  const courses = state.courses.filter((course) => {
    const matchesQuery = !query || `${course.title} ${course.description} ${(course.tags || []).join(" ")}`.toLowerCase().includes(query);
    const matchesCategory = state.courseCategory === "all" || course.category === state.courseCategory;
    return matchesQuery && matchesCategory;
  });
  const results = document.querySelector("#course-results");
  const count = document.querySelector("#course-count");
  if (!results || !count) return;
  count.textContent = `${courses.length} ${courses.length === 1 ? "course" : "courses"}`;
  if (courses.length) {
    results.innerHTML = courses.map(courseCard).join("");
  } else if (state.courses.length) {
    results.innerHTML = `<div class="empty-state"><h2>No courses match those filters</h2><p>Try a different search or category.</p></div>`;
  } else {
    results.innerHTML = `<div class="empty-state"><h2>No courses yet</h2><p>Published courses will appear here. Instructors can add the first course.</p></div>`;
  }
}

async function renderCourses() {
  state.courses = await request("/api/courses");
  const categories = [...new Set(state.courses.map((course) => course.category || "general"))].sort();
  app.innerHTML = `
    ${heading("Course catalog", "Find your next course", "Browse published courses and enrol to track your progress.")}
    <div class="section-heading">
      <div><h2 id="course-count"></h2><p class="muted small">New courses appear here when an instructor publishes them.</p></div>
    </div>
    <section class="catalog-tools" aria-label="Filter courses">
      <label class="search-field">Search courses<input type="search" data-search placeholder="Try design, Python, cloud…" value="${escapeHtml(state.courseQuery)}"></label>
      <label>Category<select data-category><option value="all">All categories</option>${categories.map((category) => `<option value="${escapeHtml(category)}" ${state.courseCategory === category ? "selected" : ""}>${escapeHtml(category)}</option>`).join("")}</select></label>
    </section>
    <section id="course-results" class="grid" aria-live="polite"></section>
    ${state.user?.role === "instructor" || state.user?.role === "admin" ? `
      <section class="panel instructor-panel">
        <h2>Create a course</h2>
        <p class="muted small">Lesson IDs are used to record progress. Give each lesson a unique ID.</p>
        <form id="create-course-form">
          <div class="form-grid">
            <label>Course title<input name="title" required maxlength="200" placeholder="Introduction to design"></label>
            <label>Category<input name="category" maxlength="100" value="general"></label>
          </div>
          <label>Description<textarea name="description" maxlength="10000" placeholder="What will students learn?"></textarea></label>
          <label>Modules and lessons (JSON)<textarea name="modules" required spellcheck="false">[{"title":"Module 1","lessons":[{"id":"lesson-1","title":"First lesson"}]}]</textarea></label>
          <div class="actions"><button class="button" type="submit">Publish course</button></div>
        </form>
      </section>
    ` : ""}
  `;
  renderCourseResults();
}

function renderAccount() {
  if (state.user) {
    app.innerHTML = `
      ${heading("Account", "Your LearnHub account", "You’re signed in and can use your account to enrol and track progress.")}
      <section class="panel">
        <h2>Account details</h2>
        <p><strong>Email:</strong> ${escapeHtml(state.user.email)}</p>
        <p><strong>Role:</strong> ${escapeHtml(state.user.role)}</p>
        <div class="actions">
          <a class="button" href="/courses" data-link>Explore courses</a>
          <button class="button secondary" type="button" data-action="logout">Sign out</button>
        </div>
      </section>
    `;
    return;
  }

  const registering = state.authMode === "register";
  app.innerHTML = `
    ${heading("Account", "Welcome to LearnHub", "Sign in to your account or create one to save your learning.")}
    <section class="panel" style="max-width:560px">
      <div class="auth-switch">
        <button type="button" class="button ${registering ? "secondary" : ""}" data-action="auth-mode" data-mode="login">Sign in</button>
        <button type="button" class="button ${registering ? "" : "secondary"}" data-action="auth-mode" data-mode="register">Create account</button>
      </div>
      <form id="auth-form">
        <label>Email<input name="email" type="email" autocomplete="email" required placeholder="you@example.com"></label>
        <label>Password<input name="password" type="password" autocomplete="${registering ? "new-password" : "current-password"}" minlength="8" maxlength="72" required placeholder="At least 8 characters"></label>
        <button class="button" type="submit">${registering ? "Create student account" : "Sign in"}</button>
      </form>
      ${registering ? "" : `<aside class="demo-credentials"><strong>Try the demo</strong><span>demo.student@example.com</span><code>LearnHub123!</code><p>Sample enrolments and progress are ready to explore.</p></aside>`}
      <p class="muted small">New registrations receive the student role. Instructor accounts must be granted by an administrator.</p>
    </section>
  `;
}

async function renderEnrollments() {
  if (!state.user) {
    app.innerHTML = `${heading("My learning", "Your courses, in one place", "Sign in to view and manage your enrolments.")}${emptyState("Sign in required", "Your enrolments are tied to your account.", "/account", "Sign in")}`;
    return;
  }
  const enrollments = await request("/api/enrollments/me");
  const [courses, progressRecords] = await Promise.all([
    request("/api/courses"),
    Promise.all(enrollments.map((item) => request(`/api/progress/${encodeURIComponent(item.course_id)}`)
      .catch((error) => {
        if (error.message.includes("Progress not found")) return { percent: 0 };
        throw error;
      }))),
  ]);
  const coursesById = new Map(courses.map((course) => [course._id, course]));
  const progressByCourse = new Map(enrollments.map((item, index) => [item.course_id, progressRecords[index]]));
  app.innerHTML = `
    ${heading("My learning", "Your enrolments", "Courses you’re currently learning appear here.")}
    ${enrollments.length
      ? `<section class="grid">${enrollments.map((item) => `
          <article class="card learning-card">
            <span class="tag">${escapeHtml(item.status)}</span>
            <h3>${escapeHtml(coursesById.get(item.course_id)?.title || `Course ${item.course_id}`)}</h3>
            <p class="muted small">Enrolled ${escapeHtml(new Date(item.enrolled_at).toLocaleDateString())}</p>
            <div class="progress-track" role="progressbar" aria-valuenow="${escapeHtml(progressByCourse.get(item.course_id)?.percent || 0)}" aria-valuemin="0" aria-valuemax="100">
              <div class="progress-fill" style="width:${Math.min(100, Number(progressByCourse.get(item.course_id)?.percent) || 0)}%"></div>
            </div>
            <p class="muted small">${escapeHtml(progressByCourse.get(item.course_id)?.percent || 0)}% complete</p>
            <div class="actions">
              <a class="button small-button" href="/progress" data-link>View progress</a>
              <button class="button danger small-button" type="button" data-action="unenroll" data-enrollment="${escapeHtml(item.id)}">Unenrol</button>
            </div>
          </article>
        `).join("")}</section>`
      : emptyState("No enrolments yet", "Choose a course to start your learning journey.", "/courses", "Browse courses")}
  `;
}

async function renderProgress() {
  if (!state.user) {
    app.innerHTML = `${heading("Progress", "Your learning progress", "Sign in to see completed lessons and course progress.")}${emptyState("Sign in required", "Progress is saved to your account.", "/account", "Sign in")}`;
    return;
  }

  const enrollments = await request("/api/enrollments/me");
  if (!enrollments.length) {
    app.innerHTML = `${heading("Progress", "Your learning progress", "Your completed lessons and course progress.")}${emptyState("Nothing to track yet", "Enrol in a course before marking lessons complete.", "/courses", "Browse courses")}`;
    return;
  }

  const cards = await Promise.all(enrollments.map(async (enrollment) => {
    const [progress, course] = await Promise.all([
      request(`/api/progress/${encodeURIComponent(enrollment.course_id)}`).catch((error) => {
        if (error.message.includes("Progress not found")) return { completed_lessons: [], percent: 0 };
        throw error;
      }),
      request(`/api/courses/${encodeURIComponent(enrollment.course_id)}`),
    ]);
    const completed = new Set(progress.completed_lessons || []);
    const modules = Array.isArray(course.modules) ? course.modules : [];
    const lessons = modules.flatMap((module) => Array.isArray(module.lessons) ? module.lessons : []);
    return `
      <section class="panel">
        <div class="section-heading" style="margin:0 0 1rem">
          <div><h2>${escapeHtml(course.title)}</h2><p class="muted small">${completed.size} of ${lessons.length} lessons complete</p></div>
          <span class="tag">${escapeHtml(progress.percent || 0)}%</span>
        </div>
        <div class="progress-track" role="progressbar" aria-valuenow="${escapeHtml(progress.percent || 0)}" aria-valuemin="0" aria-valuemax="100">
          <div class="progress-fill" style="width:${Math.min(100, Number(progress.percent) || 0)}%"></div>
        </div>
        <div class="lesson-list">
          ${lessons.length ? lessons.map((lesson) => {
            const lessonId = String(lesson.id ?? "");
            const done = completed.has(lessonId);
            return `
              <div class="lesson-row ${done ? "complete" : ""}">
                <span><strong>${escapeHtml(lesson.title || "Untitled lesson")}</strong><br><span class="lesson-meta">${escapeHtml(lessonId || "Missing lesson ID")}</span></span>
                ${done
                  ? '<span class="tag">Complete</span>'
                  : `<button class="button small-button" type="button" data-action="complete-lesson" data-course="${escapeHtml(course._id)}" data-lesson="${escapeHtml(lessonId)}" ${lessonId ? "" : "disabled"}>Mark complete</button>`}
              </div>
            `;
          }).join("") : '<p class="muted small">This course has no lessons yet.</p>'}
        </div>
      </section>
    `;
  }));

  app.innerHTML = `${heading("Progress", "Your learning progress", "Mark lessons complete as you work through your courses.")}${cards.join("")}`;
}

async function renderNotifications() {
  if (!state.user) {
    app.innerHTML = `${heading("Notifications", "Your activity", "Sign in to view course and account activity.")}${emptyState("Sign in required", "Notifications are private to your account.", "/account", "Sign in")}`;
    return;
  }
  const notifications = await request("/api/notifications/me");
  const courses = await request("/api/courses");
  const coursesById = new Map(courses.map((course) => [course._id, course]));
  app.innerHTML = `
    ${heading("Notifications", "Your activity", "Updates generated as you sign up, enrol, and complete courses.")}
    ${notifications.length ? `
      <section class="grid two">${notifications.map((item) => `
        <article class="card">
          <div class="notification-head"><span class="tag">${escapeHtml(item.status)}</span><time class="muted small">${escapeHtml(new Date(item.created_at).toLocaleString())}</time></div>
          <h3>${escapeHtml(item.type.replaceAll(".", " "))}</h3>
          <p>${escapeHtml(item.type === "course.completed" ? `You completed ${coursesById.get(item.payload?.course_id)?.title || "a course"}.` : item.type === "enrollment.created" ? `${coursesById.get(item.payload?.course_id)?.title || "A course"} was added to your learning.` : "Welcome to LearnHub. Your learning journey starts here.")}</p>
        </article>
      `).join("")}</section>
    ` : emptyState("No activity yet", "Your account, enrolment, and course updates will show up here.", "/courses", "Browse courses")}
  `;
}

async function renderPage() {
  updateNavigation();
  app.innerHTML = '<section class="loading-card"><span class="spinner"></span> Loading this page...</section>';
  try {
    const path = window.location.pathname.replace(/\/+$/, "") || "/";
    if (path === "/") await renderHome();
    else if (path === "/account" || path === "/users") renderAccount();
    else if (path === "/courses") await renderCourses();
    else if (path === "/enrollments") await renderEnrollments();
    else if (path === "/progress") await renderProgress();
    else if (path === "/notifications") await renderNotifications();
    else {
      app.innerHTML = `${heading("Page not found", "We couldn’t find that page", "Choose a section from the navigation to continue.")}${emptyState("Go back to LearnHub", "The page may have moved.", "/", "Go home")}`;
    }
  } catch (error) {
    app.innerHTML = `${heading("Service unavailable", "We couldn’t load this page", "The request failed. Check that the services are running and try again.")}${errorPanel(error)}<div class="actions" style="margin-top:1rem"><button class="button" type="button" data-action="retry">Try again</button><a class="button secondary" href="/" data-link>Home</a></div>`;
  }
}

async function refreshUser() {
  if (!getToken()) {
    state.user = null;
    return;
  }
  try {
    state.user = await request("/api/users/me");
  } catch {
    localStorage.removeItem(tokenKey);
    state.user = null;
  }
}

document.addEventListener("click", async (event) => {
  const link = event.target.closest("a[data-link]");
  if (link) {
    event.preventDefault();
    navigate(link.getAttribute("href"));
    return;
  }

  const button = event.target.closest("[data-action]");
  if (!button) return;
  const action = button.dataset.action;

  try {
    if (action === "logout") {
      localStorage.removeItem(tokenKey);
      state.user = null;
      setToast("You’ve signed out.");
      navigate("/");
    } else if (action === "auth-mode") {
      state.authMode = button.dataset.mode;
      renderAccount();
    } else if (action === "refresh-status") {
      button.disabled = true;
      await getServiceStatuses();
      await renderHome();
      setToast("Service status updated.");
    } else if (action === "retry") {
      await renderPage();
    } else if (action === "enroll") {
      if (!getToken()) {
        navigate("/account");
        setToast("Sign in before enrolling.", "error");
        return;
      }
      button.disabled = true;
      await request("/api/enrollments", {
        method: "POST",
        body: JSON.stringify({ course_id: button.dataset.course }),
      });
      setToast("You’re enrolled. Happy learning!");
      await renderPage();
    } else if (action === "unenroll") {
      if (!window.confirm("Remove this course from your enrolments?")) return;
      await request(`/api/enrollments/${encodeURIComponent(button.dataset.enrollment)}`, { method: "DELETE" });
      setToast("Course removed from your enrolments.");
      await renderPage();
    } else if (action === "complete-lesson") {
      button.disabled = true;
      await request(
        `/api/progress/${encodeURIComponent(button.dataset.course)}/lessons/${encodeURIComponent(button.dataset.lesson)}/complete`,
        { method: "POST" },
      );
      setToast("Lesson marked complete.");
      await renderProgress();
    }
  } catch (error) {
    setToast(error.message || "The action failed.", "error");
    button.disabled = false;
  }
});

document.addEventListener("submit", async (event) => {
  const form = event.target;
  if (!(form instanceof HTMLFormElement)) return;
  event.preventDefault();
  const submitButton = form.querySelector('button[type="submit"]');
  if (submitButton) submitButton.disabled = true;

  try {
    const values = new FormData(form);
    if (form.id === "auth-form") {
      const email = String(values.get("email"));
      const password = String(values.get("password"));
      if (state.authMode === "register") {
        await request("/api/users/register", {
          method: "POST",
          body: JSON.stringify({ email, password }),
        });
        state.authMode = "login";
        await renderPage();
        setToast("Account created. Sign in to continue.");
        return;
      }
      const result = await request("/api/users/login", {
        method: "POST",
        body: JSON.stringify({ email, password }),
      });
      localStorage.setItem(tokenKey, result.access_token);
      await refreshUser();
      setToast("You’re signed in.");
      navigate("/");
    } else if (form.id === "create-course-form") {
      let modules;
      try {
        modules = JSON.parse(String(values.get("modules")));
      } catch {
        throw new Error("Modules and lessons must be valid JSON.");
      }
      if (!Array.isArray(modules)) throw new Error("Modules must be a JSON array.");
      const course = await request("/api/courses", {
        method: "POST",
        body: JSON.stringify({
          title: values.get("title"),
          description: values.get("description"),
          category: values.get("category") || "general",
          modules,
        }),
      });
      setToast(`Course published (${course.id}).`);
      await renderCourses();
    }
  } catch (error) {
    setToast(error.message || "The request failed.", "error");
    if (submitButton) submitButton.disabled = false;
  }
});

document.addEventListener("change", (event) => {
  if (event.target.matches("[data-category]")) {
    state.courseCategory = event.target.value;
    renderCourseResults();
  }
});

document.addEventListener("input", (event) => {
  if (event.target.matches("[data-search]")) {
    state.courseQuery = event.target.value;
    renderCourseResults();
  }
});
window.addEventListener("popstate", renderPage);

async function start() {
  await refreshUser();
  await getServiceStatuses();
  await renderPage();
}

start();
