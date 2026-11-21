/**
 * Triage keyboard shortcuts, registered as an Alpine component.
 *
 * The outer triage container owns this component so HTMX swaps of
 * #triage-card do not destroy it. Keys only fire outside form controls, and
 * form actions are serialized while an HTMX request is in flight.
 */
document.addEventListener("alpine:init", function () {
  "use strict";

  Alpine.data("triageKeyboard", function () {
    return {
      actions: {
        j: "next",
        k: "previous",
        r: "replied",
        d: "dismiss",
        s: "snooze",
        b: "backlog",
        p: "promote",
        m: "mute_subreddit",
        x: "mute_country",
        e: "edit_query",
        c: "copy_citation"
      },

      busy: false,

      init: function () {
        var self = this;
        ["htmx:afterRequest", "htmx:responseError", "htmx:sendError"].forEach(
          function (name) {
            self.$root.addEventListener(name, function () {
              self.busy = false;
            });
          }
        );
      },

      onKeydown: function (event) {
        if (
          event.defaultPrevented ||
          event.repeat ||
          event.isComposing ||
          event.ctrlKey ||
          event.metaKey ||
          event.altKey
        ) {
          return;
        }
        if (this.isTyping(event.target)) return;

        var action = this.actions[(event.key || "").toLowerCase()];
        if (!action) return;

        if (this.run(action)) {
          event.preventDefault();
        }
      },

      isTyping: function (target) {
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
      },

      run: function (action) {
        if (action === "next" || action === "previous") {
          var link = this.$root.querySelector(
            'a[data-action="' + action + '"]'
          );
          if (!link) return false;
          link.click();
          return true;
        }

        if (action === "copy_citation") {
          var copyButton = this.$root.querySelector(
            '[data-action="copy-citation"]'
          );
          if (!copyButton) return false;
          copyButton.click();
          return true;
        }

        if (action === "edit_query") {
          var input = this.$root.querySelector('input[name="query"]');
          if (!input) return false;
          input.focus();
          input.select();
          return true;
        }

        if (this.busy) return false;
        var form = this.$root.querySelector(
          'form[data-action="' + action + '"]'
        );
        if (!form) return false;
        var submit = form.querySelector('button[type="submit"]');
        if (!submit) return false;
        if (typeof form.reportValidity === "function" && !form.reportValidity()) {
          return false;
        }

        this.busy = true;
        submit.click();
        return true;
      },

      copyCitation: function (button) {
        if (!button) return false;
        var text = button.getAttribute("data-citation") || "";
        if (!text) return false;

        var self = this;
        if (
          navigator.clipboard &&
          typeof navigator.clipboard.writeText === "function"
        ) {
          navigator.clipboard.writeText(text).catch(function () {
            self.fallbackCopy(text);
          });
        } else {
          this.fallbackCopy(text);
        }
        return true;
      },

      fallbackCopy: function (text) {
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
    };
  });
});
