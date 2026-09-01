// =========================================================
// LaC PWA INSTALL
// =========================================================
//
// Responsibilities:
//
// 1. Register the LaC service worker.
// 2. Keep the QUICK ACCESS card visible in the browser.
// 3. Show the install button only when installation
//    is available.
// 4. Show an installed message when LaC is running
//    as an installed app.
// 5. Handle the browser installation prompt.
// 6. Allow the user to dismiss the QUICK ACCESS card.
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

const lacInstallClose =
    document.getElementById(
        "lac-install-close"
    );


// =========================================================
// INSTALL STATE
// =========================================================

let deferredInstallPrompt = null;

let installCardDismissed = false;


// =========================================================
// CHECK WHETHER LaC IS RUNNING AS INSTALLED APP
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

    // -----------------------------------------------------
    // USER DISMISSED THE QUICK ACCESS CARD
    // -----------------------------------------------------

    if (installCardDismissed) {

        if (lacInstallSection) {

            lacInstallSection.style.display =
                "none";

        }

        return;

    }


    // -----------------------------------------------------
    // KEEP QUICK ACCESS CARD VISIBLE
    // -----------------------------------------------------

    if (lacInstallSection) {

        lacInstallSection.style.display =
            "block";

    }


    // -----------------------------------------------------
    // LaC IS RUNNING AS AN INSTALLED PWA
    // -----------------------------------------------------

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


    // -----------------------------------------------------
    // BROWSER SAYS LaC CAN BE INSTALLED
    // -----------------------------------------------------

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


    // -----------------------------------------------------
    // INSTALL PROMPT NOT AVAILABLE YET
    //
    // Keep the card visible.
    // Hide only the install button.
    // -----------------------------------------------------

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
// CLOSE QUICK ACCESS CARD
// =========================================================

if (lacInstallClose) {

    lacInstallClose.addEventListener(
        "click",
        function(event) {

            event.preventDefault();

            event.stopPropagation();


            installCardDismissed =
                true;


            if (lacInstallSection) {

                lacInstallSection.style.display =
                    "none";

            }


            console.log(
                "[LaC PWA] Quick Access card dismissed."
            );

        }
    );

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
// Chrome / Edge / Chromium browsers may fire this event
// when LaC satisfies PWA installation requirements.
//
// We store the event and use it when the user presses
// our own install button.
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
        async function(event) {

            event.preventDefault();

            event.stopPropagation();


            // Browser has not provided
            // an installation prompt yet.

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


            // beforeinstallprompt can only
            // be used once.

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
// QUICK ACCESS is visible immediately.
//
// The install button stays hidden until the browser
// supplies beforeinstallprompt.
//
// =========================================================

updateInstallUI();
