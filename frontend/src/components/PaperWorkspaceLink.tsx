import { useState, type MouseEvent } from "react";

const paperUrl = "http://localhost:5174/?workspace=paper";

export function PaperWorkspaceLink() {
  const [blocked, setBlocked] = useState(false);

  const openPaper = (event: MouseEvent<HTMLAnchorElement>) => {
    if (event.button !== 0 || event.ctrlKey || event.metaKey || event.shiftKey || event.altKey) return;
    event.preventDefault();
    // Open the destination synchronously in the user gesture. Embedded browsers
    // can mishandle target=_blank navigation and leave an empty tab behind.
    const tab = window.open(paperUrl, "_blank");
    if (tab) {
      tab.opener = null;
      setBlocked(false);
    } else {
      setBlocked(true);
    }
  };

  return <span>
    <a href={paperUrl} target="_blank" rel="noopener noreferrer" onClick={openPaper}>Paper &amp; research</a>
    {blocked && <span role="status" className="mt-2 block text-xs text-amber-300">
      The browser did not confirm the new tab. <a className="underline" href={paperUrl}>Open paper here</a>, or open {paperUrl} in a browser tab.
    </span>}
  </span>;
}
