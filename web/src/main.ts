import "./styles.css";
import { startApp } from "./app";

const app = startApp(document, document.getElementById("map")!, document.getElementById("panel")!, document.getElementById("sheet-handle")!);
(window as unknown as { backcountry: typeof app }).backcountry = app;  // for debugging a route by hand from the console
