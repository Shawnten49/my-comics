const setupMode = location.pathname === "/setup";
const form = document.querySelector("#authForm");
const message = document.querySelector("#authMessage");

if (setupMode) {
  document.querySelector("#authEyebrow").textContent = "FIRST SETUP";
  document.querySelector("#authTitle").textContent = "初始化管理员";
  document.querySelector("#authDescription").textContent =
    "输入启动终端显示的初始化令牌，创建首个管理员。";
  document.querySelector("#setupTokenField").classList.remove("hidden");
  document.querySelector("#authSubmit").textContent = "创建管理员";
}

form.addEventListener("submit", async (event) => {
  event.preventDefault();
  message.textContent = "正在处理……";
  const body = {
    username: document.querySelector("#authUsername").value.trim(),
    password: document.querySelector("#authPassword").value,
  };
  if (setupMode) {
    body.setup_token = document.querySelector("#setupToken").value;
  }
  try {
    const response = await fetch(
      setupMode ? "/api/auth/setup" : "/api/auth/login",
      {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify(body),
      },
    );
    const payload = await response.json();
    if (!response.ok) throw new Error(payload.error || "操作失败");
    if (setupMode) {
      message.textContent = "管理员创建成功，请登录。";
      setTimeout(() => {
        location.href = "/login";
      }, 700);
    } else {
      location.href = payload.must_change_password
        ? "/change-password"
        : "/";
    }
  } catch (error) {
    message.textContent = error.message;
  }
});
