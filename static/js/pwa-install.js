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
// CHECK WHETHER WE PREVIOUSLY INSTALLED LaC
// =========================================================

function wasLaCInstalled() {

    return (
        localStorage.getItem(
            "lac_pwa_installed"
        ) === "true"
    );

}


// =========================================================
// CHECK WHETHER INSTALL CARD SHOULD DISAPPEAR
// =========================================================

function shouldHideInstallCard() {

    return (
        isRunningStandalone()
        ||
        wasLaCInstalled()
        ||
        installCardDismissed
    );

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
// UPDATE INSTALL UI
// =========================================================

function updateInstallUI() {

    // -----------------------------------------------------
    // INSTALLED OR DISMISSED
    //
    // Hide the ENTIRE Quick Access card.
    // -----------------------------------------------------

    if (
        shouldHideInstallCard()
    ) {

        hideInstallSection();

        return;

    }


    // -----------------------------------------------------
    // NOT INSTALLED
    //
    // Quick Access card may be displayed.
    // -----------------------------------------------------

    if (lacInstallSection) {

        lacInstallSection.hidden =
            false;

    }


    // We no longer need to show an
    // "installed" message because the entire
    // card disappears after installation.

    if (lacInstalledMessage) {

        lacInstalledMessage.style.display =
            "none";

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


        if (lacInstallHelp) {

            lacInstallHelp.style.display =
                "block";

        }


        return;

    }


    // -----------------------------------------------------
    // INSTALL PROMPT NOT AVAILABLE YET
    // -----------------------------------------------------

    if (lacInstallButton) {

        lacInstallButton.style.display =
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


            hideInstallSection();


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


            if (
                !deferredInstallPrompt
            ) {

                console.log(
                    "[LaC PWA] Install prompt not available."
                );

                return;

            }


            lacInstallButton.disabled =
                true;


            try {

                // Show the browser's
                // installation dialog.

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

                    // Remember the installation
                    // for normal browser visits.

                    localStorage.setItem(
                        "lac_pwa_installed",
                        "true"
                    );


                    console.log(
                        "[LaC PWA] User accepted installation."
                    );


                    // Hide Quick Access immediately.

                    hideInstallSection();

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


        // Remember installation even when
        // the user later visits LaC through
        // their normal browser.

        localStorage.setItem(
            "lac_pwa_installed",
            "true"
        );


        deferredInstallPrompt =
            null;


        // IMPORTANT:
        // Hide the ENTIRE Quick Access card.

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
        function() {

            updateInstallUI();

        }
    );

}


// =========================================================
// INITIAL PAGE LOAD
// =========================================================

updateInstallUI();
