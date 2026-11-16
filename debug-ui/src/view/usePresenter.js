import { useEffect, useSyncExternalStore } from 'react';

/** Binds a presenter to React: returns its current view model, re-renders on change. */
export function usePresenter(presenter) {
  useEffect(() => {
    presenter.start();
    return () => presenter.stop();
  }, [presenter]);
  return useSyncExternalStore(presenter.subscribe, presenter.getSnapshot);
}
