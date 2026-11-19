const form = document.querySelector("#passwordForm");
const message = document.querySelector("#passwordMessage");
const currentPassword = document.querySelector("#currentPassword");
const newPassword = document.querySelector("#newPassword");
const confirmPassword = document.querySelector("#confirmPassword");
const submitButton = document.querySelector("#passwordSubmit");
let csrfToken = "";

async function initialize() {
  const response = await fetch("/api/auth/session", {
    headers: {Accept: "application/json"},
  });
  const payload = await response.json();
  if (!payload.id) {
    location.href = "/login";
    return;
  }
  csrfToken = payload.csrf_token || "";
}

form.addEventListener("submit", async (event) => {
  event.preventDefault();
  if (newPassword.value !== confirmPassword.value) {
    message.textContent = "两次输入的新密码不一致。";
    return;
  }
  submitButton.disabled = true;
  message.textContent = "正在修改密码……";
  try {
    const response = await fetch("/api/auth/password", {
      method: "POST",
      headers: {
        "Content-Type": "application/json",
        "X-CSRF-Token": csrfToken,
      },
      body: JSON.stringify({
        current_password: currentPassword.value,
        new_password: newPassword.value,
      }),
    });
    const payload = await response.json();
    if (!response.ok) throw new Error(payload.error || "修改失败");
    message.textContent = "密码修改成功，请重新登录。";
    setTimeout(() => {
      location.href = "/login";
    }, 700);
  } catch (error) {
    message.textContent = error.message;
  } finally {
    submitButton.disabled = false;
  }
});

initialize().catch(() => {
  location.href = "/login";
});
