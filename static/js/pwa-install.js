// =========================================================
// KALXA PWA INSTALL
// Powered by LaC
// =========================================================
//
// RULES:
//
// 1. Never flash the install card during page load.
// 2. Hide the card when Kalxa is running as an installed PWA.
// 3. Show the card in a normal browser.
// 4. ALWAYS show the "Add Kalxa to Home Screen" button
//    whenever the card is visible.
// 5. Use the native install prompt when available.
// 6. Otherwise show manual installation instructions.
// 7. Allow the user to dismiss the card for this page.
// 8. Support the early install-event capture from <head>.
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
// STATE
// =========================================================
//
// If Chrome fired beforeinstallprompt while <head> was
// loading, the early capture script stored the event in:
//
// window.kalxaInstallPrompt
//
// Pick it up immediately.
//
// =========================================================

let deferredInstallPrompt =
    window.kalxaInstallPrompt || null;

let installCardDismissed =
    false;


// =========================================================
// LOCAL STORAGE
// =========================================================

const KALXA_INSTALLED_KEY =
    "lac_pwa_installed";


// =========================================================
// STANDALONE CHECK
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
// REMEMBER INSTALL
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
// CLEAR OLD INSTALL MARKER
// =========================================================

function clearOldInstallMarker() {

    try {

        localStorage.removeItem(
            KALXA_INSTALLED_KEY
        );

    } catch (error) {

        console.warn(
            "[Kalxa PWA] Could not clear install state:",
            error
        );

    }

}


// =========================================================
// HIDE CARD
// =========================================================

function hideInstallSection() {

    if (!lacInstallSection) {
        return;
    }


    lacInstallSection.hidden =
        true;

}


// =========================================================
// SHOW CARD
// =========================================================

function showInstallSection() {

    if (!lacInstallSection) {
        return;
    }


    lacInstallSection.hidden =
        false;

}


// =========================================================
// SHOW INSTALL BUTTON
// =========================================================
//
// IMPORTANT:
//
// Whenever the Quick Access card is visible, the button
// remains visible.
//
// Whether Chrome supplied beforeinstallprompt determines
// what happens after the button is clicked.
//
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
// DEFAULT HELP TEXT
// =========================================================

function showDefaultHelp() {

    if (!lacInstallHelp) {
        return;
    }


    lacInstallHelp.textContent =
        "Add Kalxa for faster access to your local area.";

    lacInstallHelp.style.display =
        "block";

}


// =========================================================
// MANUAL INSTALL HELP
// =========================================================

function showManualInstallHelp() {

    if (!lacInstallHelp) {
        return;
    }


    const userAgent =
        navigator.userAgent || "";


    // -----------------------------------------------------
    // IOS / IPADOS
    // -----------------------------------------------------

    const isIOS =
        /iPhone|iPad|iPod/i.test(
            userAgent
        );


    if (isIOS) {

        lacInstallHelp.innerHTML =
            "<strong>Install Kalxa:</strong><br>" +
            "Tap the Share button in Safari, then choose " +
            "<strong>Add to Home Screen</strong>.";

        lacInstallHelp.style.display =
            "block";

        return;

    }


    // -----------------------------------------------------
    // CHROME / EDGE / ANDROID FALLBACK
    // -----------------------------------------------------

    lacInstallHelp.innerHTML =
        "<strong>Install Kalxa:</strong><br>" +
        "Open your browser menu (⋮), then choose " +
        "<strong>Install app</strong> or " +
        "<strong>Add to Home screen</strong>.";

    lacInstallHelp.style.display =
        "block";

}


// =========================================================
// SYNC EARLY INSTALL PROMPT
// =========================================================
//
// This is important.
//
// If the <head> script captured Chrome's event before this
// file loaded, copy that event into our local variable.
//
// =========================================================

function syncEarlyInstallPrompt() {

    if (
        !deferredInstallPrompt
        &&
        window.kalxaInstallPrompt
    ) {

        deferredInstallPrompt =
            window.kalxaInstallPrompt;


        console.log(
            "[Kalxa PWA] Saved early install prompt loaded."
        );

    }

}


// =========================================================
// UPDATE UI
// =========================================================

function updateInstallUI() {

    // -----------------------------------------------------
    // FIRST CHECK FOR EARLY CHROME EVENT
    // -----------------------------------------------------

    syncEarlyInstallPrompt();


    // -----------------------------------------------------
    // INSTALLED PWA
    // -----------------------------------------------------

    if (
        isRunningStandalone()
    ) {

        rememberKalxaInstalled();

        hideInstallSection();

        return;

    }


    // -----------------------------------------------------
    // USER DISMISSED CARD
    // -----------------------------------------------------

    if (
        installCardDismissed
    ) {

        hideInstallSection();

        return;

    }


    // -----------------------------------------------------
    // NORMAL BROWSER
    // -----------------------------------------------------

    showInstallSection();

    showInstallButton();


    if (lacInstalledMessage) {

        lacInstalledMessage.style.display =
            "none";

    }


    showDefaultHelp();

}


