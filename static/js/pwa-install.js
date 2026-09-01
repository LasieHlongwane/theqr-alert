
// =========================================================
// LaC PWA INSTALL
// =========================================================
//
// Responsibilities:
//
// 1. Register the LaC service worker.
// 2. Keep the QUICK ACCESS card visible.
// 3. Show the install button only when installation
//    is available.
// 4. Show an installed message when LaC is running
//    as an installed app.
// 5. Handle the browser installation prompt.
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

const lacInstallHelp =
    document.getElementById(
        "lac-install-help"
    );


// =========================================================
// INSTALL PROMPT STORAGE
// =========================================================

let deferredInstallPrompt = null;


// =========================================================
// CHECK WHETHER LaC IS RUNNING AS AN INSTALLED APP
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

    // -----------------------------------------
    // Keep QUICK ACCESS card visible.
    // -----------------------------------------

    if (lacInstallSection) {

        lacInstallSection.style.display =
            "block";

    }


    // -----------------------------------------
    // LaC is already running as installed PWA.
    // -----------------------------------------

    if (isRunningStandalone()) {

        if (lacInstallButton) {

            lacInstallButton.style.display =
                "none";

        }


        if (lacInstalledMessage) {

            lacInstalledMessage.style.display =
                "block";

        }


        if (lacInstallHelp) {

            lacInstallHelp.style.display =
                "none";

        }


        return;

    }


    // -----------------------------------------
    // Browser says LaC can be installed.
    // -----------------------------------------

    if (deferredInstallPrompt) {

        if (lacInstallButton) {

            lacInstallButton.style.display =
                "inline-flex";

            lacInstallButton.disabled =
                false;

        }


        if (lacInstalledMessage) {

            lacInstalledMessage.style.display =
                "none";

        }


        if (lacInstallHelp) {

            lacInstallHelp.style.display =
                "block";

        }


        return;

    }


    // -----------------------------------------
    // Install prompt is not available yet.
    //
    // IMPORTANT:
    // Keep the QUICK ACCESS card visible.
    // Hide only the actual installation button.
    // -----------------------------------------

    if (lacInstallButton) {

        lacInstallButton.style.display =
            "none";

    }


    if (lacInstalledMessage) {

        lacInstalledMessage.style.display =
            "none";

    }


    if (lacInstallHelp) {

        lacInstallHelp.style.display =
            "block";

    }

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
// CAPTURE INSTALL PROMPT
// =========================================================
//
// Chrome / Edge / Chromium browsers may fire this
// event once LaC satisfies their installation criteria.
//
// We save the event so installation only happens when
// the user taps our own button.
//
// =========================================================

window.addEventListener(
    "beforeinstallprompt",
    function(event) {

        event.preventDefault();


        deferredInstallPrompt =
            event;


        console.log(
            "[LaC PWA] Install prompt is available."
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

            // -------------------------------------
            // Browser has not provided an install
            // prompt.
            // -------------------------------------

            if (!deferredInstallPrompt) {

                console.log(
                    "[LaC PWA] Install prompt not available."
                );

                return;

            }


            lacInstallButton.disabled =
                true;


            try {

                // Show browser installation dialog.

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
                        "[LaC PWA] User accepted installation."
                    );

                } else {

                    console.log(
                        "[LaC PWA] User dismissed installation."
                    );

                }

            } catch (error) {

                console.error(
                    "[LaC PWA] Install error:",
                    error
                );

            }


            // The saved prompt can only be used once.

            deferredInstallPrompt =
                null;


            lacInstallButton.disabled =
                false;


            updateInstallUI();

        }
    );

}


// =========================================================
// APP INSTALLED
// =========================================================

window.addEventListener(
    "appinstalled",
    function() {

        console.log(
            "[LaC PWA] LaC installed successfully."
        );


        deferredInstallPrompt =
            null;


        if (lacInstallSection) {

            lacInstallSection.style.display =
                "block";

        }


        if (lacInstallButton) {

            lacInstallButton.style.display =
                "none";

        }


        if (lacInstalledMessage) {

            lacInstalledMessage.style.display =
                "block";

        }


        if (lacInstallHelp) {

            lacInstallHelp.style.display =
                "none";

        }

    }
);


// =========================================================
// WATCH FOR STANDALONE DISPLAY MODE
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
        function() {

            updateInstallUI();

        }
    );

}


// =========================================================
// INITIAL PAGE LOAD
// =========================================================
//
// QUICK ACCESS becomes visible immediately.
//
// The install button remains hidden until the browser
// provides beforeinstallprompt.
//
// =========================================================

updateInstallUI();

