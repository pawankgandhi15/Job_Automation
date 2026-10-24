import { StrictMode } from "react";
import { createRoot } from "react-dom/client";
import App from "./App.jsx";
import Review from "./Review.jsx";
import "./styles.css";

const isReview = location.pathname === "/review" || new URLSearchParams(location.search).get("view") === "review";
createRoot(document.getElementById("root")).render(<StrictMode>{isReview ? <Review /> : <App />}</StrictMode>);
