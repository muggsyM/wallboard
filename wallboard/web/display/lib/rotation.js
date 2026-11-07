// Page rotation state machine. Pure: no DOM, no clock — every time `t` is milliseconds
// passed in by the caller (the shell calls tick(Date.now()) once a second).
//
// tick(t) only reports that the current page has had its time; the caller then calls
// next(t), which moves on and restarts the page timer. A touch pauses rotation for
// pauseSeconds (or an explicit hold); after the pause the current page gets its full time again.

export class Rotator {
  constructor({ pages = [], defaultSeconds = 30, pauseSeconds = 120, now = 0 } = {}) {
    this.pages = pages.filter((p) => p && p.enabled !== false);
    this.defaultSeconds = defaultSeconds;
    this.pauseSeconds = pauseSeconds;
    this.index = 0;
    this._shownAt = now;
    this._pausedUntil = -Infinity;
    this._lastT = now;
  }

  current() {
    return this.pages[this.index] ?? null;
  }

  secondsFor(page) {
    return page && page.seconds > 0 ? page.seconds : this.defaultSeconds;
  }

  _show(index, t) {
    if (!this.pages.length) return null;
    const n = this.pages.length;
    this.index = ((index % n) + n) % n;
    this._shownAt = t;
    this._lastT = t;
    return this.current();
  }

  next(t = this._lastT) {
    return this._show(this.index + 1, t);
  }

  prev(t = this._lastT) {
    return this._show(this.index - 1, t);
  }

  // Jump to the first page of the given type; false if there is none.
  goTo(type, t = this._lastT) {
    const i = this.pages.findIndex((p) => p.type === type);
    if (i < 0) return false;
    this._show(i, t);
    return true;
  }

  // Pause for pauseSeconds, or for holdMs when given (a pending tile action); a running
  // longer pause is never shortened.
  touch(t, holdMs = this.pauseSeconds * 1000) {
    this._lastT = t;
    this._pausedUntil = Math.max(this._pausedUntil, t + holdMs);
  }

  tick(t) {
    this._lastT = t;
    if (this.pages.length < 2 || t < this._pausedUntil) return false;
    const since = Math.max(this._shownAt, this._pausedUntil);
    return t - since >= this.secondsFor(this.current()) * 1000;
  }
}
