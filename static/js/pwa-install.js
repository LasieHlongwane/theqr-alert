// =========================================================
// KALXA PWA INSTALL
// Powered by LaC
// =========================================================
//
// Responsibilities:
//
// 1. Register the Kalxa service worker.
// 2. Prevent the QUICK ACCESS card from flashing on load.
// 3. Hide the card when Kalxa is running as an installed app.
// 4. Show the install button when the browser confirms
//    installation is available.
// 5. Recover if Kalxa was previously installed and later
//    uninstalled.
// 6. Handle the browser installation prompt.
// 7. Allow the user to dismiss the card for the current page.
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

let installStateResolved = false;


// =========================================================
// LOCAL STORAGE KEY
// =========================================================

const KALXA_INSTALLED_KEY =
    "lac_pwa_installed";


// =========================================================
// CHECK WHETHER KALXA IS RUNNING AS INSTALLED APP
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
// PREVIOUS INSTALL MARKER
// =========================================================
//
// This is only a historical hint.
//
// IMPORTANT:
//
// We do NOT use this value by itself to permanently hide
// the install card.
//
// A user can install Kalxa, later uninstall it, and still
// have this localStorage value left behind.
//
// =========================================================

function wasKalxaPreviouslyInstalled() {

    try {

        return (
            localStorage.getItem(
                KALXA_INSTALLED_KEY
            ) === "true"
        );

    } catch (error) {

        console.warn(
            "[Kalxa PWA] Could not read install state:",
            error
        );

        return false;

    }

}


// =========================================================
// SAVE INSTALL MARKER
// =========================================================

function rememberKalxaInstalled() {

    try {

        localStorage.setItem(
            KALXA_INSTALLED_KEY,
            "true"
        );

    } catch (error) {

        console.warn(
            "[Kalxa PWA] Could not save install state:",
            error
        );

    }

}


// =========================================================
// CLEAR STALE INSTALL MARKER
// =========================================================

function clearStaleInstallMarker() {

    try {

        localStorage.removeItem(
            KALXA_INSTALLED_KEY
        );

    } catch (error) {

        console.warn(
            "[Kalxa PWA] Could not clear stale install state:",
            error
        );

    }

}


// =========================================================
// HIDE QUICK ACCESS CARD
// =========================================================

function hideInstallSection() {

    if (!lacInstallSection) {
        return;
    }


    lacInstallSection.hidden =
        true;

}


// =========================================================
// SHOW QUICK ACCESS CARD
// =========================================================

function showInstallSection() {

    if (!lacInstallSection) {
        return;
    }


    lacInstallSection.hidden =
        false;

}


// =========================================================
// HIDE INSTALL BUTTON
// =========================================================

function hideInstallButton() {

    if (!lacInstallButton) {
        return;
    }


    lacInstallButton.style.display =
        "none";

}


// =========================================================
// SHOW INSTALL BUTTON
// =========================================================

function showInstallButton() {

    if (!lacInstallButton) {
        return;
    }


    lacInstallButton.style.display =
        "inline-flex";

    lacInstallButton.disabled =
        false;

}


// =========================================================
// UPDATE INSTALL UI
// =========================================================

