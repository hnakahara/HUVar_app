// HUHVar 共通フロントスクリプト（CSP nonce 化に伴い、インライン属性ハンドラを外部化）。
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
});
