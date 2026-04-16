const form = document.querySelector("#task-form");
const input = document.querySelector("#task-input");
const list = document.querySelector("#task-list");
const summary = document.querySelector("#summary");
const filterButtons = Array.from(document.querySelectorAll("[data-filter]"));

const state = {
  filter: "all",
  tasks: [],
};

function render() {
  const visibleTasks = state.tasks.filter((task) => {
    if (state.filter === "active") {
      return !task.done;
    }
    if (state.filter === "completed") {
      return task.done;
    }
    return true;
  });

  list.innerHTML = "";
  for (const task of visibleTasks) {
    const item = document.createElement("li");
    item.className = `task-row${task.done ? " done" : ""}`;
    item.innerHTML = `
      <span class="task-title">${task.title}</span>
      <button class="button secondary" type="button" data-id="${task.id}">
        ${task.done ? "Mark active" : "Mark done"}
      </button>
    `;
    list.appendChild(item);
  }

  const completed = state.tasks.filter((task) => task.done).length;
  summary.textContent = `${state.tasks.length} total / ${completed} completed`;
}

form.addEventListener("submit", (event) => {
  event.preventDefault();
  const title = input.value.trim();
  if (!title) {
    return;
  }
  state.tasks.push({
    id: String(Date.now()),
    title,
    done: false,
  });
  input.value = "";
  render();
});

list.addEventListener("click", (event) => {
  const button = event.target.closest("button[data-id]");
  if (!button) {
    return;
  }
  const task = state.tasks.find((candidate) => candidate.id === button.dataset.id);
  if (!task) {
    return;
  }
  task.done = !task.done;
  render();
});

for (const button of filterButtons) {
  button.addEventListener("click", () => {
    state.filter = button.dataset.filter || "all";
    for (const candidate of filterButtons) {
      candidate.classList.toggle("active", candidate === button);
    }
    render();
  });
}

render();