// =========================================================
// CLOSE CARD
// =========================================================

if (lacInstallClose) {

    lacInstallClose.addEventListener(
        "click",
        function(event) {

            event.preventDefault();

            event.stopPropagation();


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
// RECEIVE EARLY INSTALL PROMPT
// =========================================================
//
// access.html <head> dispatches this event immediately
// after it captures beforeinstallprompt.
//
// =========================================================

window.addEventListener(
    "kalxainstallpromptready",
    function() {

        if (
            window.kalxaInstallPrompt
        ) {

            deferredInstallPrompt =
                window.kalxaInstallPrompt;


            clearOldInstallMarker();


            console.log(
                "[Kalxa PWA] Early install prompt received."
            );


            updateInstallUI();

        }

    }
);


// =========================================================
// FALLBACK NATIVE INSTALL PROMPT CAPTURE
// =========================================================
//
// Keep this listener.
//
// It protects pages that do not yet have the early
// <head> capture script and also gives us another chance
// to receive the browser event.
//
// =========================================================

window.addEventListener(
    "beforeinstallprompt",
    function(event) {

        event.preventDefault();


        deferredInstallPrompt =
            event;


        window.kalxaInstallPrompt =
            event;


        clearOldInstallMarker();


        console.log(
            "[Kalxa PWA] Native install prompt captured."
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


            // -------------------------------------------------
            // CHECK EARLY CAPTURE ONE MORE TIME
            // -------------------------------------------------
            //
            // This is deliberately done at click time.
            //
            // If Chrome supplied the event before the main
            // script was ready, we still use it.
            //
            // -------------------------------------------------

            syncEarlyInstallPrompt();


            // -------------------------------------------------
            // NATIVE INSTALL PROMPT AVAILABLE
            // -------------------------------------------------

            if (
                deferredInstallPrompt
            ) {

                lacInstallButton.disabled =
                    true;


                try {

                    console.log(
                        "[Kalxa PWA] Opening native install prompt."
                    );


                    deferredInstallPrompt.prompt();


                    const result =
                        await deferredInstallPrompt
                            .userChoice;


                    console.log(
                        "[Kalxa PWA] Install choice:",
                        result.outcome
                    );


                    // -----------------------------------------
                    // ACCEPTED
                    // -----------------------------------------

                    if (
                        result.outcome ===
                        "accepted"
                    ) {

                        rememberKalxaInstalled();


                        hideInstallSection();


                        console.log(
                            "[Kalxa PWA] Installation accepted."
                        );

                    }


                    // -----------------------------------------
                    // DISMISSED
                    // -----------------------------------------

                    else {

                        console.log(
                            "[Kalxa PWA] Installation dismissed."
                        );

                    }


                } catch (error) {

                    console.error(
                        "[Kalxa PWA] Install error:",
                        error
                    );

                }


                // ---------------------------------------------
                // PROMPT CAN ONLY BE USED ONCE
                // ---------------------------------------------

                deferredInstallPrompt =
                    null;


                window.kalxaInstallPrompt =
                    null;


                lacInstallButton.disabled =
                    false;


                // ---------------------------------------------
                // KEEP CARD AVAILABLE IF USER CANCELLED
                // ---------------------------------------------

                if (
                    !isRunningStandalone()
                ) {

                    updateInstallUI();

                }


                return;

            }


            // -------------------------------------------------
            // NO NATIVE PROMPT
            // -------------------------------------------------
            //
            // Chrome / browser did not provide
            // beforeinstallprompt.
            //
            // JavaScript cannot manufacture the browser's
            // native installation event.
            //
            // Keep the button/card visible and provide
            // fallback instructions.
            //
            // -------------------------------------------------

            console.log(
                "[Kalxa PWA] Native install prompt unavailable."
            );


            showManualInstallHelp();

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
            "[Kalxa PWA] Kalxa installed successfully."
        );


        rememberKalxaInstalled();


        deferredInstallPrompt =
            null;


        window.kalxaInstallPrompt =
            null;


        hideInstallSection();

    }
);


// =========================================================
// WATCH STANDALONE MODE
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


                deferredInstallPrompt =
                    null;


                window.kalxaInstallPrompt =
                    null;


                hideInstallSection();

            } else {

                updateInstallUI();

            }

        }
    );

}


// =========================================================
// INITIAL PAGE LOAD
// =========================================================
//
// access.html / qr_entry.html should begin with:
//
// <section
//     id="lac-install-section"
//     class="lac-install-section"
//     hidden
// >
//
// =========================================================

hideInstallSection();


// =========================================================
// INITIAL EARLY-PROMPT CHECK
// =========================================================
//
// The early event may already have been captured before this
// script loaded.
//
// =========================================================

syncEarlyInstallPrompt();


// =========================================================
// DISPLAY CORRECT INITIAL STATE
// =========================================================

window.requestAnimationFrame(
    function() {

        updateInstallUI();

    }
);
