import { describe, expect, it } from "vitest";
import { ago, bytes, label, secs } from "./format";

describe("format", () => {
  it("formats seconds", () => {
    expect(secs(null)).toBe("—");
    expect(secs(4.173, 2)).toBe("4.17s");
    expect(secs(300, 0)).toBe("5m 0s");
  });
  it("formats relative time", () => {
    const now = Date.parse("2026-01-01T12:00:00Z");
    expect(ago("2026-01-01T11:59:58Z", now)).toBe("just now");
    expect(ago("2026-01-01T11:58:00Z", now)).toBe("2m ago");
    expect(ago(null)).toBe("—");
  });
  it("formats bytes and labels", () => {
    expect(bytes(512)).toBe("512 B");
    expect(bytes(2048)).toBe("2.0 KB");
    expect(label("cloud_outage")).toBe("cloud outage");
  });
});
