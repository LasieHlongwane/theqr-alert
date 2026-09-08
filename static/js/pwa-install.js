// =========================================================
// KALXA PWA INSTALL
// Powered by LaC
// =========================================================
//
// GOALS:
//
// 1. Never flash the install card during page load.
// 2. Hide the card when Kalxa is running as an installed PWA.
// 3. Show the Quick Access card in a normal browser.
// 4. Use Chrome's native install prompt whenever available.
// 5. On a first visit, give Chrome time to provide
//    beforeinstallprompt before falling back to manual help.
// 6. Do not require the user to refresh the page.
// 7. Support the early install-event capture from <head>.
// 8. Fall back to manual instructions when native install
//    genuinely is not available.
// 9. Allow the card to be dismissed for the current page.
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
// access.html may capture beforeinstallprompt before this
// script loads and save it in:
//
// window.kalxaInstallPrompt
//
// =========================================================

let deferredInstallPrompt =
    window.kalxaInstallPrompt || null;

let installCardDismissed =
    false;

let installButtonBusy =
    false;


// =========================================================
// INSTALL PROMPT WAIT SETTINGS
// =========================================================
//
// On a brand-new Chrome visit, the service worker / manifest
// may still be becoming install-ready.
//
// If the user taps the button before Chrome has fired
// beforeinstallprompt, wait briefly for the event.
//
// =========================================================

const KALXA_INSTALL_PROMPT_WAIT_MS =
    5000;


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
// IOS CHECK
// =========================================================

function isIOSDevice() {

    const userAgent =
        navigator.userAgent || "";


    return /iPhone|iPad|iPod/i.test(
        userAgent
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
// NORMAL INSTALL BUTTON
// =========================================================

function showInstallButton() {

    if (!lacInstallButton) {
        return;
    }


    lacInstallButton.style.display =
        "inline-flex";

    lacInstallButton.disabled =
        false;

    lacInstallButton.removeAttribute(
        "aria-busy"
    );


    // Preserve the original label so we can restore it
    // after the temporary loading state.

    if (
        !lacInstallButton.dataset
            .kalxaOriginalText
    ) {

        lacInstallButton.dataset
            .kalxaOriginalText =
            lacInstallButton.textContent.trim();

    }


    lacInstallButton.textContent =
        lacInstallButton.dataset
            .kalxaOriginalText;

}


// =========================================================
// INSTALL BUTTON BUSY STATE
// =========================================================

function showInstallPreparingState() {

    if (!lacInstallButton) {
        return;
    }


    if (
        !lacInstallButton.dataset
            .kalxaOriginalText
    ) {

        lacInstallButton.dataset
            .kalxaOriginalText =
            lacInstallButton.textContent.trim();

    }


    installButtonBusy =
        true;


    lacInstallButton.style.display =
        "inline-flex";

    lacInstallButton.disabled =
        true;

    lacInstallButton.setAttribute(
        "aria-busy",
        "true"
    );

    lacInstallButton.textContent =
        "Preparing install…";

}


// =========================================================
// RESTORE BUTTON
// =========================================================

function restoreInstallButton() {

    installButtonBusy =
        false;


    showInstallButton();

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
// PREPARING HELP TEXT
// =========================================================

function showPreparingHelp() {

    if (!lacInstallHelp) {
        return;
    }


    lacInstallHelp.textContent =
        "Preparing Kalxa for installation…";

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


    // -----------------------------------------------------
    // IOS / IPADOS
    // -----------------------------------------------------

    if (
        isIOSDevice()
    ) {

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
        "If the install window does not appear, open your " +
        "browser menu (⋮), then choose " +
        "<strong>Install app</strong> or " +
        "<strong>Add to Home screen</strong>.";

    lacInstallHelp.style.display =
        "block";

}


// =========================================================
// SYNC EARLY INSTALL PROMPT
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
// WAIT FOR NATIVE INSTALL PROMPT
// =========================================================
//
// This fixes the important first-visit case:
//
// User opens Kalxa
//      ↓
// Chrome is still checking PWA installability
//      ↓
// User taps Add to Home Screen
//      ↓
// Instead of immediately telling them to use ⋮,
// wait briefly for beforeinstallprompt.
//
// =========================================================

function waitForNativeInstallPrompt(
    timeoutMs =
        KALXA_INSTALL_PROMPT_WAIT_MS
) {

    syncEarlyInstallPrompt();


    // -----------------------------------------------------
    // ALREADY AVAILABLE
    // -----------------------------------------------------

    if (
        deferredInstallPrompt
    ) {

        return Promise.resolve(
            deferredInstallPrompt
        );

    }


    // -----------------------------------------------------
    // WAIT FOR CHROME
    // -----------------------------------------------------

    return new Promise(
        function(resolve) {

            let finished =
                false;


            function finish(
                promptEvent
            ) {

                if (
                    finished
                ) {
                    return;
                }


                finished =
                    true;


                window.removeEventListener(
                    "beforeinstallprompt",
                    temporaryPromptListener
                );


                window.removeEventListener(
                    "kalxainstallpromptready",
                    temporaryEarlyListener
                );


                resolve(
                    promptEvent || null
                );

            }


            function temporaryPromptListener(
                event
            ) {

                event.preventDefault();


                deferredInstallPrompt =
                    event;


                window.kalxaInstallPrompt =
                    event;


                clearOldInstallMarker();


                console.log(
                    "[Kalxa PWA] Install prompt became ready while waiting."
                );


                finish(
                    event
                );

            }


            function temporaryEarlyListener() {

                syncEarlyInstallPrompt();


                if (
                    deferredInstallPrompt
                ) {

                    console.log(
                        "[Kalxa PWA] Early install prompt became ready while waiting."
                    );


                    finish(
                        deferredInstallPrompt
                    );

                }

            }


            window.addEventListener(
                "beforeinstallprompt",
                temporaryPromptListener
            );


            window.addEventListener(
                "kalxainstallpromptready",
                temporaryEarlyListener
            );


            // -------------------------------------------------
            // CHECK AGAIN AFTER LISTENERS ARE ATTACHED
            // -------------------------------------------------

            syncEarlyInstallPrompt();


            if (
                deferredInstallPrompt
            ) {

                finish(
                    deferredInstallPrompt
                );

                return;

            }


            // -------------------------------------------------
            // TIMEOUT
            // -------------------------------------------------

            window.setTimeout(
                function() {

                    syncEarlyInstallPrompt();


                    finish(
                        deferredInstallPrompt
                    );

                },
                timeoutMs
            );

        }
    );

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


    if (
        !installButtonBusy
    ) {

        showInstallButton();

    }


    if (
        lacInstalledMessage
    ) {

        lacInstalledMessage.style.display =
            "none";

    }


    if (
        !installButtonBusy
    ) {

        showDefaultHelp();

    }

}


// =========================================================
// CLOSE CARD
// =========================================================

if (
    lacInstallClose
) {

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


                        // -------------------------------------
                        // Wait until the browser has finished
                        // establishing service-worker readiness.
                        //
                        // This does not manufacture an install
                        // prompt. It simply gives Chrome the
                        // opportunity to finish its PWA setup.
                        // -------------------------------------

                        return navigator
                            .serviceWorker
                            .ready;

                    }
                )
                .then(
                    function(registration) {

                        if (
                            registration
                        ) {

                            console.log(
                                "[Kalxa PWA] Service worker ready."
                            );

                        }


                        syncEarlyInstallPrompt();


                        updateInstallUI();

                    }
                )
                .catch(
                    function(error) {

                        console.error(
                            "[Kalxa PWA] Service worker registration failed:",
                            error
                        );


                        updateInstallUI();

                    }
                );

        }
    );

}


