// Decides which replies on a chat page are new usage, as opposed to history (pure logic,
// unit-tested; no DOM access).
//
// Timing alone can't tell them apart: pages load history late, when scrolling up
// (Gemini's infinite scroller), or when switching chats in the sidebar. So a reply only
// counts if something shows it was produced while we watched:
//
// - it follows a user turn that appeared at the bottom of the transcript with no reply
//   after it (the user sent it in this page session), or
// - it was seen in progress: its text grew between observations, it was empty, or the
//   site flagged it as streaming.
//
// History fails both: it arrives complete, together with the question it answered.

export interface Turn<T> {
  el: T;
  kind: "user" | "reply";
  chars: number;
  streaming?: boolean;
}

export interface FinishedReply<T> {
  reply: Turn<T>;
  /** The user turn the reply answers, if one precedes it. */
  user?: Turn<T>;
}

interface ReplyState {
  lastChars: number;
  stable: number;
  live: boolean;
  done: boolean;
}

export class ReplyTracker<T extends object> {
  private readonly stablePolls: number;
  private readonly seenUsers = new WeakSet<T>();
  private readonly pendingUsers = new WeakSet<T>();
  private readonly replies = new WeakMap<T, ReplyState>();
  private started = false;

  /** A reply counts as finished once its length is unchanged for `stablePolls` observations. */
  constructor(stablePolls = 2) {
    this.stablePolls = stablePolls;
  }

  /** Observe the transcript (turns in document order). Returns replies that just finished and are new. */
  observe(turns: Turn<T>[]): FinishedReply<T>[] {
    if (!this.started) {
      // Everything on the page at start is history.
      for (const t of turns) {
        if (t.kind === "user") this.seenUsers.add(t.el);
        else this.replies.set(t.el, { lastChars: t.chars, stable: 0, live: false, done: true });
      }
      this.started = true;
      return [];
    }

    const finished: FinishedReply<T>[] = [];
    let lastUser: Turn<T> | undefined;
    turns.forEach((t, i) => {
      if (t.kind === "user") {
        if (!this.seenUsers.has(t.el)) {
          this.seenUsers.add(t.el);
          // Sent just now: it sits at the bottom, nothing answers it yet.
          if (!turns.slice(i + 1).some((x) => x.kind === "reply")) this.pendingUsers.add(t.el);
        }
        lastUser = t;
        return;
      }

      const state = this.replies.get(t.el);
      if (!state) {
        const answersPending = lastUser !== undefined && this.pendingUsers.has(lastUser.el);
        if (answersPending) this.pendingUsers.delete(lastUser!.el);
        this.replies.set(t.el, {
          lastChars: t.chars,
          stable: 0,
          live: answersPending || Boolean(t.streaming) || t.chars === 0,
          done: false,
        });
        return;
      }
      if (state.done) return;
      if (t.chars !== state.lastChars || t.streaming) {
        state.live = true;
        state.stable = 0;
        state.lastChars = t.chars;
        return;
      }
      state.stable += 1;
      if (state.stable < this.stablePolls || t.chars === 0) return;
      state.done = true;
      if (state.live) finished.push({ reply: t, user: lastUser });
    });
    return finished;
  }
}
