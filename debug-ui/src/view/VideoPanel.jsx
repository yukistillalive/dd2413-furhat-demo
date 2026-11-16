import React from 'react';

export function VideoPanel({ video }) {
  return (
    <section className="video">
      <div className="frame">
        {video.src ? (
          <img src={video.src} alt="Robot camera" />
        ) : (
          <p className="muted">No camera frame yet.<br />Is the backend running without --no-vision?</p>
        )}
      </div>
      <dl className="stats">
        <dt>Frames</dt><dd>{video.count}</dd>
        <dt>Last frame</dt><dd>{video.ageLabel}</dd>
      </dl>
    </section>
  );
}