function updateInstallUI() {

    // -----------------------------------------------------
    // KALXA IS CURRENTLY RUNNING AS AN INSTALLED PWA
    // -----------------------------------------------------

    if (
        isRunningStandalone()
    ) {

        rememberKalxaInstalled();

        hideInstallSection();

        installStateResolved =
            true;

        return;

    }


    // -----------------------------------------------------
    // USER CLOSED THE CARD DURING THIS PAGE SESSION
    // -----------------------------------------------------

    if (
        installCardDismissed
    ) {

        hideInstallSection();

        installStateResolved =
            true;

        return;

    }


    // -----------------------------------------------------
    // BROWSER CONFIRMED KALXA CAN CURRENTLY BE INSTALLED
    //
    // This is important for uninstall recovery.
    //
    // If we previously stored "installed = true", but the
    // browser is now offering installation again, that old
    // marker is stale.
    // -----------------------------------------------------

    if (
        deferredInstallPrompt
    ) {

        if (
            wasKalxaPreviouslyInstalled()
        ) {

            clearStaleInstallMarker();

            console.log(
                "[Kalxa PWA] Previous install marker was stale and has been cleared."
            );

        }


        showInstallSection();

        showInstallButton();


        if (lacInstalledMessage) {

            lacInstalledMessage.style.display =
                "none";

        }


        if (lacInstallHelp) {

            lacInstallHelp.style.display =
                "block";

        }


        installStateResolved =
            true;

        return;

    }


    // -----------------------------------------------------
    // INSTALL PROMPT HAS NOT ARRIVED YET
    //
    // Do not immediately show the card.
    //
    // This prevents:
    //
    // Card appears
    //      ↓
    // JS checks state
    //      ↓
    // Card disappears
    //
    // Instead, we wait briefly for the browser to tell us
    // whether installation is available.
    // -----------------------------------------------------

    hideInstallButton();


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
// RESOLVE INITIAL INSTALL STATE
// =========================================================
//
// Chromium may dispatch beforeinstallprompt shortly after
// the page JavaScript begins.
//
// We therefore give the browser a short period to provide
// that event before deciding what to do with the card.
//
// =========================================================

function resolveInitialInstallState() {

    if (
        installStateResolved
    ) {

        return;

    }


    // Installed PWA:
    // definitely hide the card.

    if (
        isRunningStandalone()
    ) {

        rememberKalxaInstalled();

        hideInstallSection();

        installStateResolved =
            true;

        return;

    }


    // Browser already told us installation is possible.

    if (
        deferredInstallPrompt
    ) {

        updateInstallUI();

        return;

    }


    // -----------------------------------------------------
    // NORMAL BROWSER MODE
    //
    // We cannot reliably prove that an app is installed
    // merely from an old localStorage marker.
    //
    // Therefore the old marker does NOT permanently hide
    // the card.
    //
    // Show the Quick Access information card, but keep the
    // install button hidden until beforeinstallprompt is
    // actually available.
    // -----------------------------------------------------

    showInstallSection();

    hideInstallButton();


    if (lacInstalledMessage) {

        lacInstalledMessage.style.display =
            "none";

    }


    if (lacInstallHelp) {

        lacInstallHelp.style.display =
            "block";

    }


    installStateResolved =
        true;

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


            // Dismiss only for this page/session.
            //
            // We intentionally do not save this to
            // localStorage so the user can see the
            // Quick Access option again later.

            installCardDismissed =
                true;


            hideInstallSection();


            console.log(
                "[Kalxa PWA] Quick Access card dismissed."
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
                            "[Kalxa PWA] Service worker registered:",
                            registration.scope
                        );

                    }
                )
                .catch(
                    function(error) {

                        console.error(
                            "[Kalxa PWA] Service worker registration failed:",
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

window.addEventListener(
    "beforeinstallprompt",
    function(event) {

        // Prevent Chrome/Edge from immediately showing
        // their own mini-infobar.

        event.preventDefault();


        deferredInstallPrompt =
            event;


        console.log(
            "[Kalxa PWA] Install prompt is available."
        );


        // If localStorage says Kalxa was installed before,
        // but the browser is offering installation again,
        // Kalxa was likely removed.

        if (
            wasKalxaPreviouslyInstalled()
        ) {

            clearStaleInstallMarker();

            console.log(
                "[Kalxa PWA] Kalxa appears installable again. Stale installation state cleared."
            );

        }


        installStateResolved =
            true;


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


            if (
                !deferredInstallPrompt
            ) {

                console.log(
                    "[Kalxa PWA] Install prompt is not currently available."
                );

                return;

            }


            lacInstallButton.disabled =
                true;


            try {

                // -------------------------------------------------
                // SHOW BROWSER INSTALL DIALOG
                // -------------------------------------------------

                deferredInstallPrompt.prompt();


                const result =
                    await deferredInstallPrompt
                        .userChoice;


                console.log(
                    "[Kalxa PWA] Install choice:",
                    result.outcome
                );


                // -------------------------------------------------
                // USER ACCEPTED INSTALLATION
                // -------------------------------------------------

                if (
                    result.outcome ===
                    "accepted"
                ) {

                    rememberKalxaInstalled();


                    console.log(
                        "[Kalxa PWA] User accepted Kalxa installation."
                    );


                    // Hide immediately.
                    //
                    // The appinstalled event will also confirm
                    // installation when supported.

                    hideInstallSection();

                }


                // -------------------------------------------------
                // USER DISMISSED BROWSER INSTALL DIALOG
                // -------------------------------------------------

                else {

                    console.log(
                        "[Kalxa PWA] User dismissed the installation dialog."
                    );

                }


            } catch (error) {

                console.error(
                    "[Kalxa PWA] Install error:",
                    error
                );

            }


            // -------------------------------------------------
            // A beforeinstallprompt event can only be consumed
            // once.
            // -------------------------------------------------

            deferredInstallPrompt =
                null;


            lacInstallButton.disabled =
                false;


            // If installation was accepted, the card remains
            // hidden.
            //
            // If dismissed, the informational Quick Access
            // card can remain visible while the install button
            // disappears until the browser offers installation
            // again.

            if (
                wasKalxaPreviouslyInstalled()
            ) {

                hideInstallSection();

            } else {

                showInstallSection();

                hideInstallButton();

            }

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
            "[Kalxa PWA] Kalxa installed successfully."
        );


        rememberKalxaInstalled();


        deferredInstallPrompt =
            null;


        hideInstallSection();

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
        function(event) {

            if (
                event.matches
            ) {

                rememberKalxaInstalled();

                hideInstallSection();

            } else {

                // The page has left standalone mode.
                //
                // Re-evaluate rather than trusting the old
                // localStorage marker.

                installStateResolved =
                    false;


                window.setTimeout(
                    resolveInitialInstallState,
                    250
                );

            }

        }
    );

}


// =========================================================
// INITIAL PAGE LOAD
// =========================================================
//
// IMPORTANT:
//
// access.html should contain:
//
//     <section
//         id="lac-install-section"
//         class="lac-install-section"
//         hidden
//     >
//
// The card therefore starts invisible BEFORE JavaScript
// executes.
//
// This eliminates the visual flash.
//
// =========================================================

hideInstallSection();


// Give Chromium a brief opportunity to fire
// beforeinstallprompt before resolving the fallback UI.

window.setTimeout(
    resolveInitialInstallState,
    350
);
