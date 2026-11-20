/**
 * Triage keyboard shortcuts.
 *
 * Keys only fire outside form controls so typing in the query or snooze
 * fields never triggers an action. Actions reuse the data-action values
 * rendered by the triage card, and navigation falls back to the Previous
 * and Next link labels.
 */
(function () {
  "use strict";

  var KEY_ACTIONS = {
    "j": "next",
    "k": "previous",
    "r": "replied",
    "d": "dismiss",
    "s": "snooze",
    "b": "backlog",
    "p": "promote",
    "m": "mute_subreddit",
    "x": "mute_country",
    "e": "edit_query",
    "c": "copy_citation"
  };

  function triageCard() {
    return document.getElementById("triage-card");
  }

  function shouldIgnore(event) {
    var target = event.target;
    if (!target || !target.tagName) return false;

    var tag = target.tagName.toUpperCase();
    if (tag === "INPUT" || tag === "TEXTAREA" || tag === "SELECT") {
      return true;
    }
    if (target.isContentEditable) return true;
    return (
      typeof target.closest === "function" &&
      target.closest("[contenteditable]") !== null
    );
  }

  function query(selector) {
    var card = triageCard();
    return card ? card.querySelector(selector) : null;
  }

  function findThreadLink(label) {
    return (
      query('a[data-action="' + label + '"]') || findThreadLinkByText(label)
    );
  }

  function findThreadLinkByText(label) {
    var card = triageCard();
    if (!card) return null;
    var links = card.querySelectorAll("a[href]");
    for (var i = 0; i < links.length; i += 1) {
      var text = links[i].textContent.replace(/\s+/g, " ").trim().toLowerCase();
      if (text === label) return links[i];
    }
    return null;
  }

  function submitForm(action) {
    var form = query('form[data-action="' + action + '"]');
    if (!form) return false;

    var button = form.querySelector('button[type="submit"]');
    if (typeof form.requestSubmit === "function") {
      form.requestSubmit(button || undefined);
    } else if (button) {
      button.click();
    } else {
      form.submit();
    }
    return true;
  }

  function clickButton(action) {
    var button = query(
      '[data-action="' + action + '"] button[type="submit"]'
    );
    if (!button) return false;
    button.click();
    return true;
  }

  function focusQuery() {
    var input = query('input[name="query"]');
    if (!input) return false;
    input.focus();
    input.select();
    return true;
  }

  function fallbackCopy(text) {
    var textarea = document.createElement("textarea");
    textarea.value = text;
    textarea.setAttribute("readonly", "");
    textarea.style.position = "fixed";
    textarea.style.top = "-1000px";
    textarea.style.left = "-1000px";
    document.body.appendChild(textarea);
    textarea.select();
    try {
      if (typeof document.execCommand === "function") {
        document.execCommand("copy");
      }
    } finally {
      document.body.removeChild(textarea);
    }
  }

  function copyText(text) {
    if (!text) return false;
    if (navigator.clipboard && typeof navigator.clipboard.writeText === "function") {
      try {
        navigator.clipboard.writeText(text).catch(function () {
          fallbackCopy(text);
        });
      } catch (error) {
        fallbackCopy(text);
      }
      return true;
    }
    fallbackCopy(text);
    return true;
  }

  function copyCitation() {
    var button = query('[data-action="copy-citation"]');
    if (!button) return false;
    return copyText(button.getAttribute("data-citation") || "");
  }

  function navigate(label) {
    var link = findThreadLink(label);
    if (!link) return false;
    window.location.assign(link.href);
    return true;
  }

  function run(action) {
    switch (action) {
      case "next":
        return navigate("next");
      case "previous":
        return navigate("previous");
      case "promote":
        return clickButton("promote");
      case "edit_query":
        return focusQuery();
      case "copy_citation":
        return copyCitation();
      default:
        return submitForm(action);
    }
  }

  document.addEventListener("keydown", function (event) {
    if (event.defaultPrevented || event.ctrlKey || event.metaKey || event.altKey) {
      return;
    }
    if (shouldIgnore(event)) return;

    var key = (event.key || "").toLowerCase();
    if (!Object.prototype.hasOwnProperty.call(KEY_ACTIONS, key)) return;

    var action = KEY_ACTIONS[key];
    if (run(action)) {
      event.preventDefault();
    }
  });
})();
