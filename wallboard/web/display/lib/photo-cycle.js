// Photo sequence for photo.nextPhoto(). Pure: no DOM.
// Ordered mode follows the user's order and wraps. Shuffle mode deals a random
// permutation and shows every photo once before reshuffling; a new round never starts
// with the photo that was just shown. Changing the list keeps the position: ordered
// mode continues after the last shown photo, shuffle mode keeps already-shown photos
// out of the rest of the current round (new photos join it, removed ones leave it).

export class PhotoCycle {
  constructor(random = Math.random) {
    this._random = random;
    this._items = [];
    this._shuffle = false;
    this._key = "";
    this._lastId = null;
    this._queue = []; // shuffle: ids still to show this round
    this._shown = new Set(); // shuffle: ids shown this round
  }

  setItems(items, shuffle) {
    const list = Array.isArray(items) ? items.filter((p) => p && p.id) : [];
    const ids = list.map((p) => p.id);
    const key = `${shuffle ? 1 : 0}|${ids.join(",")}`;
    if (key === this._key) return;
    this._key = key;
    this._items = list;
    if (Boolean(shuffle) !== this._shuffle) {
      this._shuffle = Boolean(shuffle);
      this._queue = [];
      this._shown = new Set();
    } else if (this._shuffle && this._queue.length) {
      const present = new Set(ids);
      const queued = this._queue.filter((id) => present.has(id));
      const added = ids.filter((id) => !this._queue.includes(id) && !this._shown.has(id));
      this._queue = this._permute([...queued, ...added]);
    }
  }

  next() {
    const n = this._items.length;
    if (!n) return null;
    let item;
    if (this._shuffle) {
      if (!this._queue.length) this._newRound();
      const id = this._queue.shift();
      this._shown.add(id);
      item = this._items.find((p) => p.id === id);
    } else {
      const last = this._items.findIndex((p) => p.id === this._lastId);
      item = this._items[(last + 1) % n]; // last === -1 -> first photo
    }
    this._lastId = item.id;
    return item;
  }

  _newRound() {
    const ids = this._permute(this._items.map((p) => p.id));
    if (ids.length > 1 && ids[0] === this._lastId) ids.push(ids.shift());
    this._queue = ids;
    this._shown = new Set();
  }

  // Fisher-Yates.
  _permute(ids) {
    const a = ids.slice();
    for (let i = a.length - 1; i > 0; i--) {
      const j = Math.floor(this._random() * (i + 1));
      [a[i], a[j]] = [a[j], a[i]];
    }
    return a;
  }
}
