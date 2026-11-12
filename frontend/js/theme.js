document.addEventListener("alpine:init", function () {
    var boot = window.__civicThemeBoot || {};
    var STORAGE_KEY = boot.storageKey || "civic-theme";
    var root = document.documentElement;
    var media = window.matchMedia("(prefers-color-scheme: dark)");
    var names = {system: "System (follow device)", light: "Light", dark: "Dark"};
    var next = {system: "Light", light: "Dark", dark: "System"};

    function resolved(mode) {
        return mode === "system" ? (media.matches ? "dark" : "light") : mode;
    }

    function apply(mode) {
        var theme = resolved(mode);
        root.classList.toggle("dark", theme === "dark");
        root.dataset.themeMode = mode;
        root.style.colorScheme = theme;
    }

    Alpine.store("theme", {
        mode: boot.mode || "system",
        get resolved() {
            return resolved(this.mode);
        },
        get label() {
            return "Theme: " + names[this.mode] + " — switch to " + next[this.mode];
        },
        set: function (mode) {
            if (mode !== "light" && mode !== "dark" && mode !== "system") {
                return;
            }
            this.mode = mode;
            try {
                if (mode === "system") {
                    localStorage.removeItem(STORAGE_KEY);
                } else {
                    localStorage.setItem(STORAGE_KEY, mode);
                }
            } catch (error) {}
            apply(mode);
        },
        cycle: function () {
            this.set(
                this.mode === "system"
                    ? "light"
                    : this.mode === "light"
                        ? "dark"
                        : "system"
            );
        },
    });

    var store = Alpine.store("theme");
    apply(store.mode);

    function handleMediaChange() {
        if (store.mode === "system") {
            apply("system");
        }
    }

    if (media.addEventListener) {
        media.addEventListener("change", handleMediaChange);
    } else if (media.addListener) {
        media.addListener(handleMediaChange);
    }

    window.addEventListener("storage", function (event) {
        if (event.key !== STORAGE_KEY) {
            return;
        }
        var mode = "system";
        try {
            var value = localStorage.getItem(STORAGE_KEY);
            if (value === "light" || value === "dark") {
                mode = value;
            }
        } catch (error) {}
        store.mode = mode;
        apply(mode);
    });
});