// =========================================================
// RECEIVE EARLY INSTALL PROMPT
// =========================================================
//
// access.html <head> may dispatch this event after it
// captures beforeinstallprompt.
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
// NATIVE INSTALL PROMPT CAPTURE
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
// OPEN NATIVE INSTALL PROMPT
// =========================================================

async function openNativeInstallPrompt() {

    syncEarlyInstallPrompt();


    if (
        !deferredInstallPrompt
    ) {

        return false;

    }


    const promptToUse =
        deferredInstallPrompt;


    try {

        console.log(
            "[Kalxa PWA] Opening native install prompt."
        );


        await promptToUse.prompt();


        const result =
            await promptToUse.userChoice;


        console.log(
            "[Kalxa PWA] Install choice:",
            result.outcome
        );


        // -------------------------------------------------
        // PROMPT CAN ONLY BE USED ONCE
        // -------------------------------------------------

        deferredInstallPrompt =
            null;


        window.kalxaInstallPrompt =
            null;


        // -------------------------------------------------
        // ACCEPTED
        // -------------------------------------------------

        if (
            result.outcome ===
            "accepted"
        ) {

            rememberKalxaInstalled();


            hideInstallSection();


            console.log(
                "[Kalxa PWA] Installation accepted."
            );


            return true;

        }


        // -------------------------------------------------
        // USER CANCELLED
        // -------------------------------------------------

        console.log(
            "[Kalxa PWA] Installation dismissed."
        );


        return false;


    } catch (error) {

        console.error(
            "[Kalxa PWA] Install error:",
            error
        );


        deferredInstallPrompt =
            null;


        window.kalxaInstallPrompt =
            null;


        return false;

    }

}


// =========================================================
// INSTALL BUTTON
// =========================================================

