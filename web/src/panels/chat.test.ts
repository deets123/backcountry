import { describe, expect, it } from "vitest";
import { INITIAL } from "../store";
import type { ChatMessage, Run } from "../types";
import { bindChat, renderChat } from "./chat";

const run = { run_id: "r1", candidates: [] } as unknown as Run;
const proposal = { request: { trailhead: "bear_rocks", total: [18, 26] as [number, number] }, changes: ["Miles per day: 6-11 mi → 6-9 mi"] };

describe("renderChat", () => {
  it("shows messages, a proposal card with Run and Dismiss, and notes", () => {
    const chat: ChatMessage[] = [
      { role: "user", text: "shorter days" },
      { role: "assistant", text: "This lowers the daily maximum.", proposal, status: "open" },
      { role: "assistant", text: "I can't answer that from this trip's data.", withheld: "unsupported number '7.8'" },
      { role: "assistant", text: "Adding that trail.", note: "There's no trail named 'X' in this region." }];
    const html = renderChat({ ...INITIAL, run, chat });
    expect(html).toContain("shorter days"); expect(html).toContain("This lowers the daily maximum.");
    expect(html).toContain("Miles per day: 6-11 mi → 6-9 mi"); expect(html).toContain('data-run="1"'); expect(html).toContain('data-dismiss="1"');
    expect(html).toContain("(answer withheld: unsupported number &#39;7.8&#39;)"); expect(html).toContain("There&#39;s no trail named &#39;X&#39; in this region.");
    expect(html).toContain('id="chat-input"');
  });
  it("marks proposals that were run or dismissed, without buttons", () => {
    const html = renderChat({ ...INITIAL, run, chat: [{ role: "assistant", text: "", proposal, status: "ran" }, { role: "assistant", text: "", proposal, status: "dismissed" }] });
    expect(html).toContain("Ran these settings"); expect(html).toContain("Dismissed"); expect(html).not.toContain("data-run=");
  });
  it("says when it is thinking, offline, or failed", () => {
    expect(renderChat({ ...INITIAL, run, chatBusy: true })).toContain("Thinking… (local model, can take ~20 s)");
    const off = renderChat({ ...INITIAL, run, offline: true });
    expect(off).toContain("Chat needs the server."); expect(off).toMatch(/id="chat-input"[^>]*disabled/);
    expect(renderChat({ ...INITIAL, run, chatError: "The chat model isn't reachable" })).toContain("The chat model isn&#39;t reachable");
    expect(renderChat({ ...INITIAL })).toBe("");
  });
  it("escapes everything the model or user wrote", () => {
    const html = renderChat({ ...INITIAL, run, chat: [{ role: "user", text: "<img src=x onerror=alert(1)>" }] });
    expect(html).not.toContain("<img"); expect(html).toContain("&lt;img");
  });
  it("shows the server's error and keeps the input usable", () => {
    const html = renderChat({ ...INITIAL, run, chatError: "The chat model isn't reachable (Ollama at 127.0.0.1:11434). Start Ollama, or plan with the form." });
    expect(html).toContain("Start Ollama"); expect(html).not.toMatch(/id="chat-input"[^>]*disabled/);
  });
});

describe("bindChat", () => {
  it("sends on the button and on Enter, and wires Run/Dismiss", () => {
    const root = document.createElement("div");
    root.innerHTML = renderChat({ ...INITIAL, run, chat: [{ role: "assistant", text: "", proposal, status: "open" }] });
    const calls: string[] = [];
    bindChat(root, { sendChat: (t) => calls.push(`send:${t}`), runProposal: (i) => calls.push(`run:${i}`), dismissProposal: (i) => calls.push(`dismiss:${i}`) });
    const input = root.querySelector<HTMLInputElement>("#chat-input")!;
    input.value = "3 nights"; root.querySelector<HTMLButtonElement>("#chat-send")!.click();
    input.value = "avoid roads"; input.dispatchEvent(new KeyboardEvent("keydown", { key: "Enter" }));
    root.querySelector<HTMLButtonElement>('[data-run="0"]')!.click();
    root.querySelector<HTMLButtonElement>('[data-dismiss="0"]')!.click();
    expect(calls).toEqual(["send:3 nights", "send:avoid roads", "run:0", "dismiss:0"]);
  });
});

describe("chat across runs", () => {
  it("a proposal from an earlier result can't be run and says why", () => {
    const html = renderChat({ ...INITIAL, run, chat: [{ role: "assistant", text: "", proposal, status: "open", runId: "older" }] });
    expect(html).toContain("From an earlier result; not runnable here."); expect(html).not.toContain("data-run=");
    expect(renderChat({ ...INITIAL, run, chat: [{ role: "assistant", text: "", proposal, status: "open", runId: "r1" }] })).toContain('data-run="0"');
  });
  it("says that camps moved on the map aren't what the chat sees", () => {
    expect(renderChat({ ...INITIAL, run, view: "trip", campsM: [1000, 2000] })).toContain("Chat uses the engine's camps, not the ones you moved.");
    expect(renderChat({ ...INITIAL, run, view: "trip", campsM: null })).not.toContain("not the ones you moved");
  });
});

describe("the chat log", () => {
  it("labels messages from an earlier result so they aren't mistaken for this trip's", () => {
    const chat: ChatMessage[] = [{ role: "assistant", text: "Zion answer", runId: "r2", proposal, status: "open" }, { role: "user", text: "this trip", runId: "r1" }];
    const html = renderChat({ ...INITIAL, run, chat });
    expect(html).toContain("this trip");
    expect(html).toContain("From an earlier result");
    expect(html).not.toContain('data-run="0"');
  });
});
