import { afterEach, describe, expect, it, vi } from "vitest";
import { http } from "../axios";
import { listPermissions, setPermission } from "./rbac";

afterEach(() => {
  vi.restoreAllMocks();
});

describe("rbac api", () => {
  it("asks for one subject's permissions", async () => {
    const get = vi.spyOn(http, "get").mockResolvedValue({ data: { permissions: [] } });
    const signal = new AbortController().signal;
    await listPermissions("tok", { type: "role", id: "r1" }, signal);
    expect(get).toHaveBeenCalledWith("/admin/rbac/permissions", {
      headers: { Authorization: "Bearer tok" },
      params: { subject_type: "role", subject_id: "r1" },
      signal,
    });
  });

  it("PUTs a permission cell", async () => {
    const put = vi.spyOn(http, "put").mockResolvedValue({ data: {} });
    const body = {
      subject_type: "user" as const,
      subject_id: "u1",
      resource_type: "agent" as const,
      resource_id: "audio",
      action: "use" as const,
      effect: "deny" as const,
      constraints: {},
    };
    await setPermission("tok", body);
    expect(put).toHaveBeenCalledWith("/admin/rbac/permissions", body, { headers: { Authorization: "Bearer tok" } });
  });
});
