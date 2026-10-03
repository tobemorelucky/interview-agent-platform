/** Stateful SSE decoder. Network chunk boundaries have no event semantics. */
export class SSEParser {
  private buffer = "";
  private event = "message";
  private data: string[] = [];

  push(chunk: string): { event: string; data: unknown }[] {
    this.buffer += chunk;
    const result: { event: string; data: unknown }[] = [];
    let end: number;
    while ((end = this.buffer.indexOf("\n")) >= 0) {
      const line = this.buffer.slice(0, end).replace(/\r$/, "");
      this.buffer = this.buffer.slice(end + 1);
      if (line === "") {
        if (this.data.length) result.push({ event: this.event, data: JSON.parse(this.data.join("\n")) });
        this.data = [];
        this.event = "message";
      } else if (!line.startsWith(":")) {
        const colon = line.indexOf(":");
        const field = colon < 0 ? line : line.slice(0, colon);
        const value = colon < 0 ? "" : line.slice(colon + 1).replace(/^ /, "");
        if (field === "event") this.event = value;
        if (field === "data") this.data.push(value);
      }
    }
    return result;
  }

  finish(): void {
    if (this.buffer.trim() || this.data.length) throw new Error("SSE stream ended in an incomplete event");
  }
}
