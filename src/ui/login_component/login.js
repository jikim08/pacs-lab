const REMEMBERED_ID_KEY = "pacs.login.remembered_id";

export default function ({ parentElement, data, setTriggerValue }) {
  const form = parentElement.querySelector("#pacs-login-form");
  const userId = parentElement.querySelector("#pacs-login-id");
  const password = parentElement.querySelector("#pacs-login-password");
  const showPassword = parentElement.querySelector("#pacs-login-show");
  const rememberId = parentElement.querySelector("#pacs-login-remember");
  const forgotPassword = parentElement.querySelector("#pacs-login-forgot");
  const submitButton = parentElement.querySelector("#pacs-login-submit");
  const feedback = parentElement.querySelector("#pacs-login-feedback");
  let submitting = false;

  feedback.textContent = data?.message ?? "";
  feedback.dataset.kind = data?.message_kind ?? "error";
  submitButton.disabled = false;
  submitButton.textContent = "로그인";

  try {
    const savedId = window.localStorage.getItem(REMEMBERED_ID_KEY);
    if (savedId !== null && !userId.value) {
      userId.value = savedId;
      rememberId.checked = true;
    }
  } catch {
    // The form remains usable when browser storage is unavailable.
  }

  showPassword.onclick = () => {
    const visible = password.type === "password";
    password.type = visible ? "text" : "password";
    showPassword.textContent = visible ? "숨김" : "표시";
    showPassword.setAttribute("aria-label", visible ? "비밀번호 숨김" : "비밀번호 표시");
    showPassword.setAttribute("aria-pressed", String(visible));
  };

  const updateRememberedId = () => {
    try {
      if (rememberId.checked) {
        window.localStorage.setItem(REMEMBERED_ID_KEY, userId.value.trim());
      } else {
        window.localStorage.removeItem(REMEMBERED_ID_KEY);
      }
    } catch {
      // Browser storage can be disabled independently of the login form.
    }
  };

  rememberId.onchange = updateRememberedId;
  userId.oninput = () => {
    feedback.textContent = "";
    if (rememberId.checked) updateRememberedId();
  };
  password.oninput = () => { feedback.textContent = ""; };

  forgotPassword.onclick = () => setTriggerValue("forgot_password", true);

  form.onsubmit = (event) => {
    event.preventDefault();
    if (submitting) return;
    submitting = true;
    submitButton.disabled = true;
    submitButton.textContent = "확인 중...";
    updateRememberedId();
    setTriggerValue("submit", {
      user_id: userId.value,
      password: password.value,
    });
  };
}
