import { StrictMode } from "react";
import { createRoot } from "react-dom/client";

import "./styles.css";

function App() {
  return (
    <main className="shell">
      <p className="eyebrow">Phase 1 foundation</p>
      <h1>Intelligent LLM Gateway</h1>
      <p className="intro">
        The React and TypeScript application shell is ready for the gateway
        contract.
      </p>
    </main>
  );
}

createRoot(document.getElementById("root")!).render(
  <StrictMode>
    <App />
  </StrictMode>,
);
