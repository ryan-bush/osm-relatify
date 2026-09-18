// The navbar shows the running version; if GitHub has published a newer release, it
// says so there rather than in a banner, so an out-of-date instance is visible without
// anything to dismiss. The server does the asking and caches the answer.
const link = document.getElementById("app-version")

if (link)
    fetch("/version")
        .then((resp) => (resp.ok ? resp.json() : null))
        .then((data) => {
            if (!data) return

            if (data.url) link.href = data.url

            if (!data.updateAvailable) return

            link.textContent = `Update to ${data.latest}`
            link.title = `Version ${data.latest} is available; this is ${data.version}`
            link.classList.add("app-version-outdated")
        })
        // an instance with no route to GitHub keeps its version on show and says no more
        .catch(() => {})