if (
    lacInstallButton
) {

    // Save the original label immediately.

    lacInstallButton.dataset
        .kalxaOriginalText =
        lacInstallButton.textContent.trim();


    lacInstallButton.addEventListener(
        "click",
        async function(event) {

            event.preventDefault();

            event.stopPropagation();


            // -------------------------------------------------
            // PREVENT DOUBLE TAPS
            // -------------------------------------------------

            if (
                installButtonBusy
            ) {

                return;

            }


            // -------------------------------------------------
            // ALREADY INSTALLED
            // -------------------------------------------------

            if (
                isRunningStandalone()
            ) {

                rememberKalxaInstalled();

                hideInstallSection();

                return;

            }


            // -------------------------------------------------
            // CHECK EARLY CAPTURE
            // -------------------------------------------------

            syncEarlyInstallPrompt();


            // -------------------------------------------------
            // NATIVE PROMPT ALREADY AVAILABLE
            // -------------------------------------------------

            if (
                deferredInstallPrompt
            ) {

                installButtonBusy =
                    true;


                lacInstallButton.disabled =
                    true;


                const installed =
                    await openNativeInstallPrompt();


                installButtonBusy =
                    false;


                if (
                    !installed
                    &&
                    !isRunningStandalone()
                ) {

                    restoreInstallButton();

                    showDefaultHelp();

                    updateInstallUI();

                }


                return;

            }


            // -------------------------------------------------
            // IOS
            // -------------------------------------------------
            //
            // iOS does not use beforeinstallprompt in the same
            // way, so there is no reason to wait.
            //
            // -------------------------------------------------

            if (
                isIOSDevice()
            ) {

                showManualInstallHelp();

                return;

            }


            // -------------------------------------------------
            // FIRST-VISIT CHROME / ANDROID CASE
            // -------------------------------------------------
            //
            // Do NOT immediately show the three-dot message.
            //
            // Chrome may still be finishing the service worker
            // and installability checks.
            //
            // -------------------------------------------------

            console.log(
                "[Kalxa PWA] Waiting for native install prompt."
            );


            showInstallPreparingState();

            showPreparingHelp();


            // -------------------------------------------------
            // WAIT FOR SERVICE WORKER READINESS
            // -------------------------------------------------

            if (
                "serviceWorker" in navigator
            ) {

                try {

                    await Promise.race(
                        [

                            navigator
                                .serviceWorker
                                .ready,

                            new Promise(
                                function(resolve) {

                                    window.setTimeout(
                                        resolve,
                                        2500
                                    );

                                }
                            )

                        ]
                    );

                } catch (error) {

                    console.warn(
                        "[Kalxa PWA] Service worker readiness check failed:",
                        error
                    );

                }

            }


            // -------------------------------------------------
            // CHROME MAY HAVE FIRED THE EVENT WHILE WE WAITED
            // -------------------------------------------------

            syncEarlyInstallPrompt();


            // -------------------------------------------------
            // IF STILL MISSING, WAIT FOR EVENT
            // -------------------------------------------------

            if (
                !deferredInstallPrompt
            ) {

                await waitForNativeInstallPrompt(
                    KALXA_INSTALL_PROMPT_WAIT_MS
                );

            }


            // -------------------------------------------------
            // RESTORE BUTTON BEFORE OPENING PROMPT
            // -------------------------------------------------

            installButtonBusy =
                false;


            restoreInstallButton();


            syncEarlyInstallPrompt();


            // -------------------------------------------------
            // NATIVE PROMPT NOW AVAILABLE
            // -------------------------------------------------

            if (
                deferredInstallPrompt
            ) {

                console.log(
                    "[Kalxa PWA] Native prompt ready after first-load wait."
                );


                installButtonBusy =
                    true;


                lacInstallButton.disabled =
                    true;


                const installed =
                    await openNativeInstallPrompt();


                installButtonBusy =
                    false;


                if (
                    !installed
                    &&
                    !isRunningStandalone()
                ) {

                    restoreInstallButton();

                    showDefaultHelp();

                    updateInstallUI();

                }


                return;

            }


            // -------------------------------------------------
            // GENUINELY UNAVAILABLE
            // -------------------------------------------------
            //
            // Only now do we show manual browser instructions.
            //
            // -------------------------------------------------

            console.log(
                "[Kalxa PWA] Native install prompt unavailable after waiting."
            );


            restoreInstallButton();


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


        installButtonBusy =
            false;


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


                installButtonBusy =
                    false;


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
// HTML should initially contain:
//
// <section
//     id="lac-install-section"
//     class="lac-install-section"
//     hidden
// >
//
// This prevents the install card flashing while JavaScript
// initializes.
//
// =========================================================

hideInstallSection();


// =========================================================
// INITIAL EARLY-PROMPT CHECK
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
