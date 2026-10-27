// Small progressive enhancements; every page works without this file.
(function () {
  'use strict';

  // Password visibility toggle.
  document.querySelectorAll('[data-password-toggle]').forEach(function (button) {
    var input = document.getElementById(button.dataset.passwordToggle);
    if (!input) return;
    button.addEventListener('click', function () {
      var reveal = input.type === 'password';
      input.type = reveal ? 'text' : 'password';
      button.textContent = reveal ? 'Hide' : 'Show';
      button.setAttribute('aria-pressed', String(reveal));
      input.focus();
    });
  });

  // Grow the composer with its content, up to the CSS max-height.
  function autogrow(textarea) {
    textarea.style.height = 'auto';
    textarea.style.height = textarea.scrollHeight + 'px';
  }
  document.querySelectorAll('textarea[data-autogrow]').forEach(function (textarea) {
    textarea.addEventListener('input', function () { autogrow(textarea); });
  });

  // Suggested questions fill the composer; nothing is sent.
  var composer = document.getElementById('composer-input');
  document.querySelectorAll('[data-suggestion]').forEach(function (button) {
    button.addEventListener('click', function () {
      if (!composer) return;
      composer.value = button.dataset.suggestion;
      autogrow(composer);
      composer.focus();
    });
  });
})();
