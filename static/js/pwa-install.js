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

let deferredInstallPrompt = null;

let installCardDismissed = false;


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
// The button is ALWAYS visible whenever the Quick Access
// card is visible.
//
// Native installation availability determines what happens
// when the button is clicked — NOT whether the button exists.
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
    // CHROME / EDGE / ANDROID
    // -----------------------------------------------------

    lacInstallHelp.innerHTML =
        "<strong>Install Kalxa:</strong><br>" +
        "Open your browser menu (⋮), then choose " +
        "<strong>Add to Home screen</strong> " +
        "or <strong>Install app</strong>.";

    lacInstallHelp.style.display =
        "block";

}


// =========================================================
// UPDATE UI
// =========================================================

function updateInstallUI() {

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
    //
    // Always show both:
    //
    // Quick Access card
    // +
    // Add Kalxa button
    //
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
// CAPTURE NATIVE INSTALL PROMPT
// =========================================================

window.addEventListener(
    "beforeinstallprompt",
    function(event) {

        event.preventDefault();


        deferredInstallPrompt =
            event;


        // If the browser is offering installation,
        // an old "installed" localStorage marker
        // cannot be trusted.

        clearOldInstallMarker();


        console.log(
            "[Kalxa PWA] Native install prompt available."
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
            // NATIVE INSTALL PROMPT AVAILABLE
            // -------------------------------------------------

            if (
                deferredInstallPrompt
            ) {

                lacInstallButton.disabled =
                    true;


                try {

                    deferredInstallPrompt.prompt();


                    const result =
                        await deferredInstallPrompt
                            .userChoice;


                    console.log(
                        "[Kalxa PWA] Install choice:",
                        result.outcome
                    );


                    if (
                        result.outcome ===
                        "accepted"
                    ) {

                        rememberKalxaInstalled();

                        hideInstallSection();


                        console.log(
                            "[Kalxa PWA] Installation accepted."
                        );

                    } else {

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


                // The captured prompt can only be used once.

                deferredInstallPrompt =
                    null;


                lacInstallButton.disabled =
                    false;


                // If Kalxa is not actually running
                // standalone, keep the Quick Access
                // interface available.

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
            // Chrome/another browser has not supplied
            // beforeinstallprompt.
            //
            // Do NOT hide the button.
            //
            // Give the user manual installation instructions.
            // -------------------------------------------------

            console.log(
                "[Kalxa PWA] Native install prompt unavailable. Showing manual instructions."
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
// access.html / qr_entry.html should start the section with:
//
// <section
//     id="lac-install-section"
//     class="lac-install-section"
//     hidden
// >
//
// This prevents the browser from painting the wrong state
// before JavaScript runs.
//
// =========================================================

hideInstallSection();


// Wait until DOM/script initialization has completed,
// then reveal the correct state.
//
// requestAnimationFrame avoids the previous competing
// timer-based UI changes.

window.requestAnimationFrame(
    function() {

        updateInstallUI();

    }
);
