// HUVar 共通フロントスクリプト（CSP nonce 化に伴い、インライン属性ハンドラを外部化）。
document.addEventListener("DOMContentLoaded", function () {
  // 言語セレクタ: 変更で所属フォームを自動送信
  document.querySelectorAll("select[data-autosubmit]").forEach(function (el) {
    el.addEventListener("change", function () {
      if (el.form) el.form.submit();
    });
  });

  // 「ファイルを選択」ボタン → 隠しファイル入力をクリック
  var trigger = document.querySelector("[data-file-trigger]");
  if (trigger) {
    var targetInput = document.getElementById(trigger.getAttribute("data-file-trigger"));
    if (targetInput) {
      trigger.addEventListener("click", function () {
        targetInput.click();
      });
    }
  }

  // ファイル入力の変更 → 選択ファイル名を表示
  var fileInput = document.querySelector('input[type="file"][data-name-target]');
  if (fileInput) {
    var label = document.getElementById(fileInput.getAttribute("data-name-target"));
    var emptyText = fileInput.getAttribute("data-empty-text") || "";
    fileInput.addEventListener("change", function () {
      if (label) {
        label.textContent = fileInput.files.length ? fileInput.files[0].name : emptyText;
      }
    });
  }

  // data-copy ボタン: 指定 id の要素テキストをクリップボードへコピー。
  // navigator.clipboard は secure context (HTTPS/localhost) のみのため、
  // HTTP 環境では execCommand("copy") にフォールバックする。
  document.querySelectorAll("button[data-copy]").forEach(function (btn) {
    btn.addEventListener("click", function () {
      var src = document.getElementById(btn.getAttribute("data-copy"));
      if (!src) return;
      var text = src.textContent;
      var flash = function () {
        var orig = btn.textContent;
        btn.textContent = "✓";
        setTimeout(function () { btn.textContent = orig; }, 1200);
      };
      var fallback = function () {
        var ta = document.createElement("textarea");
        ta.value = text;
        ta.style.position = "fixed";
        ta.style.top = "0";
        ta.style.opacity = "0";
        document.body.appendChild(ta);
        ta.focus();
        ta.select();
        try { document.execCommand("copy"); flash(); } catch (e) { /* noop */ }
        document.body.removeChild(ta);
      };
      if (navigator.clipboard && navigator.clipboard.writeText) {
        navigator.clipboard.writeText(text).then(flash).catch(fallback);
      } else {
        fallback();
      }
    });
  });
});
