
// =========================================================
// LaC PWA INSTALL
// =========================================================
//
// Responsibilities:
//
// 1. Register the LaC service worker.
// 2. Capture the browser install prompt.
// 3. Show the "Add LaC to Home Screen" button.
// 4. Trigger installation when the user taps the button.
// 5. Hide the button when LaC is already installed.
//
// =========================================================


// =========================================================
// ELEMENTS
// =========================================================

const lacInstallSection =
    document.getElementById(
        "lac-install-section"
    );

const lacInstallButton =
    document.getElementById(
        "lac-install-button"
    );

const lacInstalledMessage =
    document.getElementById(
        "lac-installed-message"
    );


// =========================================================
// INSTALL PROMPT STORAGE
// =========================================================

let deferredInstallPrompt = null;


// =========================================================
// CHECK IF RUNNING AS INSTALLED APP
// =========================================================

function isRunningStandalone() {

    const standaloneDisplayMode =
        window.matchMedia(
            "(display-mode: standalone)"
        ).matches;


    const iosStandalone =
        window.navigator.standalone === true;


    return (
        standaloneDisplayMode
        ||
        iosStandalone
    );

}


// =========================================================
// UPDATE INSTALL UI
// =========================================================

function updateInstallUI() {

    if (!lacInstallSection) {
        return;
    }


    // Already launched as an installed PWA.

    if (isRunningStandalone()) {

        if (lacInstallButton) {

            lacInstallButton.style.display =
                "none";

        }


        if (lacInstalledMessage) {

            lacInstalledMessage.style.display =
                "block";

        }


        lacInstallSection.style.display =
            "block";


        return;

    }


    // Install prompt available.

    if (deferredInstallPrompt) {

        lacInstallSection.style.display =
            "block";


        if (lacInstallButton) {

            lacInstallButton.style.display =
                "inline-flex";

        }


        if (lacInstalledMessage) {

            lacInstalledMessage.style.display =
                "none";

        }


        return;

    }


    // Browser has not provided an install prompt.

    lacInstallSection.style.display =
        "none";

}


// =========================================================
// REGISTER SERVICE WORKER
// =========================================================

if (
    "serviceWorker" in navigator
) {

    window.addEventListener(
        "load",
        function() {

            navigator.serviceWorker
                .register(
                    "/service-worker.js"
                )
                .then(
                    function(registration) {

                        console.log(
                            "[LaC PWA] Service worker registered:",
                            registration.scope
                        );

                    }
                )
                .catch(
                    function(error) {

                        console.error(
                            "[LaC PWA] Service worker registration failed:",
                            error
                        );

                    }
                );

        }
    );

}


// =========================================================
// CAPTURE INSTALL EVENT
// =========================================================
//
// Chromium-based browsers may fire this event when
// the PWA satisfies installation requirements.
//
// =========================================================

window.addEventListener(
    "beforeinstallprompt",
    function(event) {

        // Prevent browser from immediately showing
        // its own install UI.

        event.preventDefault();


        deferredInstallPrompt =
            event;


        console.log(
            "[LaC PWA] Install prompt ready."
        );


        updateInstallUI();

    }
);


// =========================================================
// INSTALL BUTTON
// =========================================================

if (lacInstallButton) {

    lacInstallButton.addEventListener(
        "click",
        async function() {

            if (!deferredInstallPrompt) {

                return;

            }


            lacInstallButton.disabled =
                true;


            try {

                deferredInstallPrompt.prompt();


                const result =
                    await deferredInstallPrompt
                        .userChoice;


                console.log(
                    "[LaC PWA] Install choice:",
                    result.outcome
                );


                if (
                    result.outcome ===
                    "accepted"
                ) {

                    console.log(
                        "[LaC PWA] Installation accepted."
                    );

                } else {

                    console.log(
                        "[LaC PWA] Installation dismissed."
                    );

                }

            } catch (error) {

                console.error(
                    "[LaC PWA] Install error:",
                    error
                );

            }


            deferredInstallPrompt =
                null;


            lacInstallButton.disabled =
                false;


            updateInstallUI();

        }
    );

}


// =========================================================
// APP INSTALLED EVENT
// =========================================================

window.addEventListener(
    "appinstalled",
    function() {

        console.log(
            "[LaC PWA] App installed."
        );


        deferredInstallPrompt =
            null;


        if (lacInstallButton) {

            lacInstallButton.style.display =
                "none";

        }


        if (lacInstalledMessage) {

            lacInstalledMessage.style.display =
                "block";

        }


        if (lacInstallSection) {

            lacInstallSection.style.display =
                "block";

        }

    }
);


// =========================================================
// DISPLAY MODE CHANGE
// =========================================================
//
// Useful if the page changes into standalone mode.
//
// =========================================================

const standaloneMediaQuery =
    window.matchMedia(
        "(display-mode: standalone)"
    );


if (
    standaloneMediaQuery
    &&
    typeof standaloneMediaQuery
        .addEventListener === "function"
) {

    standaloneMediaQuery.addEventListener(
        "change",
        updateInstallUI
    );

}


// Initial check.

updateInstallUI();
