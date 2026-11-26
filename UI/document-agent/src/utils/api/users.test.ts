import { afterEach, describe, expect, it, vi } from "vitest";
import { http } from "../axios";
import { listUsers } from "./users";

afterEach(() => {
  vi.restoreAllMocks();
});

describe("listUsers", () => {
  it("sends the query (free text included) in a POST body, never in the URL", async () => {
    const post = vi.spyOn(http, "post").mockResolvedValue({ data: {} });
    const get = vi.spyOn(http, "get");
    const signal = new AbortController().signal;
    await listUsers("tok", { q: "ana@example.com", status: "pending", limit: 10 }, signal);
    expect(get).not.toHaveBeenCalled();
    expect(post).toHaveBeenCalledWith(
      "/admin/users/search",
      { q: "ana@example.com", status: "pending", limit: 10 },
      { headers: { Authorization: "Bearer tok" }, signal },
    );
  });
});
