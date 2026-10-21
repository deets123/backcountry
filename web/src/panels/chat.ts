// The chat box: messages, proposals the user can run or dismiss, and honest states (thinking, offline, errors).
import type { State } from "../store";
import type { ChatMessage } from "../types";
import { esc } from "./common";

function messageHtml(m: ChatMessage, i: number): string {
  if (m.role === "user") return `<div class="chat-msg chat-user">${esc(m.text)}</div>`;
  const parts = [m.text ? `<div>${esc(m.text)}</div>` : ""];
  if (m.withheld) parts.push(`<div class="note">(answer withheld: ${esc(m.withheld)})</div>`);
  if (m.note) parts.push(`<div class="note error">${esc(m.note)}</div>`);
  if (m.proposal) {
    const list = `<ul>${m.proposal.changes.map((c) => `<li>${esc(c)}</li>`).join("")}</ul>`;
    const foot = m.status === "ran" ? `<div class="note">Ran these settings.</div>`
      : m.status === "dismissed" ? `<div class="note">Dismissed.</div>`
      : `<div class="row"><button class="primary" data-run="${i}">Run</button><button class="link" data-dismiss="${i}">Dismiss</button></div>`;
    parts.push(`<div class="card proposal"><b>Proposed changes</b>${list}${foot}</div>`);
  }
  return `<div class="chat-msg chat-assistant">${parts.join("")}</div>`;
}

export function renderChat(s: State): string {
  if (!s.run) return "";
  const status = s.offline ? `<p class="note">Chat needs the server.</p>`
    : s.chatBusy ? `<p class="status">Thinking… (local model, can take ~20 s)</p>`
    : s.chatError ? `<p class="status error">${esc(s.chatError)}</p>` : "";
  const off = s.offline || s.chatBusy ? " disabled" : "";
  return `<section id="chat"><h2>Ask or change</h2>
<div id="chat-log">${s.chat.map(messageHtml).join("")}</div>
${status}
<div class="row"><input id="chat-input" name="chat" placeholder="e.g. make day 2 shorter, or: which day is hardest?" maxlength="1000"${off}><button class="primary" id="chat-send"${off}>Send</button></div>
<p class="note">Answers come only from this trip's data. Changes run only when you tap Run.</p></section>`;
}

export interface ChatActions { sendChat(text: string): void; runProposal(i: number): void; dismissProposal(i: number): void }

export function bindChat(root: HTMLElement, actions: ChatActions): void {
  const input = root.querySelector<HTMLInputElement>("#chat-input");
  const send = () => { if (input && input.value.trim()) actions.sendChat(input.value); };
  root.querySelector("#chat-send")?.addEventListener("click", send);
  input?.addEventListener("keydown", (e) => { if (e.key === "Enter") send(); });
  root.querySelectorAll<HTMLElement>("[data-run]").forEach((b) => b.addEventListener("click", () => actions.runProposal(Number(b.dataset.run))));
  root.querySelectorAll<HTMLElement>("[data-dismiss]").forEach((b) => b.addEventListener("click", () => actions.dismissProposal(Number(b.dataset.dismiss))));
}
