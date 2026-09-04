export interface WakeLockSentinelLike {
  release(): Promise<void>;
}

export interface WakeLockManagerLike {
  request(type: "screen"): Promise<WakeLockSentinelLike>;
}

export interface WakeLockNavigatorLike {
  wakeLock?: WakeLockManagerLike;
}

export interface WakeLockController {
  sync(enabled: boolean): Promise<void>;
  release(): Promise<void>;
}

export function createWakeLock(
  getNavigator: () => WakeLockNavigatorLike = () => navigator,
): WakeLockController {
  let sentinel: WakeLockSentinelLike | null = null;

  async function sync(enabled: boolean): Promise<void> {
    if (enabled) {
      await acquire();
    } else {
      await release();
    }
  }

  async function acquire(): Promise<void> {
    if (sentinel !== null) return;
    const manager = getNavigator().wakeLock;
    if (manager === undefined) return;
    try {
      sentinel = await manager.request("screen");
    } catch {
      // Document not visible (or unsupported): the request must not break
      // playback — capture just runs without the wake lock.
      sentinel = null;
    }
  }

  async function release(): Promise<void> {
    const current = sentinel;
    sentinel = null;
    if (current === null) return;
    try {
      await current.release();
    } catch {
      // A stale sentinel must never throw out of the player's effect.
    }
  }

  return { sync, release };
}
