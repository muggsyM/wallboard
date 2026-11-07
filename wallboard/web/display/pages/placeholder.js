// Placeholder page modules (Tasks 14–17 replace today/week/month/home with real pages).
// Shows the shared header and the page title on a panel; follows the page contract:
//   { type, titleKey, settings, mount(el, ctx) -> { update(state), destroy() } }
import { renderHeader } from "../header.js";

export function placeholderPage(type, titleKey, { todayHeader = false } = {}) {
  return {
    type,
    titleKey,
    settings: [],
    mount(el, ctx) {
      el.innerHTML = `<div class="wb-page-header"></div>
        <div class="wb-panel wb-placeholder"><span class="wb-placeholder-title"></span></div>`;
      const header = el.querySelector(".wb-page-header");
      const title = el.querySelector(".wb-placeholder-title");
      const update = (state) => {
        renderHeader(header, state, todayHeader ? {} : { titleKey });
        title.textContent = ctx.t(titleKey);
      };
      update(ctx.store.get());
      return {
        update,
        destroy() {
          el.innerHTML = "";
        },
      };
    },
  };
}
