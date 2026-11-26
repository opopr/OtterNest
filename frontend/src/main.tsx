import React from "react";
import ReactDOM from "react-dom/client";
import App from "./App";
import "./styles.css";

// Apply the saved theme before the first login or loading screen is rendered.
try { document.documentElement.dataset.uiTheme = localStorage.getItem("otternest-ui-theme") === "dark" ? "dark" : "light"; } catch { document.documentElement.dataset.uiTheme = "light"; }

ReactDOM.createRoot(document.getElementById("root")!).render(
  <React.StrictMode><App /></React.StrictMode>
);
