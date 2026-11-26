import { afterEach, describe, expect, it, vi } from "vitest";
import { http } from "../axios";
import { listUsers, removeAdminDepartment } from "./users";

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

describe("removeAdminDepartment", () => {
  it("encodes the department in the path", async () => {
    const del = vi.spyOn(http, "delete").mockResolvedValue({ data: {} });
    await removeAdminDepartment("tok", "u1", "I+D Comercial");
    expect(del).toHaveBeenCalledWith("/admin/users/u1/admin-departments/I%2BD%20Comercial", {
      headers: { Authorization: "Bearer tok" },
    });
  });
});
