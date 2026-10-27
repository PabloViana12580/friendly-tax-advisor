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

  // Suggested questions fill the composer; the user still chooses to send.
  var composer = document.getElementById('composer-input');
  document.querySelectorAll('[data-suggestion]').forEach(function (button) {
    button.addEventListener('click', function () {
      if (!composer) return;
      composer.value = button.dataset.suggestion;
      autogrow(composer);
      composer.focus();
    });
  });

  // Chat: send the question, show a pending entry, then swap in the
  // server-rendered (already escaped) exchange.
  var form = document.getElementById('composer');
  if (!form || !composer) return;
  var thread = document.getElementById('thread');
  var welcome = document.getElementById('welcome');
  var errorBox = document.getElementById('composer-error');
  var sendButton = form.querySelector('button[type="submit"]');
  var csrfToken = form.querySelector('input[name="csrfmiddlewaretoken"]').value;
  var busy = false;

  function showError(message) {
    errorBox.textContent = message;
    errorBox.hidden = !message;
  }

  function setBusy(value) {
    busy = value;
    sendButton.disabled = value;
    sendButton.textContent = value ? 'Sending…' : 'Send';
    form.setAttribute('aria-busy', String(value));
  }

  function pendingEntry(question) {
    var article = document.createElement('article');
    article.className = 'sb-exchange sb-exchange--pending';
    var q = document.createElement('p');
    q.className = 'sb-exchange__question';
    q.textContent = question;
    var status = document.createElement('p');
    status.className = 'sb-exchange__pending';
    status.textContent = 'Searching the approved sources…';
    article.append(q, status);
    return article;
  }

  function addToHistory(id, title) {
    var list = document.getElementById('history-list');
    var empty = document.getElementById('history-empty');
    if (!list || list.querySelector('[data-conversation="' + id + '"]')) return;
    list.querySelectorAll('[aria-current]').forEach(function (a) { a.removeAttribute('aria-current'); });
    var item = document.createElement('li');
    var link = document.createElement('a');
    link.className = 'sb-history__link';
    link.href = form.dataset.conversationUrl + id + '/';
    link.dataset.conversation = id;
    link.setAttribute('aria-current', 'page');
    link.textContent = title;
    item.append(link);
    list.prepend(item);
    list.hidden = false;
    if (empty) empty.remove();
  }

  function send() {
    var question = composer.value.trim();
    if (busy) return;
    if (!question) {
      showError('Enter a question.');
      composer.focus();
      return;
    }
    showError('');
    setBusy(true);
    if (welcome) welcome.hidden = true;
    var pending = pendingEntry(question);
    thread.append(pending);
    pending.scrollIntoView({ block: 'start', behavior: 'smooth' });

    fetch(form.action, {
      method: 'POST',
      headers: { 'Content-Type': 'application/json', 'X-CSRFToken': csrfToken },
      credentials: 'same-origin',
      body: JSON.stringify({ question: question, conversation_id: form.dataset.conversationId || null })
    })
      .then(function (response) {
        return response.json().catch(function () { return {}; }).then(function (data) {
          if (!response.ok) throw new Error(data.error || 'The question couldn’t be sent. Try again.');
          return data;
        });
      })
      .then(function (data) {
        pending.insertAdjacentHTML('afterend', data.html);
        pending.remove();
        composer.value = '';
        autogrow(composer);
        if (!form.dataset.conversationId) {
          form.dataset.conversationId = data.conversation_id;
          history.replaceState(null, '', form.dataset.conversationUrl + data.conversation_id + '/');
          addToHistory(data.conversation_id, data.conversation_title);
        }
      })
      .catch(function (error) {
        // Keep the question in the box so it can be sent again.
        pending.remove();
        if (welcome && !thread.children.length) welcome.hidden = false;
        showError(error.message);
      })
      .finally(function () {
        setBusy(false);
        composer.focus();
      });
  }

  form.addEventListener('submit', function (event) {
    event.preventDefault();
    send();
  });
  // Enter sends; Shift+Enter starts a new line.
  composer.addEventListener('keydown', function (event) {
    if (event.key === 'Enter' && !event.shiftKey && !event.isComposing) {
      event.preventDefault();
      send();
    }
  });
})();
