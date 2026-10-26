// The navbar's Overpass picker. The choice lives in a cookie, so every request and the
// route calculation's websocket carry it without anything else having to pass it on;
// it takes effect from the next query, whatever is already loaded stays as it is.
const select = document.getElementById("overpass-instance")

if (select)
    select.addEventListener("change", () => {
        const name = select.dataset.cookie
        document.cookie = select.value
            ? `${name}=${encodeURIComponent(select.value)}; path=/; max-age=31536000; samesite=lax`
            : `${name}=; path=/; max-age=0; samesite=lax`
    })
