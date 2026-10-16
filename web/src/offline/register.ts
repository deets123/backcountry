// Registers the service worker (production builds only; see main.ts) and offers updates.
// A new version never takes over on its own: the app shows a banner and waits for a tap.
export function registerWorker(onUpdateReady: (apply: () => void) => void, sw: ServiceWorkerContainer | undefined = navigator.serviceWorker): void {
  if (!sw) return;
  sw.register("/sw.js").then((reg) => {
    const offer = (w: ServiceWorker | null) => {
      if (!w || !sw.controller) return; // first install: nothing to replace
      onUpdateReady(() => {
        sw.addEventListener("controllerchange", () => location.reload());
        w.postMessage("skipWaiting");
      });
    };
    offer(reg.waiting);
    reg.addEventListener("updatefound", () => {
      const w = reg.installing;
      w?.addEventListener("statechange", () => { if (w.state === "installed") offer(w); });
    });
  }).catch(() => { /* no service worker (e.g. plain http on a phone): the app still works online */ });
}
