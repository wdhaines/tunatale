// Where TunaTale is live while THIS instance is parked (tunatale-qyw0). Set by
// the handler the root layout registers with api.ts, which fires when any API
// call answers 503 with a `parked_at`. Non-null = show only the parked page:
// this instance holds a copy that the next hand-back will overwrite.
function createParkedStore() {
  let at = $state<string | null>(null);
  return {
    get at() {
      return at;
    },
    set(url: string) {
      at = url;
    },
    clear() {
      at = null;
    },
  };
}

export const parkedStore = createParkedStore();
