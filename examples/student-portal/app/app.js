const EXPECTED_EMAIL = "student@example.com";
const EXPECTED_PASSWORD = "tester123";

const loginForm = document.querySelector("#login-form");
const loginPanel = document.querySelector("#login-panel");
const dashboard = document.querySelector("#dashboard");
const profileMenu = document.querySelector("#profile-menu");
const profileToggle = document.querySelector("#profile-toggle");
const profileEmail = document.querySelector("#profile-email");
const loginError = document.querySelector("#login-error");

loginForm.addEventListener("submit", (event) => {
  event.preventDefault();
  const form = new FormData(loginForm);
  const email = String(form.get("email") || "").trim();
  const password = String(form.get("password") || "").trim();

  if (email !== EXPECTED_EMAIL || password !== EXPECTED_PASSWORD) {
    loginError.textContent = "Use the student@example.com / tester123 test account.";
    return;
  }

  loginError.textContent = "";
  profileEmail.textContent = email;
  loginPanel.classList.add("hidden");
  dashboard.classList.remove("hidden");
  profileToggle.classList.remove("hidden");
});

profileToggle.addEventListener("click", () => {
  const isHidden = profileMenu.classList.toggle("hidden");
  profileToggle.setAttribute("aria-expanded", String(!isHidden));
});
