// Auto-close tuning for the Current Spool card (see SpoolBuddyDashboard).
//
// When the roll is lifted off the scale while its card is showing, the card
// starts a visible, cancellable countdown and then closes itself — the user
// has clearly moved on to the next roll. Engineer-configurable here; if this
// ever needs to be user-configurable, the kiosk Settings page can layer a
// localStorage override on top (the same pattern as the default core weight).

/** Seconds the countdown runs before the card closes itself. */
export const AUTO_CLOSE_SECONDS = 3;

/** The scale must first read ABOVE this (a real roll is sitting on it) for
 *  the auto-close to arm — prevents firing when a card is opened while the
 *  scale is already empty. */
export const AUTO_CLOSE_ARM_THRESHOLD_G = 250;

/** Once armed, a reading BELOW this means the roll was lifted — the
 *  countdown starts. Rising back above it mid-countdown cancels. */
export const AUTO_CLOSE_EMPTY_THRESHOLD_G = 50;
