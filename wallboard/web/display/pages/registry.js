// Page type -> page module. Adding a page = a new module file + one entry here.
// Imported by the display shell and (Task 18) by the settings page, so page modules must
// not touch the DOM at import time.
import today from "./today.js";
import week from "./week.js";
import month from "./month.js";
import home from "./home.js";
import photos from "./photos.js";

export const PAGES = { today, week, month, home, photos };

export function getPage(type) {
  return Object.hasOwn(PAGES, type) ? PAGES[type] : null;
}
