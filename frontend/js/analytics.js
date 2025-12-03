/**
 * Umami Analytics - HTMX Event Tracking
 *
 * Tracks HTMX requests that have data-umami-htmx-event attributes.
 * The Umami script must be loaded before this runs.
 */
(function () {
  // Only run if Umami is loaded
  if (typeof umami === "undefined") return;

  // Track HTMX requests
  document.body.addEventListener("htmx:beforeRequest", function (evt) {
    var el = evt.detail.elt;
    var event = el.dataset.umamiHtmxEvent;
    if (event) {
      umami.track(event);
    }
  });
})();
