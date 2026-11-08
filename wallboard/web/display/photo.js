// Photos for the display: the shared nextPhoto() sequence used by Today's framed photo and
// the Photos slideshow page. (The board background is the static chalkboard, display.css.)
//
//   photo.nextPhoto()  -> PhotoItem | null (user order, or shuffle without repeats)
//   photo.sync(state)  -> take the photo list and photos_shuffle from store state
import { PhotoCycle } from "./lib/photo-cycle.js";

export function createPhoto() {
  const cycle = new PhotoCycle();
  return {
    nextPhoto: () => cycle.next(),
    sync(state) {
      cycle.setItems(state.photos, Boolean(state.config?.photos_shuffle));
    },
  };
}
