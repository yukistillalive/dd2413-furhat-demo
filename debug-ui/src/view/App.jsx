import React from 'react';
import { usePresenter } from './usePresenter.js';
import { Header } from './Header.jsx';
import { VideoPanel } from './VideoPanel.jsx';
import { ConversationLog } from './ConversationLog.jsx';

/** VIEW: passive. Renders the view model and forwards user actions to the presenter. */
export default function App({ presenter }) {
  const vm = usePresenter(presenter);
  return (
    <div className="app">
      <Header url={vm.url} connection={vm.connection} robotState={vm.robotState}
              onConnect={(url) => presenter.connect(url)} />
      <main>
        <VideoPanel video={vm.video} />
        <ConversationLog items={vm.items} showEvents={vm.showEvents}
                         onToggleEvents={(v) => presenter.setShowEvents(v)}
                         onClear={() => presenter.clear()} />
      </main>
    </div>
  );
}
