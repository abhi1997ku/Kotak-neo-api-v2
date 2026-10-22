// A full navigation between workspaces gives each backend its own auth/query state.
export const isResearchWorkspace = new URLSearchParams(window.location.search).get("workspace") === "paper"
  || window.location.port === "5174";
